"""Testy opcji A: trzymanie przez tydzień / miesiąc."""
import numpy as np
import pandas as pd
import pytest

from lab import features as F
from lab import models as M
from lab import swing as S
from lab.tests.test_models import fake_dataset


def swing_dataset(days=1100, stocks=40, signal=0.02, seed=0):
    """Sztuczna tabela z etykietami na 5 i 20 sesji; sygnał miesięczny ukryty w mom_12_1."""
    ds = fake_dataset(days=days, stocks=stocks, signal=0.0, seed=seed)
    rng = np.random.default_rng(seed + 1)
    n = len(ds)
    ds["y_h5"] = 0.2 * signal * ds["mom_12_1"] + rng.normal(0.001, 0.04, n)
    ds["y_h20"] = signal * ds["mom_12_1"] + rng.normal(0.004, 0.08, n)
    for h in (5, 20):
        ds[f"y_h{h}_ex"] = ds[f"y_h{h}"] - ds.groupby("date")[f"y_h{h}"].transform("mean")
    return ds


@pytest.fixture(scope="module")
def ds():
    return swing_dataset()


def test_prepare_h(ds):
    d = S.prepare_h(ds, 20)
    vault_from = ds["date"].max() - pd.Timedelta(days=F.VAULT_DAYS)
    assert d["date"].max() < vault_from - pd.Timedelta(days=28)          # zapas na 20 sesji trzymania
    assert np.allclose(d.groupby("date")["ret_ex"].sum(), 0, atol=1e-12)
    assert d["mom_12_1"].between(-0.5, 0.5).all()


def test_rebalance_every_h_sessions():
    dates = pd.bdate_range("2021-01-01", periods=100)
    reb = S.rebalance_dates(dates, 20)
    assert len(reb) == 5 and reb[1] == dates[20]


def test_portfolio_by_hand():
    d1, d2 = pd.Timestamp("2021-01-04"), pd.Timestamp("2021-02-01")
    rows = [
        (d1, "A", 2.0, np.log(1.10)), (d1, "B", 1.0, np.log(0.95)), (d1, "C", 0.0, 0.0),
        (d2, "A", 2.0, np.log(1.00)), (d2, "B", 1.0, np.log(1.00)), (d2, "C", 0.0, 0.0),
    ]
    p = pd.DataFrame(rows, columns=["date", "ticker", "score", "ret_raw"]).assign(model="m", ret_ex=0.0)
    eq, st = S.simulate_portfolio(p, "m", 20, k=2, capital=1000.0, slippage_bp=10)
    # okres 1: kupno A i B po 500 zł, koszt 0.1% od 1000 zł = 1 zł; zwrot (+10% i -5%) / 2 = +2.5%
    e1 = (1000 - 1) * 1.025
    assert eq.iloc[0] == pytest.approx(e1)
    # okres 2: te same spółki, wagi odjechały mało (53.7% i 46.3%) -> żadnej transakcji, żadnego kosztu
    assert eq.iloc[1] == pytest.approx(e1)
    assert st["transakcji"] == 2                   # tylko 2 kupna na starcie


def test_portfolio_swaps_only_changed_names():
    days = [pd.Timestamp("2021-01-04"), pd.Timestamp("2021-02-01"), pd.Timestamp("2021-03-01")]
    rows = []
    for i, d in enumerate(days):
        for t, sc in (("A", 3.0), ("B", 2.0 if i < 2 else 0.0), ("C", 1.0 if i < 2 else 2.5)):
            rows.append((d, t, sc, 0.0))
    p = pd.DataFrame(rows, columns=["date", "ticker", "score", "ret_raw"]).assign(model="m", ret_ex=0.0)
    eq, st = S.simulate_portfolio(p, "m", 20, k=2, capital=1000.0, slippage_bp=10)
    # start: kupno A, B (2 transakcje); okres 2: bez zmian; okres 3: sprzedaż B, kupno C (2 transakcje)
    assert st["transakcji"] == 4
    # 0.1% od 1000 zł na starcie (zostaje 999 zł), potem 0.1% od sprzedaży B i kupna C (2 × 499.50 zł)
    assert eq.iloc[-1] == pytest.approx(999 - 0.999)


def test_whole_shares_skip():
    d1 = pd.Timestamp("2021-01-04")
    p = pd.DataFrame([(d1, "LPP", 2.0, 0.1), (d1, "X", 1.0, 0.0), (d1, "Y", 0.5, 0.0)],
                     columns=["date", "ticker", "score", "ret_raw"]).assign(model="m", ret_ex=0.0)
    prices = pd.DataFrame({"ticker": ["LPP", "X", "Y"], "date": d1, "exec_px": [15000.0, 50.0, 50.0]})
    eq, _ = S.simulate_portfolio(p, "m", 20, k=2, capital=10000.0, slippage_bp=0, prices=prices)
    assert eq.iloc[0] == pytest.approx(10000.0)    # LPP pominięta (5 000 zł na spółkę), X i Y bez zmian


class Spy:
    name = "szpieg"

    def __init__(self):
        self.seen = []

    def fit(self, train):
        self._mx = train["date"].max()
        return self

    def score(self, test):
        self.seen.append((self._mx, test["date"].min()))
        return np.zeros(len(test))


def test_gap_covers_holding_period(ds):
    data = S.prepare_h(ds, 20)
    spy = Spy()
    M.walk_forward(data, [spy], step_months=3, progress=None, gap=21)
    sessions = pd.DatetimeIndex(sorted(data["date"].unique()))
    for mx, mn in spy.seen:
        assert sessions.searchsorted(mn) - sessions.searchsorted(mx) > 21


def test_run_finds_monthly_signal_and_prints_verdict(ds):
    lines = []
    res, per_h = S.run(ds, horizons=(20,), out=lines.append, save=False, step_months=6)
    text = "\n".join(lines)
    assert "WERDYKT" in text and "Trzymanie przez miesiąc" in text
    rows, hold, *_ = per_h[20]
    main = {c.model: m for c, m, _ in rows if c.k == S.K and c.slippage_bp == S.SLIPPAGE_BP}
    assert main["reguła: momentum 12-1"]["rocznie"] > main["losowo"]["rocznie"]
    assert main["logistyczna"]["trafnosc"] > 0.52
