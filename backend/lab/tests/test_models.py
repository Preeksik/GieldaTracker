"""Testy modeli i walk-forward - na sztucznych danych z ukrytym sygnałem."""
import numpy as np
import pandas as pd
import pytest

from lab import features as F
from lab import models as M


def fake_dataset(days=1000, stocks=40, signal=0.004, seed=0):
    """Tabela jak z lab.features: sygnał ukryty w dist_high_250 (im wyżej, tym lepiej jutro)."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2017-01-02", periods=days)
    n = days * stocks
    df = pd.DataFrame({
        "date": np.repeat(dates, stocks),
        "ticker": np.tile([f"S{i:02d}.WA" for i in range(stocks)], days),
        "market": "PL",
    })
    for f in F.FEATURES:
        df[f] = rng.normal(size=n)
    df["dow"] = df["date"].dt.dayofweek.astype(float)
    noise = rng.normal(0, 0.02, n)
    df["y_oc"] = signal * df["dist_high_250"] + noise - 0.001
    df["y_cc"] = signal * df["dist_high_250"] + rng.normal(0, 0.02, n) + 0.0005
    for raw, ex in (("y_oc", "y_oc_ex"), ("y_cc", "y_cc_ex")):
        df[ex] = df[raw] - df.groupby("date")[raw].transform("mean")
    df["y_up"] = (df["y_oc_ex"] > 0).astype(float)
    df["y_oc_ex_idx"] = df["y_oc_ex"]
    df["turnover_20"] = rng.lognormal(10, 1, n)
    df["open_suspect"] = (rng.random(n) < 0.1).astype(float)
    df["idx_open_suspect"] = 0.0
    df["train_ok"] = True
    df["predict_row"] = False
    return df


@pytest.fixture(scope="module")
def ds():
    return fake_dataset()


def test_prepare_filters(ds):
    oc = M.prepare(ds, "oc")
    cc = M.prepare(ds, "cc")
    vault_from = ds["date"].max() - pd.Timedelta(days=F.VAULT_DAYS)
    assert oc["date"].max() < vault_from and cc["date"].max() < vault_from     # sejf nietknięty
    per_day = cc.groupby("date").size()
    assert per_day.min() >= M.MIN_STOCKS and per_day.max() <= 20                 # płynniejsza połowa z 40
    assert len(oc) < len(cc)                                                     # oc: bez fałszywych otwarć
    for f in M.STOCK_FEATURES:
        assert oc[f].between(-0.5, 0.5).all()
    assert np.allclose(oc.groupby("date")["ret_ex"].sum(), 0, atol=1e-12)
    assert ((oc["y"] == 1) == (oc["ret_ex"] > 0)).all()


def test_folds_are_monthly_after_warmup():
    dates = pd.bdate_range("2017-01-02", "2020-06-30")
    fl = M.folds(dates, step_months=1, min_train_years=2)
    assert fl[0][0] == pd.Timestamp("2019-01-01") and len(fl) == 18
    assert all(b == a + pd.DateOffset(months=1) for a, b in fl)


class Spy:
    """Model-szpieg: zapisuje, jakie daty widział przy nauce i przy teście."""
    name = "szpieg"

    def __init__(self):
        self.seen = []

    def fit(self, train):
        self._train_max = train["date"].max()
        return self

    def score(self, test):
        self.seen.append((self._train_max, test["date"].min()))
        return np.zeros(len(test))


def test_walk_forward_never_trains_on_test_period(ds):
    data = M.prepare(ds, "cc")
    spy = Spy()
    M.walk_forward(data, [spy], step_months=3, progress=None)
    sessions = pd.DatetimeIndex(sorted(data["date"].unique()))
    for train_max, test_min in spy.seen:
        assert train_max < test_min
        # przerwa: co najmniej GAP_DAYS sesji między ostatnim dniem nauki a pierwszym dniem testu
        assert sessions.searchsorted(test_min) - sessions.searchsorted(train_max) > M.GAP_DAYS


def test_future_labels_do_not_change_past_predictions(ds):
    data = M.prepare(ds, "cc")
    cut = data["date"].quantile(0.75)
    scrambled = data.copy()
    rng = np.random.default_rng(1)
    late = scrambled["date"] > cut
    scrambled.loc[late, ["ret_ex", "ret_raw"]] = rng.normal(size=(late.sum(), 2))
    scrambled.loc[late, "y"] = (scrambled.loc[late, "ret_ex"] > 0).astype(int)
    a, _ = M.walk_forward(data, [M.LogisticModel()], step_months=3, progress=None)
    b, _ = M.walk_forward(scrambled, [M.LogisticModel()], step_months=3, progress=None)
    early = a["date"] <= cut - pd.Timedelta(days=10)
    assert early.sum() > 200
    assert not np.allclose(a.loc[~early, "score"].to_numpy(), b.loc[~early, "score"].to_numpy())  # późniejsze się zmieniły
    assert np.allclose(a.loc[early, "score"].to_numpy(), b.loc[early, "score"].to_numpy())


def test_models_find_planted_signal_and_random_does_not(ds):
    data = M.prepare(ds, "cc")
    preds, imp = M.walk_forward(data, M.default_models(), step_months=3, progress=None)
    summary, yearly, _ = M.evaluate(preds)
    s = summary.set_index("model")
    for name in ("logistyczna", "lightgbm", "reguła: najbliżej rocznego szczytu"):
        assert s.loc[name, "top_ex_t"] > 4 and s.loc[name, "ic"] > 0.05, name
        assert s.loc[name, "trafnosc"] > 0.55
    assert abs(s.loc["losowo", "top_ex_t"]) < 3
    assert abs(s.loc["reguła: zmienność 20 dni", "top_ex_t"]) < 3.5     # cecha bez sygnału
    # logistyczna i drzewa wskazują właściwą cechę jako najważniejszą
    assert max(imp["logistyczna"], key=lambda f: abs(imp["logistyczna"][f])) == "dist_high_250"
    assert max(imp["lightgbm"], key=imp["lightgbm"].get) == "dist_high_250"
    assert set(yearly.columns) >= {2019}


def test_run_prints_report_and_comparison(ds):
    lines = []
    M.run(ds, step_months=6, out=lines.append, save=False)
    text = "\n".join(lines)
    assert "Handel w ciągu dnia" in text and "Handel z nocą" in text
    assert "Porównanie" in text and "Licznik prób" in text and "Rok po roku" in text


def test_trial_counter(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "trials_path", lambda: str(tmp_path / "trials.json"))
    assert M.register_trial({"a": 1}) == 1
    assert M.register_trial({"a": 1}) == 1          # ta sama konfiguracja = ta sama próba
    assert M.register_trial({"a": 2}) == 2
    assert 2.9 < M.chance_t(100) < 3.1
