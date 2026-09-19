"""
HossaLab - automatyzacja (harmonogram) i powiadomienia Telegram.

Moduł jest samodzielny: main.py go importuje, ale on nie importuje main.py
(brak cyklicznego importu). Funkcje do wywołania w tle przekazujesz przy
podpięciu, na końcu main.py:

    from automation import setup_automation, send_telegram_alert
    setup_automation(app, scheduler, digest_fn=morning_digest, espi_fn=espi_scan)

Konfiguracja w backend/.env:
    TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
    TELEGRAM_CHAT_ID=123456789
"""

import os
import re
import json
import html
import hashlib
import logging
from datetime import datetime

import requests
from pydantic import BaseModel
from dotenv import load_dotenv

# Wczytujemy .env TUTAJ, a nie polegamy na main.py: ten moduł bywa importowany
# zanim main.py zdąży wywołać load_dotenv(), a wtedy token byłby jeszcze pusty
# i Telegram zgłaszałby się jako niepodłączony mimo poprawnej konfiguracji.
load_dotenv()

logger = logging.getLogger("gpw-api")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUTOMATION_CONFIG_FILE = os.path.join(BASE_DIR, "automation_config.json")
AUTOMATION_RESULTS_FILE = os.path.join(BASE_DIR, "automation_results.json")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

TIMEZONE = os.environ.get("HOSSALAB_TZ", "Europe/Warsaw")

DEFAULT_CONFIG = {
    "digest_enabled": True,
    "digest_hour": 7,
    "digest_minute": 30,
    "digest_weekdays_only": True,
    "espi_enabled": True,
    "espi_every_hours": 2,
    "espi_start_hour": 8,        # w nocy i tak nic nie wychodzi
    "espi_end_hour": 20,
    "telegram_digest": True,
    "telegram_espi": True,
    "telegram_alerts": True,
}

# Funkcje przekazane przy setup_automation() - wypełniane w setup_automation()
_digest_fn = None
_espi_fn = None
_scheduler = None


# ---------------------------------------------------------------- pliki stanu

def _read_json(path, fallback):
    if not os.path.exists(path):
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.exception("Nie udało się odczytać %s", path)
        return fallback


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_config():
    """Konfiguracja z pliku, uzupełniona domyślnymi wartościami (dla nowych opcji)."""
    return {**DEFAULT_CONFIG, **_read_json(AUTOMATION_CONFIG_FILE, {})}


def save_config(cfg):
    merged = {**DEFAULT_CONFIG, **cfg}
    _write_json(AUTOMATION_CONFIG_FILE, merged)
    return merged


def load_results():
    return _read_json(AUTOMATION_RESULTS_FILE, {})


def save_results(results):
    _write_json(AUTOMATION_RESULTS_FILE, results)


# ------------------------------------------------------------------ Telegram

def markdown_to_telegram_html(text):
    """
    Zamienia Markdown od Gemini na HTML, który rozumie Telegram.

    Telegram wspiera tylko kilka tagów (<b>, <i>, <code>, <pre>, <a>), a jego
    MarkdownV2 wymaga escapowania kilkunastu znaków - jeden nieuciekinięty znak
    wywala całą wiadomość. HTML jest bezpieczniejszy: escapujemy wszystko,
    a potem wstawiamy tylko potrzebne tagi.
    """
    if not text:
        return ""

    out = html.escape(text)

    # Nagłówki (## Tytuł) -> pogrubienie
    out = re.sub(r"^\s*#{1,6}\s*(.+)$", r"<b>\1</b>", out, flags=re.MULTILINE)

    # **pogrubienie**. Bez DOTALL, żeby pojedyncza gwiazdka nie zjadła pół wiadomości.
    out = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"__([^_\n]+)__", r"<b>\1</b>", out)

    # *kursywa* / _kursywa_
    out = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<i>\1</i>", out)
    out = re.sub(r"(?<![\w_])_([^_\n]+)_(?![\w_])", r"<i>\1</i>", out)

    # `kod`
    out = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", out)

    # Punktory -> •
    out = re.sub(r"^\s*[-*]\s+", "• ", out, flags=re.MULTILINE)

    # Poziome linie tylko zaśmiecają widok na telefonie
    out = re.sub(r"^\s*[-–—]{3,}\s*$", "", out, flags=re.MULTILINE)

    # Maksymalnie jedna pusta linia z rzędu
    out = re.sub(r"\n{3,}", "\n\n", out)

    return out.strip()


def split_message(text, limit=3900):
    """
    Dzieli wiadomość na części mieszczące się w limicie Telegrama (4096 znaków).
    Tnie po akapitach, a gdy akapit sam jest za długi - po liniach, żeby nie
    rozciąć tagu HTML w połowie.
    """
    if len(text) <= limit:
        return [text] if text else []

    chunks, current = [], ""

    for para in text.split("\n\n"):
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= limit:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(para) <= limit:
            current = para
            continue

        for line in para.split("\n"):
            cand2 = f"{current}\n{line}" if current else line
            if len(cand2) <= limit:
                current = cand2
                continue

            if current:
                chunks.append(current)
                current = ""

            # Sama linia dłuższa niż limit - tniemy ją na kawałki zamiast obcinać,
            # inaczej zgubilibyśmy jej dalszą część.
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current = line

    if current:
        chunks.append(current)
    return chunks


def _post_telegram(text, silent=False):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": silent,
        },
        timeout=20,
    )
    return resp


def send_telegram(text, title=None, silent=False):
    """
    Wysyła wiadomość na Telegrama. Zwraca (wysłano: bool, info: str).
    Gdy Telegram nie jest skonfigurowany, nic nie robi - reszta automatyzacji
    (wyniki w apce) działa niezależnie.
    """
    if not TELEGRAM_ENABLED:
        return False, "Telegram nie jest skonfigurowany (brak TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID w .env)."

    body = markdown_to_telegram_html(text)
    if title:
        body = f"<b>{html.escape(title)}</b>\n\n{body}"

    parts = split_message(body)
    if not parts:
        return False, "Pusta wiadomość - nie wysyłam."

    sent = 0
    for i, part in enumerate(parts):
        suffix = f"\n\n<i>({i + 1}/{len(parts)})</i>" if len(parts) > 1 else ""
        try:
            resp = _post_telegram(part + suffix, silent=silent)
            if resp.status_code != 200:
                try:
                    detail = resp.json().get("description", resp.text[:200])
                except ValueError:
                    detail = resp.text[:200]
                logger.error("Telegram odrzucił wiadomość (%s): %s", resp.status_code, detail)
                return sent > 0, f"Telegram zwrócił błąd: {detail}"
            sent += 1
        except requests.exceptions.RequestException as e:
            logger.exception("Błąd sieci przy wysyłce na Telegrama")
            return sent > 0, f"Błąd sieci: {e}"

    return True, f"Wysłano {sent} wiadomość(i)."


def send_telegram_alert(subject, body):
    """
    Powiadomienie o alercie (cenowym / dywidendowym). Podepnij to w send_alert_email
    w main.py, żeby jednym ruchem objąć oba rodzaje alertów.

    Treść alertów jest zwykłym tekstem (nie Markdownem), więc escapujemy ją w całości
    i nie próbujemy interpretować gwiazdek - w cenach i tak ich nie ma.
    """
    if not TELEGRAM_ENABLED or not load_config().get("telegram_alerts"):
        return False

    text = f"<b>{html.escape(str(subject))}</b>\n\n{html.escape(str(body))}"
    try:
        for part in split_message(text):
            resp = _post_telegram(part)
            if resp.status_code != 200:
                logger.error("Telegram odrzucił alert: %s", resp.text[:200])
                return False
        return True
    except requests.exceptions.RequestException:
        logger.exception("Nie udało się wysłać alertu na Telegrama")
        return False


# ------------------------------------------------------------- wyniki zadań

def _store_result(key, payload, error=None):
    """Zapisuje wynik zadania i oznacza go jako nieprzeczytany (plakietka w apce)."""
    results = load_results()
    results[key] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "unread": error is None,
        "error": error,
        **(payload or {}),
    }
    save_results(results)
    return results[key]


def _content_hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def run_morning_digest(force_send=False):
    """Poranny briefing: uruchamia Twoją funkcję, zapisuje wynik, wysyła na Telegrama."""
    cfg = load_config()

    if _digest_fn is None:
        return _store_result("digest", None, error="Funkcja briefingu nie została podpięta.")

    try:
        data = _digest_fn()
    except Exception as e:
        logger.exception("Poranny briefing nie powiódł się")
        return _store_result("digest", None, error=str(e))

    report = data.get("report", "")
    stored = _store_result("digest", {
        "report": report,
        "market_snapshot": data.get("market_snapshot", []),
    })

    if report and (force_send or cfg.get("telegram_digest")):
        stamp = datetime.now().strftime("%d.%m.%Y")
        ok, info = send_telegram(report, title=f"☕ Poranny briefing · {stamp}")
        logger.info("Briefing na Telegrama: %s (%s)", ok, info)

    return stored


def run_espi_scan(force_send=False):
    """
    Skan ESPI/EBI. Wysyła na Telegrama TYLKO gdy treść raportu się zmieniła -
    inaczej co 2h dostawałbyś ten sam komunikat i przestałbyś je czytać.
    """
    cfg = load_config()

    if _espi_fn is None:
        return _store_result("espi", None, error="Funkcja skanera ESPI nie została podpięta.")

    try:
        data = _espi_fn()
    except Exception as e:
        logger.exception("Skan ESPI nie powiódł się")
        return _store_result("espi", None, error=str(e))

    report = data.get("report", "")
    new_hash = _content_hash(report)
    previous = load_results().get("espi", {})
    changed = previous.get("content_hash") != new_hash

    stored = _store_result("espi", {
        "report": report,
        "companies_checked": data.get("companies_checked", []),
        "content_hash": new_hash,
        "changed": changed,
    })

    # Skan, który niczego nie zmienił, nie powinien świecić plakietką "nowe"
    if not changed and not force_send:
        results = load_results()
        results["espi"]["unread"] = previous.get("unread", False)
        save_results(results)

    if report and (force_send or (cfg.get("telegram_espi") and changed)):
        stamp = datetime.now().strftime("%d.%m %H:%M")
        ok, info = send_telegram(report, title=f"📜 Nowe komunikaty ESPI · {stamp}")
        logger.info("ESPI na Telegrama: %s (%s)", ok, info)
    elif not changed:
        logger.info("Skan ESPI: brak zmian względem poprzedniego - nie wysyłam powiadomienia.")

    return stored


# ---------------------------------------------------------------- harmonogram

def _digest_job():
    if load_config().get("digest_enabled"):
        run_morning_digest()


def _espi_job():
    """Skan ESPI tylko w godzinach sesji."""
    cfg = load_config()
    if not cfg.get("espi_enabled"):
        return
    hour = datetime.now().hour
    if not (cfg.get("espi_start_hour", 8) <= hour <= cfg.get("espi_end_hour", 20)):
        logger.info("Skan ESPI pominięty - poza godzinami (%s:00).", hour)
        return
    run_espi_scan()


def reschedule():
    """Przebudowuje zadania wg aktualnej konfiguracji (po zapisie z interfejsu)."""
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    if _scheduler is None:
        return

    cfg = load_config()

    for job_id in ("hossalab_digest", "hossalab_espi"):
        try:
            _scheduler.remove_job(job_id)
        except Exception:
            pass  # zadania mogło nie być - normalne przy pierwszym uruchomieniu

    if cfg.get("digest_enabled"):
        _scheduler.add_job(
            _digest_job,
            CronTrigger(
                hour=cfg.get("digest_hour", 7),
                minute=cfg.get("digest_minute", 30),
                day_of_week="mon-fri" if cfg.get("digest_weekdays_only", True) else "*",
                timezone=TIMEZONE,
            ),
            id="hossalab_digest",
            replace_existing=True,
        )

    if cfg.get("espi_enabled"):
        _scheduler.add_job(
            _espi_job,
            IntervalTrigger(hours=max(1, int(cfg.get("espi_every_hours", 2))), timezone=TIMEZONE),
            id="hossalab_espi",
            replace_existing=True,
        )

    logger.info(
        "Harmonogram HossaLab: %s",
        [j.id for j in _scheduler.get_jobs() if j.id.startswith("hossalab_")],
    )


# ------------------------------------------------------------------- endpointy

class AutomationConfigUpdate(BaseModel):
    digest_enabled: bool | None = None
    digest_hour: int | None = None
    digest_minute: int | None = None
    digest_weekdays_only: bool | None = None
    espi_enabled: bool | None = None
    espi_every_hours: int | None = None
    espi_start_hour: int | None = None
    espi_end_hour: int | None = None
    telegram_digest: bool | None = None
    telegram_espi: bool | None = None
    telegram_alerts: bool | None = None


def setup_automation(app, scheduler, digest_fn=None, espi_fn=None):
    """
    Podpina automatyzację. Wywołaj NA KOŃCU main.py, gdy morning_digest i espi_scan
    są już zdefiniowane:

        setup_automation(app, scheduler, digest_fn=morning_digest, espi_fn=espi_scan)
    """
    global _scheduler, _digest_fn, _espi_fn

    _scheduler = scheduler
    _digest_fn = digest_fn
    _espi_fn = espi_fn

    @app.on_event("startup")
    def _start_automation():
        # Scheduler startuje Twój istniejący start_scheduler() - tu tylko dokładamy zadania.
        reschedule()
        logger.info(
            "Automatyzacja HossaLab gotowa (Telegram: %s).",
            "włączony" if TELEGRAM_ENABLED else "wyłączony",
        )

    @app.get("/api/automation/config")
    def get_automation_config():
        jobs = []
        if _scheduler:
            for job in _scheduler.get_jobs():
                if job.id.startswith("hossalab_"):
                    jobs.append({
                        "id": job.id,
                        "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                    })
        return {
            "config": load_config(),
            "telegram_configured": TELEGRAM_ENABLED,
            "timezone": TIMEZONE,
            "jobs": jobs,
        }

    @app.post("/api/automation/config")
    def update_automation_config(update: AutomationConfigUpdate):
        cfg = load_config()
        for key, value in update.model_dump(exclude_none=True).items():
            cfg[key] = value

        # Sanity: wartości poza zakresem wysypałyby CronTrigger przy starcie
        cfg["digest_hour"] = max(0, min(23, int(cfg["digest_hour"])))
        cfg["digest_minute"] = max(0, min(59, int(cfg["digest_minute"])))
        cfg["espi_every_hours"] = max(1, min(24, int(cfg["espi_every_hours"])))
        cfg["espi_start_hour"] = max(0, min(23, int(cfg["espi_start_hour"])))
        cfg["espi_end_hour"] = max(0, min(23, int(cfg["espi_end_hour"])))

        saved = save_config(cfg)
        reschedule()
        return {"config": saved}

    @app.get("/api/automation/results")
    def get_automation_results():
        return {"results": load_results()}

    @app.post("/api/automation/results/{key}/read")
    def mark_result_read(key: str):
        results = load_results()
        if key in results:
            results[key]["unread"] = False
            save_results(results)
        return {"ok": True}

    @app.post("/api/automation/run/digest")
    def run_digest_now():
        return {"result": run_morning_digest(force_send=True)}

    @app.post("/api/automation/run/espi")
    def run_espi_now():
        return {"result": run_espi_scan(force_send=True)}

    @app.post("/api/telegram/test")
    def telegram_test():
        ok, info = send_telegram(
            "Jeśli to widzisz, powiadomienia działają poprawnie.\n\n"
            "Od teraz dostaniesz tu **poranny briefing**, świeże **komunikaty ESPI** "
            "i **alerty cenowe**.",
            title="✅ HossaLab podłączony",
        )
        return {"sent": ok, "info": info, "configured": TELEGRAM_ENABLED}

    return _scheduler
