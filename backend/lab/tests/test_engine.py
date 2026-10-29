"""Obie wersje silnika (C++ i Python) dają ten sam wynik."""
import numpy as np
import pytest

from lab import engine


def series():
    rng = np.random.default_rng(3)
    x = 50 * np.cumprod(1 + rng.normal(0, 0.015, 3000))
    x[[5, 700]] = np.nan
    return x


@pytest.fixture(params=["cpp", "python"])
def eng(request, monkeypatch):
    if request.param == "cpp" and engine._qp is None:
        pytest.skip("silnik C++ niezainstalowany")
    if request.param == "python":
        monkeypatch.setattr(engine, "_qp", None)
    return engine


def test_backends_agree(eng):
    x = series()
    ref = {
        "mean": [np.nanmean(x[i - 19:i + 1]) if not np.isnan(x[i - 19:i + 1]).any() else np.nan
                 for i in range(19, len(x))],
    }
    got = eng.rolling_mean(x, 20)[19:]
    assert np.allclose(got, ref["mean"], rtol=1e-9, equal_nan=True)
    sd = eng.rolling_std(x, 20)
    z = eng.rolling_zscore(x, 20)
    assert np.isnan(sd[:19]).all() and np.isnan(z[:19]).all()
    ok = ~np.isnan(sd)
    assert np.allclose(z[ok], ((x - eng.rolling_mean(x, 20)) / sd)[ok], rtol=1e-8)


def test_zscore_of_constant_is_nan(eng):
    assert np.isnan(eng.rolling_zscore(np.full(50, 2.0), 10)[9:]).all()
