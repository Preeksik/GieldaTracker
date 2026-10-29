"""Testy magazynu notowań - bez internetu: Yahoo zastępuje sztuczna funkcja pobierająca."""

import numpy as np
import pandas as pd
import pytest

from lab import data_store as ds
from lab import universe as U


def make_prices(days, start_price=100.0, seed=0, start="2016-01-04"):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=days)
    close = start_price * np.cumprod(1 + rng.normal(0, 0.01, days))
    open_ = close * (1 + rng.normal(0, 0.003, days))
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                         "Adj Close": close * 0.95, "Volume": rng.integers(1000, 9000, days).astype(float)},
                        index=idx)


class FakeYahoo:
    """Udaje yf.download(group_by='ticker'): zwraca kolumny (symbol, pole) od daty `start`."""

    def __init__(self, data, fail=()):
        self.data = data          # symbol -> DataFrame w formacie Yahoo
        self.fail = set(fail)
        self.calls = []

    def __call__(self, symbols, start):
        self.calls.append((tuple(symbols), start))
        parts = {}
        for s in symbols:
            if s in self.fail:
                raise RuntimeError("boom")
            df = self.data.get(s)
            if df is not None:
                parts[s] = df[df.index >= pd.Timestamp(start)]
        if not parts:
            return pd.DataFrame()
        return pd.concat(parts, axis=1)


@pytest.fixture
def small_universe(monkeypatch):
    monkeypatch.setattr(U, "GPW", ["PKO.WA", "CDR.WA"])
    monkeypatch.setattr(U, "USA", ["AAPL", "ZZZZ"])           # ZZZZ - Yahoo go nie zna
    monkeypatch.setattr(U, "INDEXES", {"WIG20": ["WIG20.WA", "^WIG20"], "SP500": ["^GSPC"]})
    monkeypatch.setattr(U, "FX", {"USDPLN": ["USDPLN=X"]})
    monkeypatch.setattr(U, "BENCHMARK", {"PL": "WIG20", "US": "SP500"})


def full_data(days=300):
    return {"PKO.WA": make_prices(days, 50, 1), "CDR.WA": make_prices(days, 120, 2),
            "AAPL": make_prices(days, 150, 3), "^WIG20": make_prices(days, 2000, 4),
            "^GSPC": make_prices(days, 4000, 5), "USDPLN=X": make_prices(days, 4, 6)}


def store(tmp_path, fake, today="2017-03-01", parquet=None):
    return ds.MarketStore(root=str(tmp_path), download_fn=fake, sleep=lambda s: None,
                          today=today, parquet=parquet)


@pytest.mark.parametrize("parquet", [True, False])
def test_first_download_and_status(tmp_path, small_universe, parquet):
    if parquet and not ds.HAS_PARQUET:
        pytest.skip("brak pyarrow")
    data = full_data()
    st = store(tmp_path, FakeYahoo(data), parquet=parquet)
    rep = st.update(progress=lambda *_: None)

    assert sorted(rep["nowe"]) == ["AAPL", "CDR.WA", "PKO.WA", "SP500", "USDPLN", "WIG20"]
    assert rep["puste"] == ["ZZZZ"]
    df = st.load("PKO.WA")
    assert len(df) == 300 and list(df.columns) == ds.PRICE_COLS + ["quality"]
    assert np.allclose(df["close"].to_numpy(), data["PKO.WA"]["Close"].to_numpy())
    # WIG20.WA nie istnieje w danych -> użyty drugi kandydat, zapamiętany w meta
    assert st.meta["WIG20"]["symbol"] == "^WIG20"

    s = st.status()
    assert s["instrumentow"] == 6 and s["spolek_pl"] == 2 and s["spolek_us"] == 1
    assert s["bez_danych"] == ["ZZZZ"] and s["nieaktualne"] == []


def test_incremental_update_appends_new_days(tmp_path, small_universe):
    data = full_data(300)
    st = store(tmp_path, FakeYahoo({k: v.iloc[:250] for k, v in data.items()}))
    st.update(progress=lambda *_: None)
    assert len(st.load("AAPL")) == 250

    fake = FakeYahoo(data)
    st2 = store(tmp_path, fake)
    rep = st2.update(progress=lambda *_: None)
    assert "AAPL" in rep["uzupelnione"] and not rep["pelne_odswiezenie"]
    assert len(st2.load("AAPL")) == 300
    # przyrostowo pytamy tylko o końcówkę, nie o 10 lat
    last_saved = data["AAPL"].index[249]
    inc_starts = [pd.Timestamp(start) for syms, start in fake.calls if "AAPL" in syms]
    assert inc_starts and all(s > last_saved - pd.Timedelta(days=30) for s in inc_starts)


def test_no_new_days_is_reported_as_unchanged(tmp_path, small_universe):
    data = full_data(200)
    st = store(tmp_path, FakeYahoo(data))
    st.update(progress=lambda *_: None)
    rep = store(tmp_path, FakeYahoo(data)).update(progress=lambda *_: None)
    assert "PKO.WA" in rep["bez_zmian"] and not rep["uzupelnione"]


def test_dividend_rewrite_triggers_full_refresh(tmp_path, small_universe):
    data = full_data(300)
    st = store(tmp_path, FakeYahoo({k: v.iloc[:250] for k, v in data.items()}))
    st.update(progress=lambda *_: None)

    changed = dict(data)
    cdr = data["CDR.WA"].copy()
    cdr["Adj Close"] = cdr["Adj Close"] * 0.97      # Yahoo przeliczyło całą historię po dywidendzie
    changed["CDR.WA"] = cdr
    st2 = store(tmp_path, FakeYahoo(changed))
    rep = st2.update(progress=lambda *_: None)

    assert rep["pelne_odswiezenie"] == ["CDR.WA"]
    got = st2.load("CDR.WA")
    assert len(got) == 300
    assert np.allclose(got["adj_close"].to_numpy(), cdr["Adj Close"].to_numpy())


def test_download_error_keeps_old_data(tmp_path, small_universe):
    data = full_data(250)
    st = store(tmp_path, FakeYahoo(data))
    st.update(progress=lambda *_: None)
    rep = store(tmp_path, FakeYahoo(data, fail={"PKO.WA"})).update(progress=lambda *_: None)
    # błąd w paczce psuje całą paczkę spółek - ale niczego nie kasuje
    assert "PKO.WA" in rep["bledy"]
    assert len(store(tmp_path, FakeYahoo({})).load("PKO.WA")) == 250


def test_quality_flags():
    df = make_prices(10).rename(columns=ds.FIELD_MAP)
    df.loc[df.index[2], "close"] = np.nan                       # brak ceny
    df.loc[df.index[4], "volume"] = 0                           # zerowy wolumen
    df.loc[df.index[6], ["close", "high"]] = df["close"].iloc[5] * 1.8   # szpilka, wraca nast. dnia
    df.loc[df.index[8], "low"] = df["high"].iloc[8] * 1.2       # min. > maks.
    q = ds.quality_flags(df)
    assert q[2] & ds.Q_MISSING
    assert q[4] & ds.Q_ZERO_VOL and not q[4] & ds.BAD            # zerowy wolumen to tylko informacja
    assert q[6] & ds.Q_SPIKE
    assert q[8] & ds.Q_RANGE
    assert q[0] == 0 and q[1] == 0
    assert ds.describe_flags(ds.Q_SPIKE | ds.Q_MISSING) == ["brak ceny", "szpilka"]


def test_real_trend_is_not_a_spike():
    df = make_prices(6).rename(columns=ds.FIELD_MAP)
    # +50% i cena zostaje (np. wezwanie) - to nie błąd
    for i in range(3, 6):
        df.iloc[i, :5] = df.iloc[i, :5] * 1.5
    assert not (ds.quality_flags(df) & ds.Q_SPIKE).any()


def test_extract_handles_flat_and_swapped_columns():
    raw = make_prices(5)
    assert ds.extract(raw, "AAPL") is not None                                  # płaskie kolumny
    swapped = pd.concat({"AAPL": raw}, axis=1).swaplevel(0, 1, axis=1)          # (pole, symbol)
    out = ds.extract(swapped, "AAPL")
    assert out is not None and list(out.columns) == ds.PRICE_COLS
    assert ds.extract(pd.DataFrame(), "AAPL") is None
    tz = raw.copy()
    tz.index = tz.index.tz_localize("Europe/Warsaw")
    assert ds.extract(tz, "AAPL").index.tz is None


def test_gaps_against_index_calendar(tmp_path, small_universe):
    data = full_data(200)
    data["CDR.WA"] = data["CDR.WA"].drop(data["CDR.WA"].index[50:60])   # 10 brakujących sesji
    st = store(tmp_path, FakeYahoo(data))
    st.update(progress=lambda *_: None)
    assert st.meta["CDR.WA"]["gaps"] == 10 and st.meta["PKO.WA"]["gaps"] == 0
    assert st.status()["dziury"] == {"CDR.WA": 10}


def test_no_connection_does_not_blacklist_symbols(tmp_path, small_universe):
    st = store(tmp_path, FakeYahoo({}))          # wszystko puste = brak internetu
    rep = st.update(progress=lambda *_: None)
    assert rep.get("brak_polaczenia") and not rep["puste"]
    assert st.status()["bez_danych"] == []
