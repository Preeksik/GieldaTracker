"""Raport gry na papierze dla aplikacji: wykres zgodny z rozliczeniem, blokada, kopia dziennika, ochrona laptopa."""
import json
import os

import numpy as np
import pytest

from lab import paper as P
from lab import paper_report as R
from lab.tests.test_paper import DATES, SERIES, store_until


def refresh_on(tmp_path, i, journal, **kw):
    st = store_until(tmp_path / f"s{i}", DATES[i])
    kw.setdefault("require_game", False)
    return R.refresh(st, update=False, trigger="test", journal=str(journal), lab_dir=str(tmp_path / "lab"), **kw)


def test_report_and_curve_match_settlement(tmp_path):
    jp = tmp_path / "journal.json"
    for i in (600, 620):
        refresh_on(tmp_path, i, jp)
    status = refresh_on(tmp_path, 640, jp)
    rep = status["report"]
    assert status["last_run"]["ok"] and status["last_ok"]
    assert rep["started"] == str(DATES[600].date()) and rep["last_session"] == str(DATES[640].date())
    assert rep["sessions_to_next"] == 20 and len(rep["history"]) == 9       # 640 = trzeci zestaw typów

    curve = rep["curve"]
    assert curve[0]["date"] == str(DATES[601].date())                       # start: kupno po pierwszym wskazaniu
    assert all(curve[0][k] == pytest.approx(10000) for k in ("A1", "A2", "A3", "hold"))
    for s in rep["strategies"]:
        assert curve[-1][s["key"]] == pytest.approx(s["value"], rel=1e-9)   # ostatni punkt = wynik w dzienniku
        assert curve[-1]["hold"] == pytest.approx(s["hold"], rel=1e-9)
        assert s["closed"] == 1 and s["periods"] == 2        # 600 rozliczony, 620 otwarty, 640 czeka

    # wynik pojedynczej spółki w bieżącym (otwartym) okresie - ręcznie
    j = json.loads(jp.read_text(encoding="utf-8"))
    second = next(s for s in j["sets"] if s["strategy"] == "A1" and s["decision_date"] == str(DATES[620].date()))
    cur = next(s for s in rep["history"] if s["strategy"] == "A1" and s["decision_date"] == str(DATES[620].date()))
    assert cur["status"] == "otwarty" and cur["tickers"] == second["tickers"]


def test_open_period_ticker_returns(tmp_path):
    jp = tmp_path / "journal.json"
    refresh_on(tmp_path, 600, jp)
    rep = refresh_on(tmp_path, 610, jp)["report"]
    a1 = next(s for s in rep["strategies"] if s["key"] == "A1")["current"]
    assert a1["status"] == "otwarty" and rep["sessions_to_next"] == 10
    t = a1["tickers"][0]
    px = SERIES[t["ticker"]][1]["adj_close"]
    assert t["return"] == pytest.approx(px[DATES[610]] / px[DATES[601]] - 1, abs=1e-12)
    # średnia spółek minus koszt kupna = wynik okresu
    mean = np.mean([x["return"] for x in a1["tickers"]])
    assert a1["return"] == pytest.approx(mean - P.SIDE_COST, abs=1e-12)


def test_waiting_for_buy_has_no_curve(tmp_path):
    rep = refresh_on(tmp_path, 600, tmp_path / "journal.json")["report"]
    assert rep["curve"] == []
    assert all(s["current"]["status"] == "czeka na kupno" for s in rep["strategies"])


def test_empty_journal_does_not_start_new_game(tmp_path):
    jp = tmp_path / "journal.json"
    with pytest.raises(R.NoGame):
        refresh_on(tmp_path, 600, jp, require_game=True)
    assert not jp.exists()


def test_busy_when_locked_and_lock_released_after(tmp_path):
    jp = tmp_path / "journal.json"
    lab = str(tmp_path / "lab")
    assert R.acquire_lock(lab, who="ktoś")
    with pytest.raises(R.Busy):
        refresh_on(tmp_path, 600, jp)
    R.release_lock(lab)
    refresh_on(tmp_path, 600, jp)
    assert not os.path.exists(R.lock_path(lab))


def test_backup_before_write_and_failure_is_recorded(tmp_path):
    jp = tmp_path / "journal.json"
    refresh_on(tmp_path, 600, jp)
    before = jp.read_text(encoding="utf-8")
    with pytest.raises(Exception):
        refresh_on(tmp_path, 620, jp, ds="to nie jest tabela")             # coś się wysypuje w trakcie
    assert (tmp_path / "journal.backup.json").read_text(encoding="utf-8") == before
    st = json.loads((tmp_path / "lab" / "paper_status.json").read_text(encoding="utf-8"))
    assert st["last_run"]["ok"] is False and st["report"] is not None    # stary raport zostaje
    assert not os.path.exists(R.lock_path(str(tmp_path / "lab")))


def test_incomplete_last_session_is_reported(tmp_path):
    """Yahoo zwraca ostatnią sesję bez ceny zamknięcia -> raport mówi, że wynik nie obejmuje najnowszych dni."""
    jp = tmp_path / "journal.json"
    refresh_on(tmp_path, 600, jp)
    st = store_until(tmp_path / "s610", DATES[610])
    for name in list(st.meta):
        if name.endswith(".WA"):
            df = st.load(name)
            df.loc[DATES[610], ["close", "adj_close"]] = np.nan
            st._store(name, "stock", df, name)
    rep = R.refresh(st, update=False, trigger="test", journal=str(jp), lab_dir=str(tmp_path / "lab"),
                    require_game=False)["report"]
    assert rep["prices_last_session"] == str(DATES[610].date())
    assert rep["last_session"] < rep["prices_last_session"]
    assert len(rep["incomplete_tickers"]) > 30


def test_complete_data_has_no_gap(tmp_path):
    rep = refresh_on(tmp_path, 600, tmp_path / "journal.json")["report"]
    assert rep["prices_last_session"] == rep["last_session"] and rep["incomplete_tickers"] == []
