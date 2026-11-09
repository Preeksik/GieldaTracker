"""
QuantPulse - raport gry na papierze dla zakładki w aplikacji.

Ciężka robota (pobranie notowań, cechy, nauka sieci, rozliczenie) dzieje się w `refresh()`,
uruchamianym wieczorem w tle albo przyciskiem. Wynik trafia do pliku `paper_status.json`,
a zakładka tylko go czyta - dzięki temu otwiera się od razu.

Strategie i sposób rozliczania są te same co w `paper.py` - ten moduł niczego w nich nie zmienia,
tylko dokłada: wykres dzień po dniu, wynik każdej spółki, blokadę przed podwójnym uruchomieniem
i kopię dziennika przed każdym zapisem.

    paper_status.json   gotowy raport + informacja o ostatnim rozliczeniu (do odtworzenia)
    paper.lock          "trwa rozliczanie" - istnieje tylko w trakcie liczenia
    paper_journal.backup.json   kopia dziennika sprzed ostatniego rozliczenia
"""

import os
import json
import shutil
import logging
from datetime import datetime

import numpy as np
import pandas as pd

from . import features as F
from . import paper as P
from . import vault as V

logger = logging.getLogger("gpw-api")

START_VALUE = 10000.0
LOCK_STALE_HOURS = 3          # starsza blokada = pozostałość po zamkniętym programie, można ją zdjąć


def status_path(lab_dir=None):
    return os.path.join(lab_dir or F.lab_dir(), "paper_status.json")


def lock_path(lab_dir=None):
    return os.path.join(lab_dir or F.lab_dir(), "paper.lock")


def backup_path(journal):
    return os.path.splitext(journal)[0] + ".backup.json"


def _clean(x):
    """Liczby do JSON-a: NaN/inf -> None, numpy -> zwykłe float."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# ============================================================================
# BLOKADA - żeby automat, przycisk i terminal nie liczyły naraz
# ============================================================================

def lock_active(lab_dir=None):
    path = lock_path(lab_dir)
    if not os.path.exists(path):
        return False
    age_h = (datetime.now().timestamp() - os.path.getmtime(path)) / 3600
    return age_h < LOCK_STALE_HOURS


def acquire_lock(lab_dir=None, who=""):
    """True = blokada nasza. False = ktoś już liczy."""
    path = lock_path(lab_dir)
    if os.path.exists(path) and not lock_active(lab_dir):
        logger.warning("QuantPulse: zdejmuję starą blokadę %s (program pewnie został zamknięty w trakcie)", path)
        try:
            os.remove(path)
        except OSError:
            pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"{who} {datetime.now().isoformat(timespec='seconds')} pid={os.getpid()}")
    return True


def release_lock(lab_dir=None):
    try:
        os.remove(lock_path(lab_dir))
    except OSError:
        pass


# ============================================================================
# RAPORT
# ============================================================================

def _ticker_return(prices, t, entry, end):
    s = prices.get(t)
    if s is None or entry is None or end is None or entry not in s.index or end not in s.index:
        return None
    a, b = s.loc[entry], s.loc[end]
    return _clean(b / a - 1) if pd.notna(a) and pd.notna(b) and a > 0 else None


def _period_path(prices, tickers, entry, end, calendar):
    """Średni zwrot spółek (po równo) od kupna do każdej kolejnej sesji okresu."""
    days = calendar[(calendar >= entry) & (calendar <= end)]
    rel = []
    for t in tickers:
        s = prices.get(t)
        if s is None or entry not in s.index or not (s.loc[entry] > 0):
            continue
        rel.append(s.reindex(days) / s.loc[entry] - 1)
    if not rel:
        return pd.Series(dtype=float)
    return pd.concat(rel, axis=1).mean(axis=1, skipna=True)


def _costs(items):
    """Koszt każdego okresu liczony jak w paper.settle: 0.10% od każdej sprzedanej i kupionej spółki."""
    out, prev = [], []
    for s in items:
        changed = len(set(s["tickers"]) - set(prev)) + len(set(prev) - set(s["tickers"]))
        out.append(P.SIDE_COST * changed / P.K)
        prev = s["tickers"]
    return out


def equity_curve(journal, prices, calendar, all_tickers):
    """
    Wartość 10 000 zł dzień po dniu dla A1/A2/A3 i dla trzymania wszystkich spółek.
    Ostatni punkt każdego okresu zgadza się z rozliczeniem w dzienniku (te same ceny, ten sam koszt).
    """
    if calendar is None or not len(calendar) or not prices:
        return []
    cols = {}
    hold_done = False
    for strat in V.FROZEN:
        items = sorted([s for s in journal["sets"] if s["strategy"] == strat and s.get("return") is not None
                        and s.get("entry_date") and s.get("end_date")], key=lambda s: s["decision_date"])
        if not items:
            continue
        v, hv, line, hline = START_VALUE, START_VALUE, {}, {}
        for s, cost in zip(items, _costs(items)):
            entry, end = pd.Timestamp(s["entry_date"]), pd.Timestamp(s["end_date"])
            path = _period_path(prices, s["tickers"], entry, end, calendar)
            hpath = _period_path(prices, all_tickers, entry, end, calendar)
            for d in path.index:
                r = path.get(d)
                if d != entry and pd.notna(r):
                    line[d] = v * (1 + r - cost)
                h = hpath.get(d)
                if pd.notna(h):
                    hline[d] = hv * (1 + h)
            line.setdefault(entry, v)
            hline.setdefault(entry, hv)
            v *= 1 + s["return"]
            hv *= 1 + (s["hold_return"] or 0.0)
        cols[strat] = line
        if not hold_done:
            cols["hold"] = hline
            hold_done = True
    if not cols:
        return []
    df = pd.DataFrame(cols).sort_index()
    return [{"date": str(d.date()), **{k: _clean(df.at[d, k]) for k in df.columns}} for d in df.index]


def build_report(journal, prices, calendar, all_tickers):
    """Wszystko, co pokazuje zakładka QuantPulse. Bez pobierania i bez nauki - tylko liczenie z gotowych danych."""
    sets = journal.get("sets") or []
    summ = P.summary(journal)
    last_day = calendar[-1] if calendar is not None and len(calendar) else None
    last_decision = max((s["decision_date"] for s in sets), default=None)

    strategies = []
    for strat, cfg in V.FROZEN.items():
        items = sorted([s for s in sets if s["strategy"] == strat], key=lambda s: s["decision_date"])
        cur = items[-1] if items else None
        current = None
        if cur:
            entry = pd.Timestamp(cur["entry_date"]) if cur.get("entry_date") else None
            end = pd.Timestamp(cur["end_date"]) if cur.get("end_date") else None
            current = {
                "decision_date": cur["decision_date"], "entry_date": cur.get("entry_date"),
                "end_date": cur.get("end_date"), "status": cur.get("status", "czeka na kupno"),
                "return": _clean(cur.get("return")), "hold_return": _clean(cur.get("hold_return")),
                "backfilled": bool(cur.get("backfilled")),
                "tickers": [{"ticker": t, "return": _ticker_return(prices, t, entry, end)} for t in cur["tickers"]],
            }
        sm = summ.get(strat, {})
        strategies.append({
            "key": strat, "opis": cfg["opis"], "current": current,
            "value": _clean(sm.get("value")), "hold": _clean(sm.get("hold")),
            "periods": sm.get("periods", 0), "better": sm.get("better", 0),
            "closed": sum(1 for s in items if s.get("status") == "rozliczony"),
        })

    history = [{
        "strategy": s["strategy"], "decision_date": s["decision_date"], "entry_date": s.get("entry_date"),
        "end_date": s.get("end_date"), "status": s.get("status", "czeka na kupno"),
        "return": _clean(s.get("return")), "hold_return": _clean(s.get("hold_return")),
        "backfilled": bool(s.get("backfilled")), "tickers": s["tickers"],
    } for s in sorted(sets, key=lambda s: (s["decision_date"], s["strategy"]), reverse=True)]

    sessions_left = None
    if last_day is not None and last_decision:
        passed = (calendar.searchsorted(last_day, side="right")
                  - calendar.searchsorted(pd.Timestamp(last_decision), side="right"))
        sessions_left = max(int(P.H - passed), 0)

    return {
        "started": min((s["decision_date"] for s in sets), default=None),
        "last_session": str(last_day.date()) if last_day is not None else None,
        "last_decision": last_decision,
        "sessions_to_next": sessions_left,
        "every_sessions": P.H, "per_strategy": P.K, "start_value": START_VALUE,
        "strategies": strategies, "history": history,
        "curve": equity_curve(journal, prices, calendar, all_tickers),
    }


def vault_summary(lab_dir=None):
    """Wynik zapieczętowanego testu na historii (sejf) - dla porównania z grą."""
    v = load_json(os.path.join(lab_dir or F.lab_dir(), "vault.json"))
    if not v:
        return None
    res = v.get("results") or {}
    return {
        "period": v.get("period"), "hold_total": _clean(v.get("hold_total")),
        "results": {k: {"total": _clean(r.get("total")), "months_better": r.get("months_better"),
                        "months": r.get("months"), "max_drawdown": _clean(r.get("max_drawdown"))}
                    for k, r in res.items()},
    }


# ============================================================================
# ROZLICZENIE (ciężkie) - automat wieczorem, przycisk, terminal
# ============================================================================

class Busy(Exception):
    """Ktoś już liczy (automat, przycisk albo terminal)."""


class NoGame(Exception):
    """Dziennik jest pusty - gra jest prowadzona na innym komputerze."""


def refresh(store=None, update=True, trigger="terminal", out=None, journal=None, lab_dir=None,
            require_game=True, ds=None):
    """
    Pełne rozliczenie: notowania -> cechy -> paper.run (rozliczenie + ewentualne nowe typy) -> raport.
    require_game=True: przy pustym dzienniku NIE zaczyna nowej gry (ochrona laptopa).
    Zwraca zapisany status. Rzuca Busy / NoGame.
    """
    journal = journal or P.journal_path()
    spath = status_path(lab_dir)
    lines = []

    def say(msg):
        lines.append(str(msg))
        if out:
            out(msg)

    if require_game and not (P.load_journal(journal).get("sets")):
        raise NoGame("Dziennik gry jest pusty - gra na papierze jest prowadzona na komputerze stacjonarnym.")
    if not acquire_lock(lab_dir, who=trigger):
        raise Busy("Rozliczanie już trwa - poczekaj, aż się skończy.")

    started = datetime.now().isoformat(timespec="seconds")
    prev = load_json(spath) or {}
    status = {"report": prev.get("report"), "last_run": None, "last_ok": prev.get("last_ok")}
    try:
        if os.path.exists(journal):
            shutil.copy2(journal, backup_path(journal))
        if store is None:
            from .data_store import MarketStore
            store = MarketStore()
        if ds is None:
            if update:
                say("Pobieram najnowsze notowania…")
                store.update(progress=lambda *_: None)
            say("Liczę cechy…")
            ds = F.build_dataset(store, progress=lambda *_: None)
            if update:
                F.save_dataset(ds)
        j = P.run(store, update=False, out=say, path=journal, ds=ds)

        pl = sorted(ds.loc[ds["market"] == "PL", "ticker"].unique())
        calendar = pd.DatetimeIndex(sorted(ds.loc[ds["market"] == "PL", "date"].unique()))
        status["report"] = build_report(j, P.load_prices(store, pl), calendar, pl)
        finished = datetime.now().isoformat(timespec="seconds")
        status["last_run"] = {"started": started, "finished": finished, "ok": True, "trigger": trigger,
                              "error": None, "log": lines[-80:]}
        status["last_ok"] = finished
        save_json(spath, status)
    except Exception as e:
        logger.exception("QuantPulse: rozliczenie gry na papierze nie powiodło się")
        status["last_run"] = {"started": started, "finished": datetime.now().isoformat(timespec="seconds"),
                              "ok": False, "trigger": trigger, "error": f"{type(e).__name__}: {e}",
                              "log": lines[-80:]}
        save_json(spath, status)
        raise
    finally:
        release_lock(lab_dir)
    return status
