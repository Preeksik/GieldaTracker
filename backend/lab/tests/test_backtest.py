"""Testy backtestu: koszty liczone ręcznie, limit prowizji, całe akcje, spójność i brak zaglądania w przyszłość."""
import numpy as np
import pandas as pd
import pytest

from lab import backtest as B
from lab import models as M
from lab.tests.test_models import fake_dataset

D0 = pd.Timestamp("2020-01-02")


def one(ticker="AAA", date=D0, score=1.0, ret=np.log(1.01), model="m", open_=100.0):
    return {"date": date, "ticker": ticker, "model": model, "score": score, "ret_raw": ret,
            "ret_ex": 0.0, "next_open": open_}


def split(rows):
    df = pd.DataFrame(rows)
    prices = df[["ticker", "date", "next_open"]].drop_duplicates(["ticker", "date"])
    return df.drop(columns="next_open"), prices


def fx(dates, rate=4.0):
    return pd.Series(rate, index=pd.DatetimeIndex(sorted(set(dates))))


def test_single_trade_by_hand():
    preds, prices = split([one()])
    cfg = B.Config(model="m", k=1, max_weight=1.0, capital=5000)
    eq, tr = B.simulate(preds, prices, fx([D0]), cfg)
    assert tr["shares"].iloc[0] == 50 and tr["fee"].iloc[0] == 0
    assert eq.iloc[-1] == pytest.approx(5050.0)          # 50 akcji × (101 - 100)

    eq, tr = B.simulate(preds, prices, fx([D0]), B.Config(model="m", k=1, max_weight=1.0, slippage_bp=10))
    # kupno 100.10, sprzedaż 101 × 0.999 = 100.899, akcji floor(5000 / 100.1) = 49
    assert tr["shares"].iloc[0] == 49
    assert eq.iloc[-1] == pytest.approx(5000 + 49 * (100.899 - 100.1))


def test_commission_after_free_limit():
    preds, prices = split([one()])
    cfg = B.Config(model="m", k=1, max_weight=1.0, free_turnover_eur=0.0)
    eq, tr = B.simulate(preds, prices, fx([D0], 4.0), cfg)
    # 5000 zł = 1250 EUR, 0.2% = 2.5 EUR < minimum 10 EUR -> 40 zł za kupno i 40 zł za sprzedaż
    assert tr["fee"].iloc[0] == pytest.approx(80.0)
    big = B.Config(model="m", k=1, max_weight=1.0, free_turnover_eur=0.0, capital=50_000)
    _, tr = B.simulate(preds, prices, fx([D0], 4.0), big)
    # 50 000 zł = 12 500 EUR -> 0.2% = 25 EUR = 100 zł; sprzedaż 50 500 zł -> 101 zł
    assert tr["fee"].iloc[0] == pytest.approx(100.0 + 101.0)


def test_free_limit_crossed_mid_month_and_reset_next_month():
    days = list(pd.bdate_range("2020-01-27", "2020-02-07"))
    preds, prices = split([one(date=d, ret=0.0) for d in days])
    # limit 3000 EUR: dzień = 1250 EUR kupno + 1250 sprzedaż -> drugi dzień przekracza
    cfg = B.Config(model="m", k=1, max_weight=1.0, free_turnover_eur=3000.0)
    eq, tr = B.simulate(preds, prices, fx(days, 4.0), cfg)
    jan = tr[tr["date"].dt.month == 1]["fee"].to_numpy()
    feb = tr[tr["date"].dt.month == 2]["fee"].to_numpy()
    assert jan[0] == 0 and jan[1] > 0 and all(f > 0 for f in jan[1:])
    assert feb[0] == 0                                    # nowy miesiąc - limit od zera


def test_whole_shares_skip_expensive_stock():
    preds, prices = split([one("LPP", open_=15000.0), one("CHEAP", score=0.5)])
    eq, tr = B.simulate(preds, prices, fx([D0]), B.Config(model="m", k=5, capital=5000))
    assert list(tr["ticker"]) == ["CHEAP"]                # LPP za drogie na 1000 zł na spółkę


def test_hold_everything_equals_average_return():
    """Spójność: kupno wszystkich spółek po równo, bez kosztów = średni zwrot spółek każdego dnia."""
    rng = np.random.default_rng(0)
    days = pd.bdate_range("2020-01-01", periods=60)
    rows = [one(f"S{i}", d, score=rng.random(), ret=rng.normal(0, 0.02)) for d in days for i in range(8)]
    preds, prices = split(rows)
    cfg = B.Config(model="m", k=8, max_weight=1.0, whole_shares=False, capital=1000.0)
    eq, _ = B.simulate(preds, prices, fx(days), cfg)
    avg = preds.groupby("date")["ret_raw"].apply(lambda s: np.mean(np.expm1(s)))
    assert eq.iloc[-1] == pytest.approx(1000.0 * np.prod(1 + avg), rel=1e-12)


def test_deterministic():
    data = M.prepare(fake_dataset(days=900, stocks=30), "oc")
    preds, _ = M.walk_forward(data, [M.RandomModel(), M.LogisticModel()], step_months=6, progress=None)
    prices = preds[["ticker", "date"]].drop_duplicates().assign(next_open=50.0)
    cfg = B.Config(model="logistyczna")
    a, ta = B.simulate(preds, prices, fx(preds["date"]), cfg)
    b, tb = B.simulate(preds, prices, fx(preds["date"]), cfg)
    assert a.equals(b) and ta.equals(tb)


def test_threshold_uses_only_past():
    days = pd.bdate_range("2020-01-01", periods=120)
    rng = np.random.default_rng(1)
    scores = {d: rng.random(10) for d in days}
    t1 = B.thresholds(scores, 0.05)
    changed = dict(scores)
    for d in days[90:]:
        changed[d] = changed[d] + 100                     # przyszłe oceny zupełnie inne
    t2 = B.thresholds(changed, 0.05)
    assert all(t1[d] == t2[d] for d in days[:91])         # próg dnia 90 liczony z dni 0..89
    assert all(np.isinf(t1[d]) for d in days[:B.THRESHOLD_MIN_DAYS])
    assert 0.9 < t1[days[100]] < 1.0


def test_run_report(tmp_path, monkeypatch):
    ds = fake_dataset(days=800, stocks=40, signal=0.003)
    data = M.prepare(ds, "oc")
    preds, _ = M.walk_forward(data, M.default_models(), step_months=6, progress=None)
    prices = preds[["ticker", "date"]].drop_duplicates().assign(next_open=40.0)
    lines = []
    res = B.run(ds, preds, prices, fx(preds["date"]), out=lines.append, save=False)
    text = "\n".join(lines)
    for part in ("1) Wszyscy", "2) Wielkość kapitału", "3) Mniej, ale pewniej", "4) Najlepsza", "Licznik prób"):
        assert part in text
    best = {c.model: m for c, m, *_ in res if c.slippage_bp == 0 and c.capital == 5000 and c.k == 5 and not c.top_q}
    assert best["logistyczna"]["koncowy"] > best["losowo"]["koncowy"]


def test_yearly_returns_only_years_of_test():
    days = pd.bdate_range("2019-01-02", "2020-12-31")
    eq = pd.Series(1000.0, index=days)
    eq[eq.index.year == 2020] = 1100.0
    yr = B.yearly_returns(eq, 1000.0)
    assert list(yr.index) == [2019, 2020]
    assert yr[2019] == pytest.approx(0.0) and yr[2020] == pytest.approx(0.1)
