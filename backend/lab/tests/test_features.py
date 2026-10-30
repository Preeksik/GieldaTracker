"""Testy cech i etykiet. Najważniejszy: żadna cecha z dnia t nie może korzystać z danych po t."""

import numpy as np
import pandas as pd
import pytest

from lab import features as F
from lab.data_store import MarketStore

N_DAYS = 420
PL = [f"S{i:02d}.WA" for i in range(18)]
US = ["AAA", "BBB", "CCC"]


def ohlcv(rng, days, start=100.0, vol=0.015):
    idx = pd.bdate_range("2020-01-01", periods=days)
    c = start * np.cumprod(1 + rng.normal(0.0002, vol, days))
    o = c * np.exp(rng.normal(0, vol / 2, days))
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.01, days))
    l = np.minimum(o, c) * (1 - rng.uniform(0, 0.01, days))
    v = rng.integers(10_000, 90_000, days).astype(float)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "adj_close": c * 0.98,
                         "volume": v}, index=pd.DatetimeIndex(idx, name="date"))


def make_store(tmp_path, edit=None):
    """Sztuczna baza: 18 spółek GPW, 3 z USA, WIG20, S&P 500, VIX. `edit(name, df) -> df` zmienia dane."""
    rng = np.random.default_rng(11)
    st = MarketStore(root=str(tmp_path), download_fn=lambda *a: None, sleep=lambda s: None)
    series = {n: ("stock", ohlcv(rng, N_DAYS, 50 + 3 * i)) for i, n in enumerate(PL + US)}
    series["WIG20"] = ("index", ohlcv(rng, N_DAYS, 2000, 0.01))
    series["SP500"] = ("index", ohlcv(rng, N_DAYS, 4000, 0.01))
    series["VIX"] = ("index", ohlcv(rng, N_DAYS, 18, 0.05))
    for name, (kind, df) in series.items():
        if edit:
            df = edit(name, df.copy())
        st._store(name, kind, df, name)
    return st


@pytest.fixture
def dataset(tmp_path):
    return F.build_dataset(make_store(tmp_path), progress=lambda *_: None)


def dates():
    return pd.bdate_range("2020-01-01", periods=N_DAYS)


def test_shape_and_ranges(dataset):
    ds = dataset
    assert set(F.FEATURES) <= set(ds.columns) and set(F.LABELS) <= set(ds.columns)
    tr = ds[ds["train_ok"]]
    assert len(tr) > 0 and not tr[F.FEATURES].isna().any().any()
    assert tr["mom_20"].between(-1, 1).all()
    assert tr["close_loc"].between(0, 1).all()
    assert tr["rsi_14"].between(0, 100).all()
    assert (tr["dist_high_250"] <= 1e-12).all()
    assert set(tr["dow"].unique()) <= {0, 1, 2, 3, 4}
    # rok na rozgrzewkę (odległość od rocznego szczytu) - wcześniej żadnych próbek
    assert tr["date"].min() >= dates()[249]


def test_labels_are_next_day_open_to_close(tmp_path, dataset):
    st = make_store(tmp_path / "x")
    raw = st.load("S03.WA")
    row = dataset[(dataset["ticker"] == "S03.WA") & dataset["train_ok"]].iloc[10]
    nxt = raw.index[raw.index.get_loc(row["date"]) + 1]
    assert row["y_oc"] == pytest.approx(np.log(raw.loc[nxt, "close"] / raw.loc[nxt, "open"]), abs=1e-12)
    # nadwyżka nad rynkiem: w dniu, w którym wszystkie spółki GPW są w tabeli, sumuje się do zera
    pl = dataset[(dataset["market"] == "PL") & dataset["train_ok"]]
    full_days = pl.groupby("date").filter(lambda g: len(g) == len(PL))
    sums = full_days.groupby("date")["y_oc_ex"].sum()
    assert len(sums) > 50 and np.allclose(sums, 0, atol=1e-12)
    assert ((full_days["y_up"] == 1) == (full_days["y_oc_ex"] > 0)).all()


def test_no_lookahead(tmp_path, dataset):
    """Cechy z dnia t są takie same, gdy usunie się wszystkie dane po t."""
    t = dates()[330]
    cut = F.build_dataset(make_store(tmp_path / "cut", edit=lambda n, df: df[df.index <= t]),
                          progress=lambda *_: None)
    a = dataset[dataset["date"] == t].set_index("ticker")[F.FEATURES].sort_index()
    b = cut[cut["date"] == t].set_index("ticker")[F.FEATURES].sort_index()
    assert len(a) == len(PL) + len(US) and list(a.index) == list(b.index)
    assert np.allclose(a.to_numpy(), b.to_numpy(), rtol=0, atol=1e-12, equal_nan=True)
    # dzień t w uciętych danych to "wiersz do prognozy" - bez etykiety
    assert cut.loc[cut["date"] == t, "predict_row"].all()
    assert cut.loc[cut["date"] == t, "y_oc"].isna().all()


def test_gpw_does_not_see_todays_vix(tmp_path, dataset):
    """GPW decyduje o 17:30, zanim zamknie się USA - VIX z dnia t nie może wpłynąć na cechy GPW z dnia t."""
    t = dates()[330]

    def spike_vix(name, df):
        if name == "VIX":
            df.loc[t, ["close", "adj_close", "high"]] *= 3
        return df

    alt = F.build_dataset(make_store(tmp_path / "vix", edit=spike_vix), progress=lambda *_: None)
    get = lambda d, mk: d[(d["date"] == t) & (d["market"] == mk)]["vix_chg_1"].to_numpy()
    assert np.allclose(get(dataset, "PL"), get(alt, "PL"))          # GPW: bez zmian
    assert not np.allclose(get(dataset, "US"), get(alt, "US"))      # USA: VIX z dnia t jest znany


def test_suspension_and_bad_days_are_excluded(tmp_path):
    d = dates()
    gap = d[300:306]                  # S05 zawieszona na 6 sesji
    spike = d[340]                    # S07: błędny wydruk ceny

    def edit(name, df):
        if name == "S05.WA":
            df = df.drop(gap)
        if name == "S07.WA":
            df.loc[spike, ["close", "high", "adj_close"]] *= 2.0
        return df

    ds = F.build_dataset(make_store(tmp_path, edit=edit), progress=lambda *_: None)
    s5 = ds[ds["ticker"] == "S05.WA"].set_index("date")
    assert d[299] not in s5.index or not s5.loc[d[299], "train_ok"]    # jutro nie ma sesji dla S05
    s7 = ds[(ds["ticker"] == "S07.WA") & ds["train_ok"]]["date"]
    blocked = d[339:340 + 21]          # dzień przed (etykieta) i 20 dni po (okno cech)
    assert not s7.isin(blocked).any()
    assert pd.Series(d[362:370]).isin(s7).all()   # potem wraca do gry


def test_rsi_extremes():
    up = pd.Series(np.arange(1.0, 40.0))
    assert F.rsi(up).iloc[-1] == 100
    assert F.rsi(up[::-1].reset_index(drop=True)).iloc[-1] == pytest.approx(0)
    assert F.rsi(pd.Series(np.full(40, 5.0))).iloc[-1] == 50


def test_daily_ic_detects_signal():
    rng = np.random.default_rng(0)
    rows = []
    for day in pd.bdate_range("2021-01-01", periods=60):
        y = rng.normal(size=30)
        for i in range(30):
            rows.append({"market": "PL", "date": day, "good": y[i] + rng.normal(0, 0.1),
                         "noise": rng.normal(), "y_oc_ex": y[i]})
    df = pd.DataFrame(rows)
    ic, t, n = F.daily_ic(df, "good")
    assert ic > 0.9 and t > 10 and n == 60
    ic, t, _ = F.daily_ic(df, "noise")
    assert abs(ic) < 0.1 and abs(t) < 3


def test_report_and_save_roundtrip(tmp_path, dataset):
    lines = []
    F.report(dataset, out=lines.append)
    assert any("Próbek do nauki" in l for l in lines)
    path = F.save_dataset(dataset, root=str(tmp_path / "lab"))
    back = F.load_dataset(root=str(tmp_path / "lab"))
    assert path and len(back) == len(dataset)


def test_dividend_day_has_no_fake_gap(tmp_path, dataset):
    """Dywidenda: Yahoo koryguje adj_close wstecz, ale nie otwarcia - luka nie może pokazać spadku o dywidendę."""
    d = dates()
    ex = d[330]

    def dividend(name, df):
        if name == "S02.WA":
            # 5% dywidendy: wszystkie ceny od dnia odcięcia niżej o 5%, adj_close historii przeliczone wstecz
            k = df.index >= ex
            df.loc[k, ["open", "high", "low", "close"]] *= 0.95
            df["adj_close"] = df["close"] * np.where(k, 1.0, 0.95) * 0.98
        return df

    alt = F.build_dataset(make_store(tmp_path / "div", edit=dividend), progress=lambda *_: None)
    get = lambda ds: ds[(ds["ticker"] == "S02.WA") & (ds["date"] == ex)]["gap"].iloc[0]
    assert get(alt) == pytest.approx(get(dataset), abs=1e-12)


def test_bad_day_does_not_poison_year_high(tmp_path):
    d = dates()

    def spike(name, df):
        if name == "S04.WA":
            df.loc[d[300], ["close", "high", "adj_close"]] *= 3.0     # błędny wydruk, wraca następnego dnia
        return df

    clean = F.build_dataset(make_store(tmp_path / "a"), progress=lambda *_: None)
    dirty = F.build_dataset(make_store(tmp_path / "b", edit=spike), progress=lambda *_: None)
    pick = lambda ds: ds[(ds["ticker"] == "S04.WA") & (ds["date"] > d[330])].set_index("date")["dist_high_250"]
    a, b = pick(clean), pick(dirty)
    common = a.index.intersection(b.index)
    assert len(common) > 50 and np.allclose(a[common], b[common])


def test_index_with_empty_adj_close(tmp_path, dataset):
    """Yahoo zostawia dla WIG20 pustą kolumnę adj_close - cechy indeksu i tak muszą się policzyć."""
    def empty_adj(name, df):
        if name == "WIG20":
            df["adj_close"] = np.nan
        return df

    alt = F.build_dataset(make_store(tmp_path / "idx", edit=empty_adj), progress=lambda *_: None)
    pl = alt[(alt["market"] == "PL") & alt["train_ok"]]
    assert len(pl) == len(dataset[(dataset["market"] == "PL") & dataset["train_ok"]])
    assert pl["idx_ret_1"].notna().all() and pl["rel_str_20"].notna().all()


def test_holiday_rows_from_yahoo_are_dropped(tmp_path):
    """Święto: Yahoo podaje wiersz z ceną z poprzedniego dnia i zerowym wolumenem - to nie była sesja."""
    hol = dates()[300]

    def holiday(name, df):
        if name in PL or name == "WIG20":
            prev = df.index[df.index.get_loc(hol) - 1]
            df.loc[hol, ["open", "high", "low", "close", "adj_close"]] = df.loc[prev, "adj_close"]
            df.loc[hol, ["open", "high", "low", "close"]] = df.loc[prev, "close"]
            df.loc[hol, "volume"] = 0.0
        return df

    ds = F.build_dataset(make_store(tmp_path, edit=holiday), progress=lambda *_: None)
    pl = ds[ds["market"] == "PL"]
    assert hol not in set(pl["date"])
    # dzień przed świętem ma etykietę z sesji PO święcie, a nie z pustego "święta"
    before = dates()[299]
    assert pl[pl["date"] == before]["train_ok"].all() and len(pl[pl["date"] == before]) == len(PL)
    # USA (nie miało święta) - dzień zostaje
    assert hol in set(ds[ds["market"] == "US"]["date"])
