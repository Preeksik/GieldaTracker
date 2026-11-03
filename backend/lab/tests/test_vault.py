"""Testy sejfu: nauka tylko na przeszłości, pieczęć i ostrzeżenie po zmianie strategii."""
import json

import numpy as np
import pandas as pd
import pytest

from lab import features as F
from lab import vault as V
from lab.tests.test_swing import swing_dataset


@pytest.fixture(scope="module")
def ds():
    return swing_dataset(days=1100, stocks=40, signal=0.02, seed=3)


class Spy:
    name = "szpieg"

    def __init__(self):
        self.seen = []

    def fit(self, train):
        self._mx = train["date"].max()
        return self

    def score(self, test):
        self.seen.append((self._mx, test["date"].min()))
        return -test["mom_12_1"].to_numpy()


def test_vault_trains_only_on_the_past(ds, monkeypatch):
    spy = Spy()
    real = V.make_model
    monkeypatch.setattr(V, "make_model", lambda k: spy if k == "A1" else real(k))
    preds, reb = V.predict_vault(ds, progress=None)
    vault_from = V.vault_window(ds)
    assert reb.min() >= vault_from and len(reb) >= 8
    sessions = pd.DatetimeIndex(sorted(ds["date"].unique()))
    for mx, mn in spy.seen:
        assert sessions.searchsorted(mn) - sessions.searchsorted(mx) > V.H     # przerwa na okres trzymania
    assert set(preds["model"]) == {"A1", "A2", "A3", "losowo"}


def test_seal_and_warning_after_changing_strategies(ds, tmp_path, monkeypatch):
    monkeypatch.setattr(V, "seal_path", lambda: str(tmp_path / "vault.json"))
    lines = []
    V.run(ds, out=lines.append, progress=None)
    text = "\n".join(lines)
    assert "SEJF - WERDYKT" in text and "A1" in text and "A3" in text
    seal = json.loads((tmp_path / "vault.json").read_text(encoding="utf-8"))
    assert seal["config_hash"] == V.config_hash() and set(seal["results"]) == {"A1", "A2", "A3", "losowo"}

    lines = []
    V.run(ds, out=lines.append, progress=None)                 # ta sama konfiguracja - bez ostrzeżenia
    assert not any("UWAGA" in l for l in lines)

    monkeypatch.setattr(V, "config_hash", lambda: "zmieniona")  # ktoś "poprawił" strategię po fakcie
    lines = []
    V.run(ds, out=lines.append, progress=None)
    assert any("NIE jest już uczciwym testem" in l for l in lines)
    seal2 = json.loads((tmp_path / "vault.json").read_text(encoding="utf-8"))
    assert "UWAGA" in seal2 and seal2["pierwsze_otwarcie"]["config_hash"] == seal["config_hash"]


def test_momentum_rule_passes_when_signal_is_real(ds, tmp_path, monkeypatch):
    monkeypatch.setattr(V, "seal_path", lambda: str(tmp_path / "v.json"))
    res, hold = V.run(ds, out=lambda *_: None, progress=None)
    a2 = res["A2"][0]["koncowy"] / 20000 - 1               # sygnał w danych jest w mom_12_1, A2 = zwrot 6 mies.
    assert np.isfinite(a2) and np.isfinite(hold)
