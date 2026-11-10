"""Podgląd gry "na dziś": liczy jak rozliczenie, nic nie zapisuje, rozumie typy czekające na kupno."""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from lab import paper as P
from lab import paper_live as L

DAYS = pd.bdate_range("2026-11-02", periods=7)          # pon 2.11 ... wt 10.11
UNI = ["U1.WA", "U2.WA", "U3.WA", "U4.WA"]
PX = {t: 100 * np.cumprod(1 + 0.01 * (i + 1) * np.array([0, 1, -1, 2, 1, -2, 3])) for i, t in enumerate(UNI)}


def fake_download(holiday=None):
    def dl(symbols, start):
        cols, data = [], {}
        for t in symbols:
            if t not in PX:
                continue
            vol = np.full(len(DAYS), 1000.0)
            if holiday is not None:
                vol[holiday] = 0
            for field, vals in (("Open", PX[t]), ("High", PX[t]), ("Low", PX[t]), ("Close", PX[t]),
                                ("Adj Close", PX[t]), ("Volume", vol)):
                cols.append((t, field))
                data[(t, field)] = vals
        return pd.DataFrame(data, index=DAYS, columns=pd.MultiIndex.from_tuples(cols))
    return dl


def journal(*sets):
    return {"sets": [dict(capital=20000, tickers=s[2], strategy=s[0], decision_date=str(DAYS[s[1]].date()), **s[3])
                     if len(s) > 3 else dict(capital=20000, tickers=s[2], strategy=s[0],
                                             decision_date=str(DAYS[s[1]].date())) for s in sets]}


def rel(t, a, b):
    return PX[t][b] / PX[t][a]


def test_returns_like_settlement():
    j = journal(("A1", 1, ["U1.WA", "U2.WA"]))
    r = L.live_check(j, download_fn=fake_download(), now=datetime(2026, 11, 10, 20, 0), universe=UNI)
    a1 = next(s for s in r["strategies"] if s["key"] == "A1")["held"]
    assert a1["entry_date"] == str(DAYS[2].date()) and r["last_session"] == str(DAYS[6].date())
    gross = np.mean([rel("U1.WA", 2, 6), rel("U2.WA", 2, 6)]) - 1
    assert a1["return"] == pytest.approx(gross - P.SIDE_COST * 2 / P.K)
    today = np.mean([rel(t, 2, 6) for t in ("U1.WA", "U2.WA")]) / np.mean([rel(t, 2, 5) for t in ("U1.WA", "U2.WA")]) - 1
    assert a1["today"] == pytest.approx(today)
    assert a1["hold_return"] == pytest.approx(np.mean([rel(t, 2, 6) for t in UNI]) - 1)
    assert a1["value"] == pytest.approx(10000 * (1 + a1["return"]))
    t = next(x for x in a1["tickers"] if x["ticker"] == "U1.WA")
    assert t["return"] == pytest.approx(rel("U1.WA", 2, 6) - 1) and t["today"] == pytest.approx(rel("U1.WA", 5, 6) - 1)
    assert r["session_open"] is False


def test_new_picks_wait_while_old_ones_are_still_held():
    j = journal(("A1", 1, ["U1.WA", "U2.WA"], {"status": "otwarty", "return": 0.0}), ("A1", 6, ["U3.WA", "U4.WA"]))
    r = L.live_check(j, download_fn=fake_download(), now=datetime(2026, 11, 10, 12, 0), universe=UNI)
    a1 = r["strategies"][0]
    assert a1["held"]["decision_date"] == str(DAYS[1].date())
    assert a1["pending"]["tickers"] == ["U3.WA", "U4.WA"]
    assert r["session_open"] is True                                    # wtorek 12:00, sesja trwa


def test_value_chains_settled_months_and_hides_unsettled():
    settled = ("A2", 0, ["U1.WA"], {"status": "rozliczony", "return": 0.10, "hold_return": 0.05})
    j = journal(settled, ("A2", 2, ["U2.WA"]))
    a2 = L.live_check(j, download_fn=fake_download(), now=datetime(2026, 11, 10, 20), universe=UNI)["strategies"][0]["held"]
    cost = P.SIDE_COST * 2 / P.K                                         # U1 sprzedana, U2 kupiona
    assert a2["return"] == pytest.approx(rel("U2.WA", 3, 6) - 1 - cost)
    assert a2["value"] == pytest.approx(10000 * 1.10 * (1 + a2["return"]))
    unsettled = ("A2", 0, ["U1.WA"], {"status": "otwarty", "return": 0.02})
    a2 = L.live_check(journal(unsettled, ("A2", 2, ["U2.WA"])), download_fn=fake_download(),
                      now=datetime(2026, 11, 10, 20), universe=UNI)["strategies"][0]["held"]
    assert a2["value"] is None and a2["return"] is not None


def test_holiday_rows_without_volume_are_ignored():
    j = journal(("A1", 1, ["U1.WA"]))
    r = L.live_check(j, download_fn=fake_download(holiday=2), now=datetime(2026, 11, 10, 20), universe=UNI)
    assert r["strategies"][0]["held"]["entry_date"] == str(DAYS[3].date())   # dzień 2 bez handlu -> kupno dzień 3


def test_empty_journal_and_no_data():
    assert L.live_check({"sets": []}, download_fn=fake_download()) is None
    with pytest.raises(RuntimeError):
        L.live_check(journal(("A1", 1, ["U1.WA"])), download_fn=lambda s, st: pd.DataFrame(), universe=UNI)
