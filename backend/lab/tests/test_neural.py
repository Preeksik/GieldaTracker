"""Testy sieci neuronowej napisanej od zera."""
import numpy as np
import pandas as pd
import pytest

from lab.neural import MLP, NeuralModel
from lab import models as M


def test_gradients_match_numerical_derivative():
    """Pochodne z backpropagation = pochodne liczone 'na piechotę' (różnica przy małym przesunięciu wagi)."""
    rng = np.random.default_rng(0)
    X, y = rng.normal(size=(40, 6)), (rng.random(40) > 0.5).astype(float)
    net = MLP(6, hidden=(5, 4), seed=1)
    gW, gb = net.gradients(X, y, l2=1e-3)
    eps = 1e-6
    for params, grads in ((net.W, gW), (net.b, gb)):
        for P, G in zip(params, grads):
            for idx in [tuple(rng.integers(0, s) for s in P.shape) for _ in range(5)]:
                old = P[idx]
                P[idx] = old + eps
                up = net.loss(X, y, l2=1e-3)
                P[idx] = old - eps
                down = net.loss(X, y, l2=1e-3)
                P[idx] = old
                num = (up - down) / (2 * eps)
                assert G[idx] == pytest.approx(num, rel=1e-4, abs=1e-8)


def test_learns_pattern_that_linear_model_cannot():
    """y zależy od iloczynu dwóch cech (XOR) - regresja logistyczna zgaduje, sieć sobie radzi."""
    from sklearn.linear_model import LogisticRegression
    rng = np.random.default_rng(2)
    X = rng.normal(size=(6000, 4))
    y = (X[:, 0] * X[:, 1] > 0).astype(float)
    net = MLP(4, seed=3).fit(X[:5000], y[:5000], epochs=30)
    acc_net = ((net.predict_proba(X[5000:]) > 0.5) == y[5000:]).mean()
    acc_lin = LogisticRegression().fit(X[:5000], y[:5000]).score(X[5000:], y[5000:])
    assert acc_net > 0.9 and acc_lin < 0.6


def test_deterministic_and_loss_goes_down():
    rng = np.random.default_rng(4)
    X = rng.normal(size=(2000, 8))
    y = (X[:, 0] + 0.5 * X[:, 2] > 0).astype(float)
    a = MLP(8, seed=5)
    before = a.loss(X, y)
    a.fit(X, y, epochs=30)
    assert a.loss(X, y) < before * 0.6
    b = MLP(8, seed=5).fit(X, y, epochs=30)
    assert np.array_equal(a.predict_proba(X), b.predict_proba(X))


def test_neural_model_in_walk_forward():
    """W uczciwym teście na ślepo sieć znajduje ukryty sygnał, a losowy wybór nie."""
    from lab.tests.test_models import fake_dataset
    data = M.prepare(fake_dataset(days=1100, stocks=40, signal=0.006), "cc")
    preds, imp = M.walk_forward(data, [M.RandomModel(), NeuralModel(n_nets=2, epochs=15)], step_months=6,
                                progress=None)
    summary, _, _ = M.evaluate(preds)
    s = summary.set_index("model")
    assert s.loc["sieć neuronowa", "ic"] > 0.1 and s.loc["sieć neuronowa", "top_ex_t"] > 4
    assert abs(s.loc["losowo", "top_ex_t"]) < 3
    assert set(imp["sieć neuronowa"]) >= {"dist_high_250", "mom_12_1"}
