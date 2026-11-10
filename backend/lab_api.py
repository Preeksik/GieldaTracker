"""
HossaLab - zakładka QuantPulse: gra na papierze bez terminala.

Endpointy:
    GET  /api/lab/paper        gotowy raport (typy A1/A2/A3, wynik od startu, historia, wykres) - tylko odczyt pliku
    POST /api/lab/paper/run    "Rozlicz teraz" - to samo co `python -m lab.paper run`, w tle
    GET  /api/lab/paper/live   "Sprawdź na dziś" - świeże ceny z Yahoo, kilka sekund, NIC nie zapisuje

Automat: w dni robocze o 18:30 (po sesji GPW, z zapasem na notowania w Yahoo).
Jeśli backend był wtedy wyłączony, rozliczenie rusza kilka minut po najbliższym starcie.

BEZPIECZEŃSTWO:
  - nic nie jest kupowane, nie ma połączenia z XTB - zmienia się tylko dziennik gry na papierze,
  - strategie są zamrożone (lab/vault.py), przycisk nie ma żadnych ustawień,
  - przy pustym dzienniku (laptop) nic się nie uruchamia - inaczej powstałaby druga, osobna gra,
  - blokada: automat, przycisk i terminal nie liczą naraz; przed zapisem kopia dziennika.

Laboratorium (pandas itd.) jest importowane dopiero przy rozliczaniu, żeby nie spowalniać startu aplikacji.
"""

import os
import json
import logging
import threading
from datetime import datetime, timedelta

logger = logging.getLogger("gpw-api")

AUTO_HOUR, AUTO_MINUTE = 18, 30
CATCH_UP_DELAY_MIN = 3
LIVE_CACHE_SECONDS = 60          # podgląd na dziś: nie pytamy Yahoo częściej niż raz na minutę
SCHEDULE_TEXT = f"w dni robocze o {AUTO_HOUR}:{AUTO_MINUTE:02d}"


def default_lab_dir():
    from paths import data_path
    return data_path("lab")


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def last_slot(now):
    """Ostatni termin automatu (dzień roboczy, 18:30), który już minął."""
    d = now.replace(hour=AUTO_HOUR, minute=AUTO_MINUTE, second=0, microsecond=0)
    if d > now:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def next_slot(now):
    d = now.replace(hour=AUTO_HOUR, minute=AUTO_MINUTE, second=0, microsecond=0)
    if d <= now:
        d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def needs_catch_up(last_ok, now):
    """Czy automat ominął termin (backend był wyłączony o 18:30)?"""
    if not last_ok:
        return True
    try:
        return datetime.fromisoformat(last_ok) < last_slot(now)
    except ValueError:
        return True


def setup_lab(app, scheduler=None, refresh_fn=None, lab_dir=None, now_fn=datetime.now, live_fn=None):
    """
    refresh_fn(trigger) - pełne rozliczenie; domyślnie lab.paper_report.refresh. W testach podmieniane.
    live_fn(journal) - podgląd na dziś; domyślnie lab.paper_live.live_check.
    lab_dir - folder data/lab (domyślnie z paths.py).
    """
    from fastapi import HTTPException

    lab_dir = lab_dir or default_lab_dir()
    journal_file = os.path.join(lab_dir, "paper_journal.json")
    status_file = os.path.join(lab_dir, "paper_status.json")
    lock_file = os.path.join(lab_dir, "paper.lock")
    state = {"running": False, "error": None}
    guard = threading.Lock()

    def default_refresh(trigger):
        import logging as _l
        _l.getLogger("yfinance").setLevel(_l.CRITICAL)
        from lab import paper_report as R
        return R.refresh(trigger=trigger, journal=journal_file, lab_dir=lab_dir, require_game=True)

    do_refresh = refresh_fn or default_refresh

    def game_exists():
        j = _read(journal_file) or {}
        return bool(j.get("sets"))

    def lock_busy():
        if not os.path.exists(lock_file):
            return False
        return (datetime.now().timestamp() - os.path.getmtime(lock_file)) < 3 * 3600

    def run_once(trigger):
        """Jedno rozliczenie. Zwraca False, gdy nie było co / nie wolno liczyć."""
        with guard:
            if state["running"]:
                return False
            state["running"] = True
        try:
            if not game_exists():
                logger.info("QuantPulse: dziennik gry pusty - rozliczanie pominięte (gra na innym komputerze).")
                return False
            logger.info("QuantPulse: rozliczanie gry na papierze (%s)…", trigger)
            do_refresh(trigger)
            state["error"] = None
            logger.info("QuantPulse: rozliczenie zakończone.")
            return True
        except Exception as e:
            # Busy z terminala też tu trafia - to nie błąd, tylko ktoś już liczy
            if type(e).__name__ == "Busy":
                logger.info("QuantPulse: %s", e)
            else:
                state["error"] = f"{type(e).__name__}: {e}"
                logger.exception("QuantPulse: rozliczanie nie powiodło się")
            return False
        finally:
            state["running"] = False

    @app.get("/api/lab/paper")
    def lab_paper():
        status = _read(status_file) or {}
        now = now_fn()
        return {
            "game": game_exists(),
            "running": state["running"] or lock_busy(),
            "report": status.get("report"),
            "last_run": status.get("last_run"),
            "last_ok": status.get("last_ok"),
            "error": state["error"],
            "auto": {"enabled": scheduler is not None, "when": SCHEDULE_TEXT,
                     "next": next_slot(now).isoformat(timespec="minutes")},
            "vault": _vault(),
        }

    def _vault():
        try:
            from lab.paper_report import vault_summary
            return vault_summary(lab_dir)
        except Exception:
            return None

    @app.post("/api/lab/paper/run")
    def lab_paper_run():
        if not game_exists():
            raise HTTPException(status_code=409, detail=(
                "Na tym komputerze nie ma gry na papierze - jest prowadzona na komputerze stacjonarnym. "
                "Przycisk jest zablokowany, żeby nie założyć drugiej, osobnej gry."))
        if state["running"] or lock_busy():
            raise HTTPException(status_code=409, detail="Rozliczanie już trwa - poczekaj, aż się skończy.")
        threading.Thread(target=run_once, args=("przycisk",), daemon=True, name="quantpulse-paper").start()
        return {"started": True}

    # --- Szybki podgląd "na dziś" (lab/paper_live.py): świeże ceny, nic nie zapisuje ---
    live_cache = {"at": None, "data": None}
    live_guard = threading.Lock()

    def default_live(journal):
        import logging as _l
        _l.getLogger("yfinance").setLevel(_l.CRITICAL)
        from lab.paper_live import live_check
        return live_check(journal)

    do_live = live_fn or default_live

    @app.get("/api/lab/paper/live")
    def lab_paper_live(fresh: bool = False):
        journal = _read(journal_file) or {}
        if not journal.get("sets"):
            raise HTTPException(status_code=409, detail=(
                "Na tym komputerze nie ma gry na papierze - jest prowadzona na komputerze stacjonarnym."))
        with live_guard:                       # dwa kliknięcia naraz = jedno pobranie
            at = live_cache["at"]
            if not fresh and at and (datetime.now() - at).total_seconds() < LIVE_CACHE_SECONDS:
                return {**live_cache["data"], "cached": True}
            try:
                data = do_live(journal)
            except Exception as e:
                logger.exception("QuantPulse: podgląd na dziś nie powiódł się")
                raise HTTPException(status_code=502, detail=f"Nie udało się pobrać cen z Yahoo: {e}")
            live_cache.update(at=datetime.now(), data=data)
            return {**data, "cached": False}

    app.state.lab_run_once = run_once        # do testów

    if scheduler is not None:
        try:
            from apscheduler.triggers.cron import CronTrigger
            scheduler.add_job(lambda: run_once("automat"),
                              CronTrigger(day_of_week="mon-fri", hour=AUTO_HOUR, minute=AUTO_MINUTE),
                              id="quantpulse_paper", replace_existing=True, max_instances=1, coalesce=True,
                              misfire_grace_time=3 * 3600)
            last_ok = (_read(status_file) or {}).get("last_ok")
            if game_exists() and needs_catch_up(last_ok, now_fn()):
                scheduler.add_job(lambda: run_once("zaległe"), "date", id="quantpulse_paper_catchup",
                                  replace_existing=True,
                                  run_date=datetime.now() + timedelta(minutes=CATCH_UP_DELAY_MIN))
                logger.info("QuantPulse: zaległe rozliczenie ruszy za %d min.", CATCH_UP_DELAY_MIN)
        except Exception:
            logger.exception("QuantPulse: nie udało się dodać rozliczania do harmonogramu")

    logger.info("QuantPulse gotowy (rozliczanie gry na papierze %s, gra na tym komputerze: %s).",
                SCHEDULE_TEXT, "tak" if game_exists() else "nie")
