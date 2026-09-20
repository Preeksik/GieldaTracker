"""
HossaLab - wybór modelu Gemini i automatyczny fallback po wyczerpaniu limitów.

DLACZEGO TO MA SENS:
Na darmowym kluczu Google AI Studio KAŻDY model ma własny, osobny limit dobowy.
Gemini 3.6 Flash to 20 zapytań na dobę - ale 3.8, 3.7, 3.5 i 3 Flash mają po swoje
20, a modele Flash Lite po 500. Zejście na kolejny model po wyczerpaniu poprzedniego
zamienia 20 analiz dziennie w grubo ponad tysiąc, bez płacenia ani złotówki.

JAK TO DZIAŁA:
- Lista modeli NIE jest zaszyta w kodzie. Moduł pyta Google, jakie modele obsługuje
  Twój klucz, i sam układa je od najnowszego. Nowy Gemini pojawi się w aplikacji sam.
- Gdy model zwróci 429 (limit), trafia na "kwarantannę" i zapytanie leci do następnego.
  Kwarantanna jest zapisywana na dysk, więc restart backendu jej nie kasuje.
- Błąd, który powtórzy się na każdym modelu (zły prompt, filtr bezpieczeństwa, zły
  klucz) NIE uruchamia fallbacku - inaczej jedno złe zapytanie spaliłoby limit na
  wszystkich modelach naraz.
- Każde wywołanie jest zapisywane w dzienniku (ostatnie 25), żeby dało się sprawdzić
  KTÓRY model faktycznie odpowiedział i dlaczego nie ten, którego się spodziewałeś.

Podpięcie na końcu main.py:
    from ai_models import setup_ai_models, generate
    setup_ai_models(app)
"""

import os
import re
import json
import time
import logging
import tempfile
import threading
from datetime import datetime, timedelta, timezone

import requests
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("gpw-api")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "ai_models_state.json")

API_KEY = os.environ.get("GOOGLE_API_KEY", "")
API_ROOT = "https://generativelanguage.googleapis.com/v1beta"

# Model używany tylko awaryjnie, gdy nie udało się pobrać listy modeli z Google.
FALLBACK_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

# Bezpieczniki na jeden request: ile modeli maksymalnie spróbować i ile łącznie
# czekać. Bez nich awaria po stronie Google potrafiła zająć wątek na kilkanaście
# minut (11 modeli * timeout 90 s) i zakwarantannować cały katalog.
MAX_NETWORK_ATTEMPTS = 6
TOTAL_BUDGET_SECONDS = 150

CATALOG_TTL_SECONDS = 60 * 60      # lista modeli zmienia się rzadko
CATALOG_ERROR_TTL_SECONDS = 60     # po błędzie próbujemy ponownie już za minutę
RECENT_LOG_LIMIT = 25


# ============================================================================
# REDAKCJA KLUCZA
# ============================================================================

def redact(text):
    """
    Klucz API siedzi w query stringu URL-a, więc każdy wyjątek z requests niesie go
    w treści. Ta treść trafia potem do HTTPException.detail, czyli do odpowiedzi HTTP,
    do logów i na ekran. Wycinamy go, zanim gdziekolwiek pojedzie.
    """
    s = str(text)
    if API_KEY:
        s = s.replace(API_KEY, "***")
    return re.sub(r"(key=)[A-Za-z0-9_\-]+", r"\1***", s)


# ============================================================================
# STAN - jedno źródło prawdy w pamięci, chronione zamkiem
# ============================================================================
#
# Endpointy FastAPI są synchroniczne, więc lecą na puli wątków, a APScheduler
# dokłada zadania w tle (skan ESPI, poranny briefing), które też wołają Gemini.
# Wcześniejszy wzorzec "wczytaj plik -> zmodyfikuj kopię -> zapisz plik" gubił
# przy tym kwarantanny i potrafił po cichu cofnąć tryb wybrany przez użytkownika.

_state_lock = threading.RLock()
_state = None

_catalog_lock = threading.RLock()
_catalog_cache = {"models": None, "fetched_at": None, "error": None}


def _default_state():
    return {
        "mode": "mocny",        # mocny | oszczedny | reczny
        "manual_model": None,
        "cooldowns": {},        # model -> ISO timestamp, do kiedy pomijamy
        "usage": {},            # dzień (czas pacyficzny) -> {model: liczba sukcesów}
        "last_used": None,
        "last_fallback": None,
        "recent": [],           # dziennik ostatnich wywołań
    }


def _read_state_file():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = _default_state()
        if isinstance(data, dict):
            merged.update(data)
        return merged
    except FileNotFoundError:
        return _default_state()
    except json.JSONDecodeError:
        # Plik uszkodzony (np. Ctrl+C w trakcie zapisu). Odkładamy go na bok zamiast
        # kasować po cichu - inaczej użytkownik widzi tylko, że aplikacja "sama sobie
        # zmieniła tryb", bez śladu dlaczego.
        broken = STATE_FILE + ".broken"
        try:
            os.replace(STATE_FILE, broken)
            logger.error("Uszkodzony %s - przeniesiony do %s, startuję od domyślnych ustawień.",
                         STATE_FILE, broken)
        except Exception:
            logger.exception("Uszkodzony plik stanu modeli, startuję od domyślnych ustawień")
        return _default_state()
    except Exception:
        logger.exception("Nie udało się wczytać stanu modeli - startuję od domyślnych ustawień")
        return _default_state()


def _get_state():
    """Zwraca współdzielony słownik stanu. Wołać WYŁĄCZNIE pod `_state_lock`."""
    global _state
    if _state is None:
        _state = _read_state_file()
    return _state


def _persist():
    """
    Zapis atomowy: najpierw plik tymczasowy, potem os.replace. Zwykłe open(...,'w')
    najpierw obcina plik do zera, więc przerwany zapis zostawiał pusty/uszkodzony JSON.
    Wołać pod `_state_lock`.
    """
    state = _get_state()
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=BASE_DIR, prefix=".ai_models_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, STATE_FILE)
        tmp_path = None
    except Exception:
        logger.exception("Nie udało się zapisać stanu modeli")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def snapshot_state():
    """Kopia stanu do odczytu (dla endpointów), bez ryzyka że ktoś ją w locie zmieni."""
    with _state_lock:
        return json.loads(json.dumps(_get_state()))


# ============================================================================
# CZAS
# ============================================================================

def _now():
    return datetime.now(timezone.utc)


def _pacific():
    """
    Strefa pacyficzna - w niej Google resetuje darmowe limity dobowe.
    zoneinfo na Windows potrzebuje pakietu 'tzdata'; przy jego braku lecimy na
    stałym UTC-8, co w okresie letnim daje godzinę różnicy (bez wpływu na działanie).
    """
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Los_Angeles")
    except Exception:
        return timezone(timedelta(hours=-8))


def _next_quota_reset():
    local = _now().astimezone(_pacific())
    tomorrow = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.astimezone(timezone.utc)


def _quota_day():
    """
    Klucz dnia dla liczników zużycia - liczony w czasie PACYFICZNYM, bo to o północy
    pacyficznej resetują się limity. Liczenie w UTC przesuwało licznik o 7-8 godzin
    względem rzeczywistości: aplikacja pokazywała zero, a limit wciąż był wyczerpany.
    """
    return _now().astimezone(_pacific()).strftime("%Y-%m-%d")


def _parse_iso(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# ============================================================================
# KATALOG MODELI (pobierany z API, nie zaszyty w kodzie)
# ============================================================================

# Modele, które nie służą do analizy tekstu.
_EXCLUDE = re.compile(
    r"(image|imagen|banana|tts|audio|live|veo|lyria|embedding|aqa|transcribe|translate|robotics|vision)",
    re.IGNORECASE,
)

# Aliasy typu "gemini-flash-latest" wskazują na któryś z numerowanych modeli i dzielą
# z nim ten sam limit. Trzymanie obu w kolejce oznaczałoby próbowanie DWA RAZY tego
# samego wiadra limitu - dlatego alias jest wybieralny ręcznie, ale nie wchodzi do
# automatycznego łańcucha.
_ALIAS = re.compile(r"-latest$", re.IGNORECASE)

_VERSION_RE = re.compile(r"gemini-(\d+)(?:\.(\d+))?", re.IGNORECASE)
_SNAPSHOT_RE = re.compile(r"-(\d{2})-(\d{4})$")        # ...-preview-09-2026
_SNAPSHOT_NUM_RE = re.compile(r"-(\d{3})$")            # ...-001

VARIANT_LABEL = {"pro": "Pro", "flash": "Flash", "flash-lite": "Flash Lite",
                 "gemma": "Gemma", "inny": "-"}


def _version_tuple(name):
    """
    Wersja jako KROTKA liczb, nie float.

    float("3.10") daje 3.1, czyli mniej niż 3.8 - przez co Gemini 3.10 lądowałby
    w kolejce ZA 3.8. Porównanie krotek (3, 10) > (3, 8) działa poprawnie.
    """
    m = _VERSION_RE.search(name)
    if not m:
        return (0, 0)
    major = int(m.group(1))
    minor = int(m.group(2)) if m.group(2) else 0
    return (major, minor)


def _snapshot_rank(name):
    """Nowszy snapshot preview ma iść przed starszym: '-preview-11-2026' przed '-09-2026'."""
    m = _SNAPSHOT_RE.search(name)
    if m:
        return int(m.group(2)) * 100 + int(m.group(1))
    m = _SNAPSHOT_NUM_RE.search(name)
    if m:
        return int(m.group(1))
    return 0


def _parse_model_id(raw_id):
    name = raw_id.split("/")[-1]
    lower = name.lower()

    if "flash-lite" in lower or "flash_lite" in lower:
        variant = "flash-lite"
    elif "flash" in lower:
        variant = "flash"
    elif "pro" in lower:
        variant = "pro"
    elif "gemma" in lower:
        variant = "gemma"
    else:
        variant = "inny"

    return {
        "id": name,
        "version": _version_tuple(name),
        "variant": variant,
        "variant_label": VARIANT_LABEL.get(variant, "-"),
        "is_preview": ("preview" in lower or "-exp" in lower or "experimental" in lower),
        "is_alias": bool(_ALIAS.search(name)) or _version_tuple(name) == (0, 0),
        "snapshot": _snapshot_rank(name),
    }


def fetch_catalog(force=False):
    """Pobiera z Google listę modeli obsługujących generateContent."""
    with _catalog_lock:
        fetched = _catalog_cache["fetched_at"]
        if not force and fetched is not None:
            age = (_now() - fetched).total_seconds()
            ttl = CATALOG_ERROR_TTL_SECONDS if _catalog_cache["error"] else CATALOG_TTL_SECONDS
            if age < ttl and _catalog_cache["models"]:
                return _catalog_cache["models"], _catalog_cache["error"]

    models, error = [], None
    try:
        resp = requests.get(f"{API_ROOT}/models?key={API_KEY}", timeout=20)
        if resp.status_code != 200:
            error = f"Google zwróciło {resp.status_code}: {redact(resp.text[:200])}"
        else:
            for m in resp.json().get("models", []):
                if "generateContent" not in m.get("supportedGenerationMethods", []):
                    continue
                info = _parse_model_id(m.get("name", ""))
                if _EXCLUDE.search(info["id"]):
                    continue
                info["label"] = m.get("displayName") or info["id"]
                info["input_limit"] = m.get("inputTokenLimit")
                models.append(info)
    except requests.exceptions.RequestException as e:
        error = f"Brak połączenia z Google: {redact(e)}"
    except Exception as e:
        logger.exception("Nie udało się pobrać katalogu modeli")
        error = redact(e)

    with _catalog_lock:
        if error and not models:
            # Nie zatruwamy cache'u pustą listą - lepiej podać lekko nieświeżą listę
            # niż zostawić aplikację bez żadnego fallbacku na godzinę.
            _catalog_cache["error"] = error
            _catalog_cache["fetched_at"] = _now()
            if _catalog_cache["models"]:
                return _catalog_cache["models"], error
            _catalog_cache["models"] = []
            return [], error

        _catalog_cache.update(models=models, fetched_at=_now(), error=error)
        return models, error


def _neg(version):
    return tuple(-v for v in version)


def _rank_quality(m):
    """Flash od najnowszego, wersje stabilne przed preview, potem Pro, potem Lite."""
    order = {"flash": 0, "pro": 1, "flash-lite": 2, "gemma": 3, "inny": 4}
    return (order.get(m["variant"], 9), _neg(m["version"]), m["is_preview"],
            -m["snapshot"], m["id"])


def _rank_economy(m):
    """Flash Lite (500 zapytań na dobę) przed Flash (20 na dobę)."""
    order = {"flash-lite": 0, "flash": 1, "pro": 2, "gemma": 3, "inny": 4}
    return (order.get(m["variant"], 9), _neg(m["version"]), m["is_preview"],
            -m["snapshot"], m["id"])


def build_chain(state, models, prefer=None):
    """Kolejka modeli do wypróbowania. Wybrany ręcznie idzie na czoło."""
    ranker = _rank_economy if state.get("mode") == "oszczedny" else _rank_quality
    # Aliasy (-latest) pomijamy w automacie: dzielą limit z modelem numerowanym.
    auto = [m for m in models if not m["is_alias"]]
    chain = [m["id"] for m in sorted(auto, key=ranker)]

    head = prefer or (state.get("manual_model") if state.get("mode") == "reczny" else None)
    if head:
        chain = [head] + [m for m in chain if m != head]

    return chain or [FALLBACK_MODEL]


# ============================================================================
# KWARANTANNA
# ============================================================================

def _cooldown_left(state, model):
    until = _parse_iso(state.get("cooldowns", {}).get(model))
    return max(0, int((until - _now()).total_seconds())) if until else 0


def _set_cooldown(state, model, seconds, reason):
    """Nigdy nie skracamy już nałożonej kwarantanny - tylko wydłużamy."""
    new_until = _now() + timedelta(seconds=max(5.0, float(seconds)))
    current = _parse_iso(state.get("cooldowns", {}).get(model))
    if current and current > new_until:
        return
    state.setdefault("cooldowns", {})[model] = new_until.isoformat()
    logger.warning("Model %s na kwarantannie do %s (%s)",
                   model, new_until.astimezone().strftime("%d.%m %H:%M"), redact(reason))


def _prune_cooldowns(state):
    now = _now()
    state["cooldowns"] = {
        m: ts for m, ts in state.get("cooldowns", {}).items()
        if (_parse_iso(ts) or now) > now
    }


def _retry_delay_from_error(data):
    try:
        for detail in data.get("error", {}).get("details", []):
            delay = detail.get("retryDelay")
            if isinstance(delay, str) and delay.endswith("s"):
                value = float(delay[:-1])
                if value > 0:
                    return value
    except Exception:
        pass
    return None


def _is_daily_quota(data, message):
    """
    Limit MINUTOWY mija po chwili, DOBOWY dopiero o północy - to zupełnie różne
    kwarantanny. Google podaje identyfikator metryki w error.details[].violations[],
    np. 'GenerateRequestsPerDayPerProjectPerModel'; komunikat tekstowy bywa różny,
    więc sprawdzamy oba miejsca.
    """
    try:
        for detail in data.get("error", {}).get("details", []):
            for violation in detail.get("violations", []) or []:
                blob = " ".join(str(v) for v in violation.values()).lower()
                if "perday" in blob.replace(" ", "") or "per day" in blob:
                    return True
                if "perminute" in blob.replace(" ", "") or "per minute" in blob:
                    return False
    except Exception:
        pass
    text = (message or "").lower()
    return ("per day" in text) or ("perday" in text) or ("daily" in text)


def _note_usage(state, model):
    day = _quota_day()
    usage = state.setdefault("usage", {})
    usage.setdefault(day, {})
    usage[day][model] = usage[day].get(model, 0) + 1
    cutoff = (_now().astimezone(_pacific()) - timedelta(days=7)).strftime("%Y-%m-%d")
    for old in [d for d in list(usage) if d < cutoff]:
        usage.pop(old, None)
    state["last_used"] = {"model": model, "at": _now().isoformat()}


def _log_call(state, entry):
    recent = state.setdefault("recent", [])
    recent.insert(0, entry)
    del recent[RECENT_LOG_LIMIT:]


# ============================================================================
# WYWOŁANIE
# ============================================================================

class GeminiError(Exception):
    """Błąd, którego nie naprawi zmiana modelu (zły prompt, filtr, zły klucz)."""
    def __init__(self, message, status=502):
        super().__init__(message)
        self.message = redact(message)
        self.status = status


class AllModelsExhausted(Exception):
    """Wszystkie modele w kolejce odpadły."""
    def __init__(self, message, attempts):
        super().__init__(message)
        self.message = message
        self.attempts = attempts


def _try_model(model, prompt, timeout):
    """(tekst, None) przy sukcesie albo (None, {"message", "status", "cooldown"})."""
    url = f"{API_ROOT}/models/{model}:generateContent?key={API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
    except requests.exceptions.Timeout:
        return None, {"message": "Przekroczony czas odpowiedzi.", "status": 504, "cooldown": 120}
    except requests.exceptions.RequestException as e:
        # Sieć potrafi paść przejściowo - to nie powód, żeby zabić całą analizę,
        # ale też nie powód, żeby objechać cały katalog. Krótka kwarantanna i dalej.
        return None, {"message": f"Błąd sieci: {redact(e)}", "status": 0, "cooldown": 60}

    try:
        data = resp.json()
    except ValueError:
        return None, {"message": "Odpowiedź nie była JSON-em.", "status": resp.status_code, "cooldown": 120}

    if resp.status_code == 200:
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"], None
        except (KeyError, IndexError):
            reason = ""
            try:
                reason = data["candidates"][0].get("finishReason", "")
            except (KeyError, IndexError):
                pass
            raise GeminiError(
                f"Gemini nie zwróciło treści (możliwy filtr bezpieczeństwa{', powód: ' + reason if reason else ''}).",
                502,
            )

    message = data.get("error", {}).get("message", f"Błąd {resp.status_code}")

    if resp.status_code == 429:
        if _is_daily_quota(data, message):
            # Limit dobowy: Google i tak podaje w RetryInfo kilkadziesiąt sekund,
            # ale wracanie do modelu co minutę do północy to czyste marnowanie
            # zapytań. Kwarantanna idzie do najbliższego resetu.
            cooldown = max(300, (_next_quota_reset() - _now()).total_seconds())
            kind = "limit dobowy"
        else:
            cooldown = (_retry_delay_from_error(data) or 60) + 5
            kind = "limit minutowy"
        return None, {"message": f"{kind}: {message}", "status": 429, "cooldown": cooldown}

    if resp.status_code == 404:
        return None, {"message": f"Model niedostępny dla tego klucza: {message}",
                      "status": 404, "cooldown": 6 * 3600}

    if resp.status_code in (500, 502, 503):
        return None, {"message": message, "status": resp.status_code, "cooldown": 300}

    # 400 / 401 / 403 - zły klucz albo zły prompt. Każdy model odbije się tak samo.
    raise GeminiError(message, 502)


def generate(prompt, timeout=60, prefer=None):
    """
    Wysyła prompt do pierwszego dostępnego modelu z kolejki.
    Podnosi GeminiError (błąd nie do naprawienia) albo AllModelsExhausted.
    """
    if not API_KEY:
        raise GeminiError("Brak GOOGLE_API_KEY w backend/.env.", 500)

    models, catalog_error = fetch_catalog()
    started = time.monotonic()
    attempts = []
    network_calls = 0
    result = None
    used_model = None
    fatal = None

    # Zamek trzymamy TYLKO na krótkich odczytach i zapisach stanu, nigdy na czas
    # zapytania sieciowego. Inaczej analiza z UI czekałaby w kolejce za zadaniem
    # w tle nawet dwie minuty. Kwarantannę zapisujemy natychmiast po każdej
    # porażce, więc równoległy wątek od razu ją widzi i nie puka drugi raz.
    with _state_lock:
        state = _get_state()
        _prune_cooldowns(state)
        chain = build_chain(state, models, prefer=prefer)
    wanted = chain[0] if chain else None

    try:
        for model in chain:
            if network_calls >= MAX_NETWORK_ATTEMPTS:
                attempts.append({"model": model, "skipped": True, "reason": "limit prób w jednym żądaniu"})
                break
            if time.monotonic() - started > TOTAL_BUDGET_SECONDS:
                attempts.append({"model": model, "skipped": True, "reason": "przekroczony budżet czasu"})
                break

            with _state_lock:
                left = _cooldown_left(_get_state(), model)
            if left > 0:
                attempts.append({"model": model, "skipped": True,
                                 "reason": f"kwarantanna jeszcze {left // 60 + 1} min"})
                continue

            network_calls += 1
            text, err = _try_model(model, prompt, timeout)

            if err is None:
                result, used_model = text, model
                with _state_lock:
                    state = _get_state()
                    _note_usage(state, model)
                    if any(not a.get("skipped") for a in attempts):
                        state["last_fallback"] = {"at": _now().isoformat(), "wanted": wanted,
                                                  "used": model, "attempts": attempts}
                        logger.info("Fallback modelu: %s -> %s", wanted, model)
                    _persist()
                break

            attempts.append({"model": model, "skipped": False,
                             "status": err["status"], "reason": redact(err["message"])[:200]})
            with _state_lock:
                _set_cooldown(_get_state(), model, err["cooldown"], err["message"])
                _persist()

    except GeminiError as e:
        fatal = e
    finally:
        # Dziennik zapisujemy ZAWSZE - także gdy poleciał błąd nie do naprawienia.
        # Bez tego nie dało się potem sprawdzić, dlaczego użyty został inny model.
        with _state_lock:
            _log_call(_get_state(), {
                "at": _now().isoformat(),
                "wanted": wanted,
                "used": used_model,
                "ok": result is not None,
                "attempts": attempts,
                "error": fatal.message if fatal else None,
                "catalog_error": catalog_error,
            })
            _persist()

    if fatal:
        raise fatal
    if result is not None:
        return result

    hint = f" Uwaga: nie udało się pobrać listy modeli z Google ({catalog_error})." if catalog_error else ""
    raise AllModelsExhausted(
        "Wszystkie dostępne modele Gemini odmówiły odpowiedzi (najpewniej wyczerpane limity dzienne). "
        "Limity darmowego klucza resetują się o północy czasu pacyficznego." + hint,
        attempts,
    )


# ============================================================================
# ENDPOINTY
# ============================================================================

class ModelConfig(BaseModel):
    mode: str | None = None
    manual_model: str | None = None


def setup_ai_models(app):
    from fastapi import HTTPException

    @app.get("/api/ai/status")
    def ai_status():
        """Co jest ustawione, co działa, a co siedzi na kwarantannie po limicie."""
        models, catalog_error = fetch_catalog()
        with _state_lock:
            state = _get_state()
            _prune_cooldowns(state)
            chain = build_chain(state, models)
            snap = json.loads(json.dumps(state))

        usage_today = snap.get("usage", {}).get(_quota_day(), {})
        by_id = {m["id"]: m for m in models}

        def row(model_id, alias=False):
            meta = by_id.get(model_id)
            left = _cooldown_left(snap, model_id)
            return {
                "id": model_id,
                "label": meta["label"] if meta else model_id,
                "variant": meta["variant_label"] if meta else "-",
                "version": ".".join(str(v) for v in meta["version"]) if meta else None,
                "preview": meta["is_preview"] if meta else False,
                "in_catalog": meta is not None,
                "alias": alias,
                "available": left == 0,
                "cooldown_seconds": left,
                "used_today": usage_today.get(model_id, 0),
            }

        rows = [row(m) for m in chain]
        # Modele bez numeru wersji (aliasy typu "-latest", rodzina Omni) nie wchodzą
        # do automatycznej kolejki, bo nie da się ich sensownie uszeregować i zwykle
        # dzielą limit z modelem numerowanym. Ale mają być widoczne i wybieralne ręcznie.
        rows += [row(m["id"], alias=True) for m in models
                 if m["is_alias"] and m["id"] not in chain]

        active = next((r for r in rows if r["available"] and not r["alias"]), None)
        return {
            "mode": snap.get("mode", "mocny"),
            "manual_model": snap.get("manual_model"),
            "active_model": active["id"] if active else None,
            "active_label": active["label"] if active else None,
            "last_used": snap.get("last_used"),
            "last_fallback": snap.get("last_fallback"),
            "recent": snap.get("recent", [])[:8],
            "models": rows,
            "aliases_skipped": [m["id"] for m in models if m["is_alias"]],
            "any_available": active is not None,
            "catalog_error": catalog_error,
            "quota_day": _quota_day(),
            "next_reset": _next_quota_reset().isoformat(),
            "total_used_today": sum(usage_today.values()),
        }

    @app.post("/api/ai/config")
    def ai_config(cfg: ModelConfig):
        """Ustawia tryb (mocny / oszczędny / ręczny) i ewentualnie konkretny model."""
        models, _ = fetch_catalog()
        known = {m["id"] for m in models}

        with _state_lock:
            state = _get_state()
            if cfg.mode:
                if cfg.mode not in ("mocny", "oszczedny", "reczny"):
                    raise HTTPException(status_code=400, detail="Tryb musi być: mocny, oszczedny albo reczny.")
                state["mode"] = cfg.mode
            if cfg.manual_model is not None:
                chosen = cfg.manual_model or None
                if chosen and known and chosen not in known:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Model '{chosen}' nie jest dostępny dla Twojego klucza. Sprawdź GET /api/ai/catalog.",
                    )
                state["manual_model"] = chosen
            # Tryb ręczny bez wskazanego modelu zachowywałby się po cichu jak mocny,
            # a UI i tak pokazywałoby "Ręczny" - lepiej powiedzieć to wprost.
            if state["mode"] == "reczny" and not state.get("manual_model"):
                raise HTTPException(status_code=400, detail="Tryb ręczny wymaga wskazania modelu.")
            _persist()
            return {"ok": True, "mode": state["mode"], "manual_model": state.get("manual_model")}

    @app.post("/api/ai/clear-cooldowns")
    def ai_clear_cooldowns():
        """Ręczne zdjęcie kwarantanny - gdy limit zresetował się wcześniej, niż oszacowaliśmy."""
        with _state_lock:
            state = _get_state()
            count = len(state.get("cooldowns", {}))
            state["cooldowns"] = {}
            _persist()
        return {"ok": True, "cleared": count}

    @app.get("/api/ai/catalog")
    def ai_catalog(refresh: bool = False):
        """
        Pełna lista modeli tekstowych, które Twój klucz faktycznie obsługuje.
        Tutaj sprawdzisz, czy model widoczny w AI Studio naprawdę istnieje w API
        pod nazwą, której się spodziewasz.
        """
        models, error = fetch_catalog(force=refresh)
        return {
            "count": len(models),
            "error": error,
            "models": [
                {
                    "id": m["id"], "label": m["label"], "variant": m["variant_label"],
                    "version": ".".join(str(v) for v in m["version"]),
                    "preview": m["is_preview"],
                    "alias_pominiety": m["is_alias"],
                }
                for m in sorted(models, key=_rank_quality)
            ],
        }

    logger.info("Przełącznik modeli HossaLab gotowy (awaryjny model: %s).", FALLBACK_MODEL)