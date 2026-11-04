"""Gra na papierze: typy co 20 sesji, rozliczenie liczone ręcznie, podróż w czasie bez zaglądania w przyszłość."""
import numpy as np
import pandas as pd
import pytest

from lab import paper as P
from lab import vault as V
from lab.data_store import MarketStore
from lab.tests.test_features import ohlcv

N = 760
TICKERS = [f"P{i:02d}.WA" for i in range(40)]


def full_series():
    rng = np.random.default_rng(21)
    s = {t: ("stock", ohlcv(rng, N, 20 + i)) for i, t in enumerate(TICKERS)}
    s["WIG20"] = ("index", ohlcv(rng, N, 2000, 0.01))
    s["VIX"] = ("index", ohlcv(rng, N, 18, 0.05))
    s["P05.WA"][1].loc[:, ["open", "high", "low", "close", "adj_close"]] *= 1000      # bardzo droga akcja
    return s


SERIES = full_series()
DATES = SERIES["P00.WA"][1].index


def store_until(root, day):
    """Baza notowań 'na dzień' day - jakby reszta przyszłości jeszcze się nie wydarzyła."""
    st = MarketStore(root=str(root), download_fn=lambda *a: None, sleep=lambda s: None)
    for name, (kind, df) in SERIES.items():
        st._store(name, kind, df[df.index <= day], name)
    return st


def run_on(tmp_path, i, journal):
    st = store_until(tmp_path / f"s{i}", DATES[i])
    lines = []
    j = P.run(st, update=False, out=lines.append, path=str(journal))
    return j, "\n".join(lines)


def test_first_run_picks_and_cadence(tmp_path):
    jp = tmp_path / "journal.json"
    j, text = run_on(tmp_path, 600, jp)
    sets = j["sets"]
    assert {s["strategy"] for s in sets} == set(V.FROZEN) and all(len(s["tickers"]) == 10 for s in sets)
    assert all("P05.WA" not in s["tickers"] for s in sets)           # za droga na 2 000 zł na spółkę
    assert "NOWE TYPY" in text and sets[0]["decision_date"] == str(DATES[600].date())

    j, text = run_on(tmp_path, 605, jp)                               # 5 sesji później - bez nowych typów
    assert len(j["sets"]) == 3 and "Następna zmiana składu za 15 sesji" in text and "NOWE TYPY" not in text

    j, text = run_on(tmp_path, 620, jp)                               # 20 sesji - nowe typy
    assert len(j["sets"]) == 6 and "NOWE TYPY" in text


def test_settlement_by_hand(tmp_path):
    jp = tmp_path / "journal.json"
    run_on(tmp_path, 600, jp)
    run_on(tmp_path, 620, jp)
    j, text = run_on(tmp_path, 640, jp)
    first = next(s for s in j["sets"] if s["strategy"] == "A1" and s["decision_date"] == str(DATES[600].date()))
    second = next(s for s in j["sets"] if s["strategy"] == "A1" and s["decision_date"] == str(DATES[620].date()))
    assert first["status"] == "rozliczony"
    entry, exit_ = DATES[601], DATES[621]                             # zamknięcie sesji PO wskazaniu
    rets = [SERIES[t][1]["adj_close"][exit_] / SERIES[t][1]["adj_close"][entry] - 1 for t in first["tickers"]]
    assert first["return"] == pytest.approx(np.mean(rets) - P.SIDE_COST * 10 / 10, abs=1e-12)
    changed = len(set(second["tickers"]) ^ set(first["tickers"]))
    rets2 = [SERIES[t][1]["adj_close"][DATES[640]] / SERIES[t][1]["adj_close"][DATES[621]] - 1 for t in second["tickers"]]
    assert second["status"] == "otwarty"
    assert second["return"] == pytest.approx(np.mean(rets2) - P.SIDE_COST * changed / 10, abs=1e-12)
    hold = [SERIES[t][1]["adj_close"][exit_] / SERIES[t][1]["adj_close"][entry] - 1 for t in TICKERS]
    assert first["hold_return"] == pytest.approx(np.mean(hold), abs=1e-12)
    assert "Od startu gry" in text and "A3" in text


def test_missed_months_are_backfilled_exactly_as_if_run_on_time(tmp_path):
    """Program uruchomiony z opóźnieniem dorabia typy z pominiętych dni - identyczne jak przy uruchomieniu na czas."""
    on_time = tmp_path / "on_time.json"
    for i in (600, 620, 640):
        j_ok, _ = run_on(tmp_path / f"a{i}", i, on_time)
    late = tmp_path / "late.json"
    run_on(tmp_path / "b600", 600, late)
    j_late, text = run_on(tmp_path / "b645", 645, late)            # przerwa: nikt nie uruchomił 620 ani 640
    key = lambda j: {(s["strategy"], s["decision_date"]): s["tickers"] for s in j["sets"]}
    assert key(j_late) == key(j_ok)
    back = {s["decision_date"] for s in j_late["sets"] if s.get("backfilled")}
    assert back == {str(DATES[620].date()), str(DATES[640].date())}
    assert "Dorobiono wstecz" in text
    first = next(s for s in j_late["sets"] if s["strategy"] == "A2" and s["decision_date"] == str(DATES[600].date()))
    assert first["status"] == "rozliczony" and first["end_date"] == str(DATES[621].date())   # dokładnie 20 sesji
