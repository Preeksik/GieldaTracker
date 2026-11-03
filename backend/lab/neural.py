"""
QuantPulse - sieć neuronowa napisana od zera (numpy, bez gotowych bibliotek do sieci).

CO TO JEST, W PROSTYCH SŁOWACH:
  Sieć dostaje o każdej spółce 52 liczby: 20 naszych wskaźników oraz surowy przebieg ceny
  (każdy z ostatnich 20 dni i każdy z ostatnich 12 miesięcy). Przepuszcza je przez dwie
  warstwy "neuronów" - każdy neuron to suma ważona wejść i prosta funkcja (ReLU: ujemne -> 0).
  Na końcu wychodzi jedna liczba: szansa, że spółka wypadnie lepiej od przeciętnej.
  Uczenie = tysiące drobnych poprawek wag tak, żeby te szanse lepiej zgadzały się z historią.

  wejście (52) -> warstwa 1 (32 neurony) -> warstwa 2 (16 neuronów) -> wyjście (1)

JAK SIĘ UCZY:
  - błąd: entropia krzyżowa (kara rośnie, gdy sieć jest pewna i się myli),
  - pochodne liczone "wstecz" (backpropagation) - sprawdzane testem z pochodną liczoną numerycznie,
  - poprawki wag: Adam (popularna odmiana spadku gradientowego), małe paczki po 512 przykładów,
  - przeciw przeuczeniu: kara za duże wagi (L2) i średnia z kilku sieci o różnym starcie (zespół).

W TEŚCIE WALK-FORWARD sieć jest uczona co okres od nowa wyłącznie na przeszłości - tak samo
jak pozostali zawodnicy. Ziarno losowości jest stałe: ten sam przebieg daje ten sam wynik.
"""

import numpy as np

from . import features as F
from . import models as M


class MLP:
    """Sieć w pełni połączona: wejście -> ReLU -> ReLU -> sigmoida. Wszystko w numpy."""

    def __init__(self, n_in, hidden=(32, 16), seed=0):
        rng = np.random.default_rng(seed)
        sizes = [n_in, *hidden, 1]
        self.W, self.b = [], []
        for a, b in zip(sizes[:-1], sizes[1:]):
            # inicjalizacja He - dobra dla ReLU (sygnał nie gaśnie ani nie wybucha między warstwami)
            self.W.append(rng.normal(0, np.sqrt(2.0 / a), size=(a, b)))
            self.b.append(np.zeros(b))
        self.rng = rng
        self._adam = None

    # ---------- przejście w przód ----------
    def forward(self, X):
        acts, pre = [X], []
        h = X
        for i, (W, b) in enumerate(zip(self.W, self.b)):
            z = h @ W + b
            pre.append(z)
            h = np.maximum(z, 0.0) if i < len(self.W) - 1 else z
            acts.append(h)
        logit = h[:, 0]
        return logit, acts, pre

    def predict_proba(self, X):
        logit, _, _ = self.forward(X)
        return 1.0 / (1.0 + np.exp(-np.clip(logit, -40, 40)))

    # ---------- błąd i pochodne ----------
    @staticmethod
    def loss_from_logit(logit, y):
        # entropia krzyżowa liczona stabilnie: log(1 + e^z) - y*z
        return float(np.mean(np.logaddexp(0.0, logit) - y * logit))

    def loss(self, X, y, l2=0.0):
        logit, _, _ = self.forward(X)
        reg = 0.5 * l2 * sum(float((W ** 2).sum()) for W in self.W)
        return self.loss_from_logit(logit, y) + reg

    def gradients(self, X, y, l2=0.0):
        """Pochodne błędu po wszystkich wagach (backpropagation)."""
        logit, acts, pre = self.forward(X)
        n = len(y)
        p = 1.0 / (1.0 + np.exp(-np.clip(logit, -40, 40)))
        delta = ((p - y) / n)[:, None]                       # pochodna błędu po wyjściu
        gW, gb = [None] * len(self.W), [None] * len(self.b)
        for i in range(len(self.W) - 1, -1, -1):
            gW[i] = acts[i].T @ delta + l2 * self.W[i]
            gb[i] = delta.sum(axis=0)
            if i > 0:
                delta = (delta @ self.W[i].T) * (pre[i - 1] > 0)   # ReLU przepuszcza tylko dodatnie
        return gW, gb

    # ---------- uczenie ----------
    def fit(self, X, y, epochs=10, batch=512, lr=1e-3, l2=1e-4, beta1=0.9, beta2=0.999, eps=1e-8):
        params = self.W + self.b
        if self._adam is None:
            self._adam = {"m": [np.zeros_like(p) for p in params], "v": [np.zeros_like(p) for p in params], "t": 0}
        st = self._adam
        n = len(y)
        for _ in range(epochs):
            order = self.rng.permutation(n)
            for s in range(0, n, batch):
                idx = order[s:s + batch]
                gW, gb = self.gradients(X[idx], y[idx], l2)
                grads = gW + gb
                st["t"] += 1
                for k, (p, g) in enumerate(zip(params, grads)):
                    st["m"][k] = beta1 * st["m"][k] + (1 - beta1) * g
                    st["v"][k] = beta2 * st["v"][k] + (1 - beta2) * g * g
                    m_hat = st["m"][k] / (1 - beta1 ** st["t"])
                    v_hat = st["v"][k] / (1 - beta2 ** st["t"])
                    p -= lr * m_hat / (np.sqrt(v_hat) + eps)
        return self


class NeuralModel:
    """Zawodnik dla walk-forward: zespół kilku małych sieci (różne ziarna), uśrednione oceny."""
    name = "sieć neuronowa"

    def __init__(self, n_nets=3, epochs=8, hidden=(32, 16), seed=7):
        self.n_nets, self.epochs, self.hidden, self.seed = n_nets, epochs, hidden, seed

    def _matrix(self, df):
        cols = [c for c in F.FEATURES + F.SEQ_COLS if c in df.columns]
        self.cols = getattr(self, "cols", cols)
        X = df[self.cols].to_numpy(dtype=float)
        return np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    def fit(self, train):
        X = self._matrix(train)
        self.mu = X.mean(axis=0)
        self.sd = X.std(axis=0) + 1e-9
        Xs = np.clip((X - self.mu) / self.sd, -5, 5)
        y = train["y"].to_numpy(dtype=float)
        self.nets = [MLP(Xs.shape[1], self.hidden, seed=self.seed + i).fit(Xs, y, epochs=self.epochs)
                     for i in range(self.n_nets)]
        return self

    def score(self, test):
        X = np.clip((self._matrix(test) - self.mu) / self.sd, -5, 5)
        return np.mean([net.predict_proba(X) for net in self.nets], axis=0)

    def importance(self):
        """Przybliżona ważność wejść: średnia siła wag pierwszej warstwy (po standaryzacji wejść)."""
        w = np.mean([np.abs(net.W[0]).sum(axis=1) for net in self.nets], axis=0)
        # Przebiegi (20 dni, 12 miesięcy) to wiele wejść - liczymy ich ŚREDNIĄ, a nie sumę,
        # inaczej grupa wyglądałaby na ważną tylko dlatego, że ma dużo kolumn.
        groups = {}
        for c, v in zip(self.cols, w):
            key = "seq_daily" if c.startswith("seq_d") else "seq_monthly" if c.startswith("seq_m") else c
            groups.setdefault(key, []).append(float(v))
        out = {k: float(np.mean(v)) for k, v in groups.items()}
        total = sum(out.values()) or 1.0
        return {k: v / total for k, v in out.items()}
