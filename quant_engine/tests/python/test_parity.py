"""
Zgodność silnika C++ z dokładnym liczeniem w numpy (każde okno liczone od zera) do 1e-9.

Wzorcem NIE jest pandas: jego `rolling().std()` też liczy w locie i na długich seriach odpływa
o ~5e-9, czyli bardziej niż nasz silnik (~1e-10). Z pandas porównujemy luźniej (1e-7) -
tylko żeby mieć pewność, że znaczenie wyników (NaN-y, ddof) jest takie samo.
"""
import numpy as np
import pandas as pd
import pytest
from numpy.lib.stride_tricks import sliding_window_view

import quantpulse as qp

TOL = 1e-9
PANDAS_TOL = 1e-7


def exact(x, window, fn):
    """Wynik `fn` dla każdego okna osobno; pierwsze window-1 pozycji = NaN, NaN w oknie -> NaN."""
    x = np.asarray(x, dtype=float)
    out = np.full(len(x), np.nan)
    if len(x) >= window:
        out[window - 1:] = fn(sliding_window_view(x, window))
    return out


def ex_mean(x, w):
    return exact(x, w, lambda v: v.mean(axis=1))


def ex_std(x, w, ddof=1):
    return exact(x, w, lambda v: v.std(axis=1, ddof=ddof))


def prices(n=20_000, seed=7):
    rng = np.random.default_rng(seed)
    return 100 * np.cumprod(1 + rng.normal(0, 0.02, n))


def close(a, b, tol=TOL):
    a, b = np.asarray(a), np.asarray(b)
    assert a.shape == b.shape
    assert np.array_equal(np.isnan(a), np.isnan(b)), "NaN w innych miejscach"
    ok = ~np.isnan(a)
    scale = np.maximum(np.abs(b[ok]), 1.0)
    assert np.max(np.abs(a[ok] - b[ok]) / scale, initial=0.0) < tol


@pytest.mark.parametrize("window", [2, 5, 20, 63, 252])
def test_mean_std_match_exact(window):
    x = prices()
    close(qp.rolling_mean(x, window), ex_mean(x, window))
    close(qp.rolling_std(x, window), ex_std(x, window))
    close(qp.rolling_std(x, window, ddof=0), ex_std(x, window, ddof=0))


@pytest.mark.parametrize("window", [5, 20, 252])
def test_same_meaning_as_pandas(window):
    s = pd.Series(prices())
    close(qp.rolling_mean(s.to_numpy(), window), s.rolling(window).mean(), PANDAS_TOL)
    close(qp.rolling_std(s.to_numpy(), window), s.rolling(window).std(), PANDAS_TOL)


def test_returns_scale_values():
    # stopy zwrotu (wartości rzędu 0.01) - tu względny błąd liczy się najbardziej
    r = np.diff(np.log(prices()))
    close(qp.rolling_std(r, 20), ex_std(r, 20))
    close(qp.rolling_zscore(r, 20), (r - ex_mean(r, 20)) / ex_std(r, 20))


def test_nan_behaves_like_pandas():
    x = prices(500)
    x[[10, 11, 200, 499]] = np.nan
    close(qp.rolling_mean(x, 20), ex_mean(x, 20))
    close(qp.rolling_std(x, 20), ex_std(x, 20))
    close(qp.rolling_mean(x, 20), pd.Series(x).rolling(20).mean(), PANDAS_TOL)


def test_streaming_class_equals_batch():
    x = prices(1000)
    w = qp.RollingWindow(20)
    out = []
    for v in x:
        w.push(v)
        out.append(w.mean())
    close(out, qp.rolling_mean(x, 20))
    assert w.full and w.ready and len(w) == 20
    assert w.at_lag(0) == x[-1]


def test_accepts_lists_and_int_arrays():
    close(qp.rolling_mean([1, 2, 3, 4], 2), [np.nan, 1.5, 2.5, 3.5])
    close(qp.rolling_mean(np.arange(5), 5), [np.nan] * 4 + [2.0])


def test_bad_input():
    with pytest.raises(ValueError):
        qp.rolling_mean(np.zeros(5), 0)
    with pytest.raises(ValueError):
        qp.rolling_mean(np.zeros((2, 2)), 2)


def test_tiny_variance_after_big_moves():
    # Po dużych wahaniach cena prawie stoi - klasyczna pułapka algorytmów "w locie".
    rng = np.random.default_rng(1)
    x = np.concatenate([100 + rng.normal(0, 10, 3000), 14.1286 + rng.normal(0, 1e-5, 3000)])
    for w in (2, 5, 20):
        close(qp.rolling_std(x, w), ex_std(x, w))


def test_constant_series():
    x = np.full(500, 0.1)
    out = qp.rolling_std(x, 20)
    assert np.all(out[19:] >= 0) and np.nanmax(out) < 1e-15
    assert np.all(np.isnan(qp.rolling_zscore(x, 20)[19:]))   # dzielenie przez 0 -> NaN, nie inf
