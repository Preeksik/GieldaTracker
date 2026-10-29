"""
Most między laboratorium a silnikiem C++ (`quantpulse`).

Jeśli silnik jest zainstalowany (pip install ./quant_engine), liczy on. Jeśli nie - te same
funkcje liczy numpy/pandas. Wyniki są takie same (testy w quant_engine/tests pilnują zgodności),
więc reszta laboratorium nie musi wiedzieć, która wersja działa.

    from lab import engine
    engine.BACKEND            # "cpp" albo "python"
    engine.rolling_std(x, 20)
"""
import numpy as np
import pandas as pd

try:
    import quantpulse as _qp
    BACKEND = "cpp"
except ImportError:
    _qp = None
    BACKEND = "python"


def _arr(x):
    return np.ascontiguousarray(x, dtype=float)


def rolling_mean(x, window):
    if _qp is not None:
        return _qp.rolling_mean(_arr(x), window)
    return pd.Series(_arr(x)).rolling(window).mean().to_numpy()


def rolling_std(x, window, ddof=1):
    if _qp is not None:
        return _qp.rolling_std(_arr(x), window, ddof)
    return pd.Series(_arr(x)).rolling(window).std(ddof=ddof).to_numpy()


def rolling_zscore(x, window, ddof=1):
    if _qp is not None:
        return _qp.rolling_zscore(_arr(x), window, ddof)
    s = pd.Series(_arr(x))
    sd = s.rolling(window).std(ddof=ddof)
    z = (s - s.rolling(window).mean()) / sd.where(sd > 0)
    return z.to_numpy()


def info():
    if _qp is None:
        return "silnik: Python (numpy/pandas) - C++ niezainstalowany"
    return f"silnik: C++ quantpulse {getattr(_qp, '__version__', '?')}"


if __name__ == "__main__":
    print(info())
