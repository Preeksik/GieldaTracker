"""
QuantPulse - szybki podgląd gry na papierze "na dziś".

Pobiera z Yahoo świeże ceny (także w trakcie sesji, z opóźnieniem ok. 15 min) tylko dla spółek
z bieżących typów i listy GPW (punkt odniesienia "trzymanie"), i liczy, jak idzie A1/A2/A3 dzisiaj
i od kupna. Trwa kilka sekund.

NIC NIE ZAPISUJE: dziennik i raport zostają bez zmian. Oficjalny wynik to nadal wieczorne
rozliczenie (paper_report.refresh) - podgląd w trakcie sesji może się do wieczora zmienić.
Liczy tak samo jak paper.settle: kupno na zamknięciu sesji po wskazaniu, ceny skorygowane
o dywidendy, koszt 0.10% od każdej kupionej i sprzedanej spółki.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from . import paper as P
from . import universe as U
from . import vault as V
from .data_store import extract, yahoo_download

START_VALUE = 10000.0
SESSION_OPEN, SESSION_CLOSE = (9, 0), (17, 35)   # GPW, czas polski (z zapasem na fixing)


def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def _series(raw, symbols):
    """ticker -> ceny zamknięcia skorygowane (tylko dni z handlem: wolumen > 0 - bez fałszywych świąt)."""
    out = {}
    for t in symbols:
        part = extract(raw, t)
        if part is None:
            continue
        part = part.dropna(subset=["adj_close"])
        if "volume" in part.columns:
            part = part[part["volume"].fillna(0) > 0]
        if len(part):
            s = part["adj_close"].astype(float)
            s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
            out[t] = s[~s.index.duplicated(keep="last")]
    return out


def _sessions(prices):
    """Dni sesji: data, w której notowania ma co najmniej połowa spółek."""
    if not prices:
        return pd.DatetimeIndex([])
    counts = pd.concat([pd.Series(1, index=s.index) for s in prices.values()], axis=1).sum(axis=1)
    return pd.DatetimeIndex(sorted(counts[counts >= max(1, len(prices) / 2)].index))


def _basket(prices, tickers, entry, prev, last):
    """Koszyk po równo kupiony na zamknięciu `entry`: zwrot od kupna, ruch dzisiaj, wyniki spółek."""
    rows, rel_last, rel_prev = [], [], []
    for t in tickers:
        s = prices.get(t)
        pe = s.get(entry) if s is not None else None
        pl = s.get(last) if s is not None else None
        pp = s.get(prev) if (s is not None and prev is not None) else None
        r = _num(pl / pe - 1) if pe and pl else None
        today = _num(pl / pp - 1) if (pp and pl and prev is not None and prev >= entry) else None
        rows.append({"ticker": t, "price": _num(pl), "return": r, "today": today})
        if pe and pl:
            rel_last.append(pl / pe)
            if pp:
                rel_prev.append(pp / pe)
    ret = float(np.mean(rel_last) - 1) if rel_last else None
    day = None
    if prev is not None and prev >= entry and rel_prev and len(rel_prev) == len(rel_last):
        day = float(np.mean(rel_last) / np.mean(rel_prev) - 1)
    return ret, day, rows


def session_open(now, last_session):
    """Czy dzisiejsza sesja GPW jeszcze trwa (wtedy ceny się zmieniają)."""
    if now.weekday() >= 5 or last_session is None or last_session.date() != now.date():
        return False
    return SESSION_OPEN <= (now.hour, now.minute) < SESSION_CLOSE


def live_check(journal, download_fn=yahoo_download, now=None, universe=None):
    now = now or datetime.now()
    sets = journal.get("sets") or []
    if not sets:
        return None
    universe = list(universe or U.GPW)
    by_strat = {k: sorted([s for s in sets if s["strategy"] == k], key=lambda s: s["decision_date"])
                for k in V.FROZEN}
    first = min(s["decision_date"] for s in sets)
    # wystarczy kilka tygodni wstecz: od najstarszego zestawu, który może być jeszcze trzymany
    recent = [s["decision_date"] for items in by_strat.values() for s in items[-2:]]
    start = (pd.Timestamp(min(recent)) - timedelta(days=10)).date().isoformat()
    symbols = sorted(set(universe) | {t for items in by_strat.values() for s in items[-2:] for t in s["tickers"]})

    prices = _series(download_fn(symbols, start), symbols)
    cal = _sessions(prices)
    if len(cal) == 0:
        raise RuntimeError("Yahoo nie zwróciło notowań - spróbuj za chwilę.")
    last = cal[-1]
    prev = cal[-2] if len(cal) > 1 else None

    def entry_of(s):
        i = cal.searchsorted(pd.Timestamp(s["decision_date"]), side="right")
        return cal[i] if i < len(cal) else None

    out = []
    for key, items in by_strat.items():
        if not items:
            continue
        # trzymany = ostatni zestaw, który już kupiono; nowszy bez kupna czeka na zamknięcie sesji
        held = next((s for s in reversed(items) if entry_of(s) is not None and entry_of(s) <= last), None)
        pending = items[-1] if items[-1] is not held else None
        row = {"key": key, "opis": V.FROZEN[key]["opis"], "pending": None, "held": None}
        if pending is not None:
            row["pending"] = {"decision_date": pending["decision_date"], "tickers": pending["tickers"]}
        if held is not None:
            entry = entry_of(held)
            i = items.index(held)
            prev_tickers = items[i - 1]["tickers"] if i > 0 else []
            changed = len(set(held["tickers"]) ^ set(prev_tickers))
            cost = P.SIDE_COST * changed / P.K
            ret, day, rows = _basket(prices, held["tickers"], entry, prev, last)
            hret, hday, _ = _basket(prices, universe, entry, prev, last)
            # wartość od startu gry: rozliczone wcześniej miesiące z dziennika x bieżący okres na żywo
            v = h = START_VALUE
            settled = all(s.get("status") == "rozliczony" and s.get("return") is not None for s in items[:i])
            for s in items[:i]:
                if settled:
                    v *= 1 + s["return"]
                    h *= 1 + (s.get("hold_return") or 0.0)
            net = None if ret is None else ret - cost
            if not settled:                  # poprzedni miesiąc nie jest jeszcze rozliczony w dzienniku
                v = h = None
            row["held"] = {
                "decision_date": held["decision_date"], "entry_date": str(entry.date()),
                "return": net, "hold_return": hret, "today": day, "hold_today": hday,
                "value": None if (net is None or v is None) else v * (1 + net),
                "hold_value": None if (hret is None or h is None) else h * (1 + hret),
                "bought_today": entry == last,
                "tickers": sorted(rows, key=lambda r: -(r["return"] if r["return"] is not None else -1e9)),
                "missing": [r["ticker"] for r in rows if r["return"] is None],
            }
        out.append(row)

    return {
        "checked_at": now.isoformat(timespec="seconds"),
        "last_session": str(last.date()),
        "prev_session": str(prev.date()) if prev is not None else None,
        "session_open": session_open(now, last),
        "started": first,
        "start_value": START_VALUE,
        "strategies": out,
    }
