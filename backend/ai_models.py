"""
HossaLab - wybór modelu Gemini i automatyczny fallback po wyczerpaniu limitów.

DLACZEGO TO MA SENS:
Na darmowym kluczu Google AI Studio KAŻDY model ma własny, osobny limit dzienny.
Gemini 3.6 Flash to 20 zapytań na dobę - ale 3.8, 3.7, 3.5 i 3 Flash mają po swoje
20, a modele Flash Lite po 500. Zejście na kolejny model po wyczerpaniu poprzedniego
zamienia 20 analiz dziennie w grubo ponad tysiąc, bez płacenia ani złotówki.

JAK TO DZIAŁA:
- Lista modeli NIE jest zaszyta w kodzie. Moduł pyta Google, jakie modele obsługuje
  Twój klucz (te same, które widzisz w AI Studio), i sam układa je od najmocniejszego.
  Dzięki temu nowy Gemini 3.9 pojawi się w aplikacji sam, bez zmiany kodu.
- Gdy model zwróci 429 (limit), trafia na "kwarantannę" i zapytanie leci do następnego
  w kolejce. Kwarantanna jest zapisywana na dysk, więc restart backendu nie kasuje
  wiedzy o tym, że dzienny limit się skończył.
- Błąd, który powtórzy się na każdym modelu (zły prompt, filtr bezpieczeństwa, brak
  klucza) NIE uruchamia fallbacku - inaczej jedno złe zapytanie spaliłoby limit na
  wszystkich modelach naraz.

Podpięcie na końcu main.py:
    from ai_models import setup_ai_models, generate
    setup_ai_models(app)
"""

import os
import re
import json
import logging
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

# Model startowy, gdy nic jeszcze nie wybrano. Zgodny z tym, co było w main.py.
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

_lock = threading.Lock()
_catalog_cache = {"models": None, "fetched_at": None, "error": None}
CATALOG_TTL_SECONDS = 60 * 60  # lista modeli zmienia się rzadko


# ============================================================================
# STAN NA DYSKU
# ============================================================================

def _default_state():
    return {
        "mode": "mocny",        # mocny | oszczedny | reczny
        "manual_model": None,   # używany tylko gdy mode == "reczny"
        "cooldowns": {},        # model -> ISO timestamp, do kiedy pomijamy
        "usage": {},            # "YYYY-MM-DD" -> {model: liczba udanych zapytań}
        "last_used": None,
        "last_fallback": None,  # opis ostatniego zejścia na zapasowy model
    }


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        merged = _default_state()
        merged.update(state or {})
        return merged
    except (FileNotFoundError, json.JSONDecodeError):
        return _default_state()
    except Exception:
        logger.exception("Nie udało się wczytać stanu modeli - startuję od zera")
        return _default_state()


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("Nie udało się zapisać stanu modeli")


# ============================================================================
# CZAS
# ============================================================================

def _now():
    return datetime.now(timezone.utc)


def _next_quota_reset():
    """
    Darmowe limity dzienne Google resetują się o północy czasu pacyficznego.
    zoneinfo na Windows potrzebuje pakietu 'tzdata', którego możesz nie mieć -
    dlatego przy braku bazy stref lecimy na stałym UTC-8. Różnica w okresie
    letnim to godzina, co przy limicie dobowym nie ma znaczenia.
    """
    try:
        from zoneinfo import ZoneInfo
        pacific = ZoneInfo("America/Los_Angeles")
    except Exception:
        pacific = timezone(timedelta(hours=-8))

    local = _now().astimezone(pacific)
    tomorrow = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.astimezone(timezone.utc)


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

# Modele, które nie służą do analizy tekstu - obrazy, mowa, wideo, embeddingi, robotyka.
_EXCLUDE = re.compile(
    r"(image|imagen|banana|tts|audio|live|veo|lyria|embedding|aqa|transcribe|translate|robotics|vision)",
    re.IGNORECASE,
)


def _parse_model_id(model_id):
    """
    Wyciąga z nazwy modelu wersję i wariant, żeby dało się je posortować od
    najmocniejszego. 'models/gemini-3.8-flash' -> (3.8, 'flash').
    """
    name = model_id.split("/")[-1]
    version = 0.0
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name, re.IGNORECASE)
    if m:
        try:
            version = float(m.group(1))
        except ValueError:
            version = 0.0

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

    return name, version, variant


VARIANT_LABEL = {
    "pro": "Pro",
    "flash": "Flash",
    "flash-lite": "Flash Lite",
    "gemma": "Gemma",
    "inny": "-",
}


def fetch_catalog(force=False):
    """Pobiera z Google listę modeli obsługujących generateContent. Cache: 1 godzina."""
    with _lock:
        fetched = _catalog_cache["fetched_at"]
        fresh = (
            not force
            and _catalog_cache["models"] is not None
            and fetched is not None
            and (_now() - fetched).total_seconds() < CATALOG_TTL_SECONDS
        )
        if fresh:
            return _catalog_cache["models"], _catalog_cache["error"]

    models, error = [], None
    try:
        resp = requests.get(f"{API_ROOT}/models?key={API_KEY}", timeout=20)
        if resp.status_code != 200:
            error = f"Google zwróciło {resp.status_code}: {resp.text[:200]}"
        else:
            for m in resp.json().get("models", []):
                if "generateContent" not in m.get("supportedGenerationMethods", []):
                    continue
                raw_id = m.get("name", "")
                name, version, variant = _parse_model_id(raw_id)
                if _EXCLUDE.search(name):
                    continue
                models.append({
                    "id": name,
                    "label": m.get("displayName") or name,
                    "version": version,
                    "variant": variant,
                    "variant_label": VARIANT_LABEL.get(variant, "-"),
                    "input_limit": m.get("inputTokenLimit"),
                })
    except requests.exceptions.RequestException as e:
        error = f"Brak połączenia z Google: {e}"
    except Exception as e:
        logger.exception("Nie udało się pobrać katalogu modeli")
        error = str(e)

    if error and _catalog_cache["models"]:
        # Lepiej podać lekko nieaktualną listę niż żadną - a błąd i tak zwracamy dalej.
        return _catalog_cache["models"], error

    with _lock:
        _catalog_cache.update(models=models, fetched_at=_now(), error=error)
    return models, error


def _rank_quality(m):
    """Od najmocniejszego: Flash nowszy > Flash starszy > Flash Lite > reszta."""
    order = {"flash": 0, "pro": 1, "flash-lite": 2, "gemma": 3, "inny": 4}
    return (order.get(m["variant"], 9), -m["version"], m["id"])


def _rank_economy(m):
    """Od najtańszego w limitach: Flash Lite (500/dobę) przed Flash (20/dobę)."""
    order = {"flash-lite": 0, "flash": 1, "pro": 2, "gemma": 3, "inny": 4}
    return (order.get(m["variant"], 9), -m["version"], m["id"])


def build_chain(state, models, prefer=None):
    """Układa kolejkę modeli do wypróbowania: wybrany ręcznie idzie na początek."""
    mode = state.get("mode", "mocny")
    if mode == "oszczedny":
        ordered = sorted(models, key=_rank_economy)
    else:
        ordered = sorted(models, key=_rank_quality)

    chain = [m["id"] for m in ordered]

    head = prefer or (state.get("manual_model") if mode == "reczny" else None)
    if head:
        chain = [head] + [m for m in chain if m != head]

    if not chain:
        chain = [DEFAULT_MODEL]  # awaryjnie, gdy katalog się nie pobrał
    return chain


# ============================================================================
# KWARANTANNA PO WYCZERPANIU LIMITU
# ============================================================================

def _cooldown_left(state, model):
    until = _parse_iso(state.get("cooldowns", {}).get(model))
    if not until:
        return 0
    return max(0, int((until - _now()).total_seconds()))


def _set_cooldown(state, model, seconds, reason):
    until = _now() + timedelta(seconds=max(5, int(seconds)))
    state.setdefault("cooldowns", {})[model] = until.isoformat()
    logger.warning(
        "Model %s na kwarantannie do %s (%s)",
        model, until.astimezone().strftime("%H:%M"), reason
    )


def _retry_delay_from_error(data):
    """Google potrafi podać w błędzie 429 sugerowany czas odczekania - użyjmy go."""
    try:
        for detail in data.get("error", {}).get("details", []):
            delay = detail.get("retryDelay")
            if isinstance(delay, str) and delay.endswith("s"):
                return float(delay[:-1])
    except Exception:
        pass
    return None


def _looks_like_daily_quota(message):
    """Limit MINUTOWY mija po chwili, DOBOWY dopiero o północy - to różne kwarantanny."""
    text = (message or "").lower()
    return any(k in text for k in ("per day", "perday", "daily", "requests per day", "/d'"))


def _note_usage(state, model):
    day = _now().strftime("%Y-%m-%d")
    usage = state.setdefault("usage", {})
    usage.setdefault(day, {})
    usage[day][model] = usage[day].get(model, 0) + 1
    # Trzymamy tylko ostatni tydzień, żeby plik nie puchł w nieskończoność.
    for old in [d for d in usage if d < (_now() - timedelta(days=7)).strftime("%Y-%m-%d")]:
        usage.pop(old, None)
    state["last_used"] = {"model": model, "at": _now().isoformat()}


# ============================================================================
# WYWOŁANIE
# ============================================================================

class GeminiError(Exception):
    """Błąd, którego nie naprawi zmiana modelu (zły prompt, filtr, brak klucza)."""
    def __init__(self, message, status=502):
        super().__init__(message)
        self.message = message
        self.status = status


class AllModelsExhausted(Exception):
    """Wszystkie modele w kolejce odpadły na limitach."""
    def __init__(self, message, attempts):
        super().__init__(message)
        self.message = message
        self.attempts = attempts


def _try_model(model, prompt, timeout):
    """
    Zwraca (tekst, None) przy sukcesie albo (None, info_o_bledzie).
    info_o_bledzie: {"retryable": bool, "message": str, "status": int, "cooldown": sekundy}
    """
    url = f"{API_ROOT}/models/{model}:generateContent?key={API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
    except requests.exceptions.Timeout:
        # Timeout bywa objawem przeciążenia modelu - warto spróbować następnego.
        return None, {"retryable": True, "message": "Przekroczony czas odpowiedzi.", "status": 504, "cooldown": 120}
    except requests.exceptions.RequestException as e:
        raise GeminiError(f"Nie udało się połączyć z Gemini API: {e}", 502)

    try:
        data = resp.json()
    except ValueError:
        return None, {"retryable": True, "message": "Odpowiedź nie była JSON-em.", "status": resp.status_code, "cooldown": 120}

    if resp.status_code == 200:
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"], None
        except (KeyError, IndexError):
            # Pusta odpowiedź to zwykle filtr bezpieczeństwa - inny model zachowa się tak samo.
            raise GeminiError("Gemini nie zwróciło treści (możliwy filtr bezpieczeństwa).", 502)

    message = data.get("error", {}).get("message", f"Błąd {resp.status_code}")

    if resp.status_code == 429:
        suggested = _retry_delay_from_error(data)
        if suggested:
            cooldown = suggested + 5
        elif _looks_like_daily_quota(message):
            cooldown = max(60, (_next_quota_reset() - _now()).total_seconds())
        else:
            cooldown = 90  # limit minutowy
        return None, {"retryable": True, "message": message, "status": 429, "cooldown": cooldown}

    if resp.status_code == 404:
        # Model nie istnieje dla tego klucza - nie ma po co wracać do niego dziś.
        return None, {"retryable": True, "message": message, "status": 404, "cooldown": 6 * 3600}

    if resp.status_code in (500, 502, 503):
        return None, {"retryable": True, "message": message, "status": resp.status_code, "cooldown": 300}

    # 400, 401, 403 - zły klucz albo zły prompt. Każdy model odbije się tak samo.
    raise GeminiError(message, 502)


def generate(prompt, timeout=60, prefer=None):
    """
    Wysyła prompt do pierwszego dostępnego modelu z kolejki.
    Podnosi GeminiError (błąd nie do naprawienia) albo AllModelsExhausted.
    """
    if not API_KEY:
        raise GeminiError("Brak GOOGLE_API_KEY w backend/.env.", 500)

    state = load_state()
    models, _ = fetch_catalog()
    chain = build_chain(state, models, prefer=prefer)

    attempts = []
    started_with = chain[0] if chain else None

    for model in chain:
        left = _cooldown_left(state, model)
        if left > 0:
            attempts.append({"model": model, "skipped": True, "reason": f"kwarantanna jeszcze {left // 60 + 1} min"})
            continue

        text, err = _try_model(model, prompt, timeout)

        if err is None:
            _note_usage(state, model)
            if attempts:  # coś odpadło po drodze - zapisz, żeby dało się pokazać w UI
                state["last_fallback"] = {
                    "at": _now().isoformat(),
                    "wanted": started_with,
                    "used": model,
                    "attempts": attempts,
                }
                logger.info("Fallback modelu: %s -> %s", started_with, model)
            save_state(state)
            return text

        _set_cooldown(state, model, err["cooldown"], err["message"][:120])
        attempts.append({"model": model, "skipped": False, "status": err["status"], "reason": err["message"][:160]})

    save_state(state)
    raise AllModelsExhausted(
        "Wszystkie dostępne modele Gemini odmówiły odpowiedzi (najpewniej wyczerpane limity dzienne). "
        "Limity darmowego klucza resetują się o północy czasu pacyficznego.",
        attempts,
    )


# ============================================================================
# ENDPOINTY
# ============================================================================

class ModelConfig(BaseModel):
    mode: str | None = None          # mocny | oszczedny | reczny
    manual_model: str | None = None


def setup_ai_models(app):
    from fastapi import HTTPException

    @app.get("/api/ai/status")
    def ai_status():
        """Co jest ustawione, co działa, a co siedzi na kwarantannie po limicie."""
        state = load_state()
        models, catalog_error = fetch_catalog()
        chain = build_chain(state, models)
        today = _now().strftime("%Y-%m-%d")
        usage_today = state.get("usage", {}).get(today, {})

        rows = []
        for model_id in chain:
            meta = next((m for m in models if m["id"] == model_id), None)
            left = _cooldown_left(state, model_id)
            rows.append({
                "id": model_id,
                "label": meta["label"] if meta else model_id,
                "variant": meta["variant_label"] if meta else "-",
                "version": meta["version"] if meta else None,
                "available": left == 0,
                "cooldown_seconds": left,
                "cooldown_until": state.get("cooldowns", {}).get(model_id),
                "used_today": usage_today.get(model_id, 0),
            })

        active = next((r for r in rows if r["available"]), None)
        return {
            "mode": state.get("mode", "mocny"),
            "manual_model": state.get("manual_model"),
            "active_model": active["id"] if active else None,
            "active_label": active["label"] if active else None,
            "last_used": state.get("last_used"),
            "last_fallback": state.get("last_fallback"),
            "models": rows,
            "any_available": active is not None,
            "catalog_error": catalog_error,
            "total_used_today": sum(usage_today.values()),
        }

    @app.post("/api/ai/config")
    def ai_config(cfg: ModelConfig):
        """Ustawia tryb (mocny / oszczędny / ręczny) i ewentualnie konkretny model."""
        state = load_state()
        if cfg.mode:
            if cfg.mode not in ("mocny", "oszczedny", "reczny"):
                raise HTTPException(status_code=400, detail="Tryb musi być: mocny, oszczedny albo reczny.")
            state["mode"] = cfg.mode
        if cfg.manual_model is not None:
            state["manual_model"] = cfg.manual_model or None
        save_state(state)
        return {"ok": True, "mode": state["mode"], "manual_model": state["manual_model"]}

    @app.post("/api/ai/clear-cooldowns")
    def ai_clear_cooldowns():
        """
        Ręczne zdjęcie kwarantanny. Przydatne, gdy limit zresetował się wcześniej
        niż oszacowaliśmy - aplikacja nie ma jak się o tym dowiedzieć inaczej niż
        przez próbę.
        """
        state = load_state()
        count = len(state.get("cooldowns", {}))
        state["cooldowns"] = {}
        save_state(state)
        return {"ok": True, "cleared": count}

    @app.get("/api/ai/catalog")
    def ai_catalog(refresh: bool = False):
        """Pełna lista modeli tekstowych dostępnych dla Twojego klucza."""
        models, error = fetch_catalog(force=refresh)
        return {"models": sorted(models, key=_rank_quality), "error": error, "count": len(models)}

    logger.info("Przełącznik modeli HossaLab gotowy (start: %s).", DEFAULT_MODEL)
