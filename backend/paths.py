"""
HossaLab - gdzie leżą pliki.

    backend\\            kod (.py), .env, requirements.txt
    backend\\data\\      Twoje dane: portfel, sprzedaże, dziennik, ustawienia, kopie zapasowe
    backend\\data\\cache\\  rzeczy do odtworzenia (radar, skaner, wyszukiwarka) - można skasować

Przy pierwszym starcie pliki z backend\\ (albo luzem z data\\, jeśli ktoś przeniósł je ręcznie)
są przenoszone na swoje miejsce. Niczego nie kasujemy: gdyby ten sam plik był w dwóch
miejscach, wersja z data\\ zostaje, a druga trafia do data\\_konflikty\\ do ręcznego sprawdzenia.
"""

import os
import shutil
import logging
from datetime import datetime

logger = logging.getLogger("gpw-api")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BACKEND_DIR, "data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
CONFLICT_DIR = os.path.join(DATA_DIR, "_konflikty")

# Twoje dane - tego nie da się odtworzyć
DATA_FILES = [
    "portfolio.json", "sales.json", "watchlist.json", "alerts.json", "portfolio_history.json",
    "dividend_cutoff_notifications.json", "advice_journal.json", "broker_profile.json",
    "automation_config.json", "automation_results.json", "ai_models_state.json",
]
# Pamięć podręczna - po skasowaniu aplikacja zbuduje ją od nowa
CACHE_FILES = [
    "radar_store.json", "screener_cache.json", "search_index.json", "instrument_profiles.json",
]
DATA_DIRS = ["backups"]


def data_path(name):
    return os.path.join(DATA_DIR, name)


def cache_path(name):
    return os.path.join(CACHE_DIR, name)


def _related(folder, name):
    """Plik i jego warianty: portfolio.json.przed-poprawka.bak, advice_journal.json.broken itd."""
    if not os.path.isdir(folder):
        return []
    return [f for f in os.listdir(folder)
            if (f == name or f.startswith(name + ".")) and not f.endswith(".tmp")
            and os.path.isfile(os.path.join(folder, f))]


def _move(src, dst_dir, moved, conflicts):
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(src))
    if not os.path.exists(dst):
        shutil.move(src, dst)
        moved.append(os.path.relpath(dst, BACKEND_DIR))
        return
    # Ten sam plik w dwóch miejscach - zostawiamy ten w data\, drugi odkładamy, nie kasujemy.
    os.makedirs(CONFLICT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    parked = os.path.join(CONFLICT_DIR, f"{os.path.basename(src)}.{stamp}")
    shutil.move(src, parked)
    conflicts.append(os.path.relpath(parked, BACKEND_DIR))


def migrate():
    """Przenosi pliki danych na miejsce. Bezpieczne do wielokrotnego uruchamiania."""
    moved, conflicts = [], []
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        for name in DATA_FILES:
            for f in _related(BACKEND_DIR, name):
                _move(os.path.join(BACKEND_DIR, f), DATA_DIR, moved, conflicts)
        for name in CACHE_FILES:
            for folder in (BACKEND_DIR, DATA_DIR):   # także ręcznie przeniesione do data\
                for f in _related(folder, name):
                    _move(os.path.join(folder, f), CACHE_DIR, moved, conflicts)
        for d in DATA_DIRS:
            src = os.path.join(BACKEND_DIR, d)
            if os.path.isdir(src):
                dst = os.path.join(DATA_DIR, d)
                os.makedirs(dst, exist_ok=True)
                for f in os.listdir(src):
                    _move(os.path.join(src, f), dst, moved, conflicts)
                try:
                    os.rmdir(src)
                except OSError:
                    pass
    except Exception:
        logger.exception("Przenoszenie plików do data\\ nie powiodło się - sprawdź uprawnienia do folderu")
    if moved:
        logger.info("Przeniesiono pliki danych: %s", ", ".join(moved))
    if conflicts:
        logger.warning("Ten sam plik był w dwóch miejscach - zostawiono wersję z data\\, druga leży w: %s",
                       ", ".join(conflicts))
    return moved, conflicts


migrate()
