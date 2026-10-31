"""
QuantPulse - modele i uczciwe testowanie (etap 4).

Każdego wieczoru model układa ranking spółek: które JUTRO wypadną lepiej od średniej rynku.
Sprawdzamy to metodą walk-forward: model uczy się na przeszłości, typuje kolejny miesiąc,
którego nie widział, potem okno przesuwa się o miesiąc. Ostatni rok (sejf) nie jest używany.

    cd backend
    python -m lab.models run              # GPW, obie wersje handlu, wszystkie modele (kilka minut)
    python -m lab.models run --quick      # przeuczanie co kwartał zamiast co miesiąc (szybciej)
    python -m lab.models run --label oc   # tylko handel w ciągu dnia (otwarcie -> zamknięcie)

DWIE WERSJE HANDLU:
    oc - kupno na otwarciu jutro, sprzedaż na zamknięciu jutro (bez nocy). Tylko dni,
         w których Yahoo ma prawdziwe otwarcie.
    cc - kupno na zamknięciu dziś, sprzedaż na zamknięciu jutro (z nocą w środku).

ZAWODNICY:
    losowo          - 5 losowych spółek: tyle daje czysty przypadek
    reguła: ...     - ranking według jednej cechy (kierunek ustalany na danych do nauki)
    logistyczna     - regresja logistyczna na wszystkich cechach
    lightgbm        - drzewa decyzyjne (gradient boosting)

Wszystkie wyniki są PRZED KOSZTAMI. Koszty, spread i limity obrotu doliczy backtest (etap 5).
"""

import os
import sys
import json
import time
import hashlib
import argparse
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

from . import features as F

TOP_K = 5                 # ile spółek "kupujemy" dziennie
MIN_STOCKS = 15           # dzień liczy się, jeśli po filtrach zostało tyle spółek
MIN_TRAIN_YEARS = 2       # pierwszy test po 2 latach danych do nauki
GAP_DAYS = 1              # przerwa (w sesjach) między nauką a testem
SEED = 42

MARKET_WIDE = sorted(F.MARKET_WIDE)
STOCK_FEATURES = [f for f in F.FEATURES if f not in F.MARKET_WIDE]
RULES = {                                   # cecha -> opis reguły
    "dist_high_250": "najbliżej rocznego szczytu",
    "vol_20": "zmienność 20 dni",
    "gap": "luka na otwarciu",
    "mom_20": "przewaga dni wzrostowych",
    "ret_1": "wczorajszy ruch (odbicie)",
}
LABEL_INFO = {
    "oc": ("y_oc_ex", "y_oc", "w ciągu dnia (otwarcie -> zamknięcie)"),
    "cc": ("y_cc_ex", "y_cc", "z nocą (zamknięcie -> zamknięcie)"),
}


# ============================================================================
# DANE
# ============================================================================

def prepare(ds, label="oc", market="PL", liquid=True):
    """
    Wiersze do nauki i testu: jeden rynek, tylko sprzed sejfu, płynniejsza połowa spółek danego
    dnia (obrót z 20 dni - znany w chwili decyzji), dla 'oc' tylko dni z prawdziwym otwarciem.
    Cechy spółek zamieniamy na pozycję w rankingu danego dnia (-0.5 .. 0.5) - to odporne na
    skrajne wartości i na zmiany "poziomu" rynku w czasie.
    """
    y_ex, y_raw, _ = LABEL_INFO[label]
    d = ds[(ds["market"] == market) & ds["train_ok"]].copy()
    vault_from = ds["date"].max() - pd.Timedelta(days=F.VAULT_DAYS)
    d = d[d["date"] < vault_from]
    if label == "oc":
        d = d[d["open_suspect"] == 0]
    if liquid:
        med = d.groupby("date")["turnover_20"].transform("median")
        d = d[d["turnover_20"] >= med]
    d = d[d.groupby("date")["ticker"].transform("size") >= MIN_STOCKS]
    d = d.dropna(subset=[y_ex, y_raw])
    # etykieta liczona na nowo względem średniej spółek, które zostały po filtrach
    d["ret_ex"] = d[y_ex] - d.groupby("date")[y_ex].transform("mean")
    d["ret_raw"] = d[y_raw]
    d["y"] = (d["ret_ex"] > 0).astype(int)
    for f in STOCK_FEATURES:
        d[f] = d.groupby("date")[f].rank(pct=True) - 0.5
    cols = ["date", "ticker"] + F.FEATURES + ["y", "ret_ex", "ret_raw"]
    return d[cols].sort_values(["date", "ticker"]).reset_index(drop=True)


# ============================================================================
# MODELE - każdy ma fit(train) i score(test) -> ocena (wyżej = lepiej)
# ============================================================================

class RandomModel:
    name = "losowo"

    def fit(self, train):
        return self

    def score(self, test):
        rng = np.random.default_rng(SEED + int(test["date"].iloc[0].strftime("%Y%m%d")))
        return rng.random(len(test))


class RuleModel:
    """Ranking według jednej cechy. Kierunek (więcej czy mniej = lepiej) z danych do nauki."""

    def __init__(self, feature):
        self.feature = feature
        self.name = f"reguła: {RULES.get(feature, feature)}"
        self.sign = 1.0

    def fit(self, train):
        c = np.corrcoef(train[self.feature], train["ret_ex"])[0, 1]
        self.sign = 1.0 if (np.isnan(c) or c >= 0) else -1.0
        return self

    def score(self, test):
        return self.sign * test[self.feature].to_numpy()


class LogisticModel:
    name = "logistyczna"

    def fit(self, train):
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        X = train[F.FEATURES].to_numpy()
        self.scaler = StandardScaler().fit(X)
        self.model = LogisticRegression(C=0.05, max_iter=500)
        self.model.fit(self.scaler.transform(X), train["y"].to_numpy())
        return self

    def score(self, test):
        return self.model.predict_proba(self.scaler.transform(test[F.FEATURES].to_numpy()))[:, 1]

    def importance(self):
        return dict(zip(F.FEATURES, self.model.coef_[0]))


class LGBMModel:
    name = "lightgbm"

    def fit(self, train):
        import lightgbm as lgb
        self.model = lgb.LGBMClassifier(
            n_estimators=250, learning_rate=0.03, num_leaves=15, min_child_samples=300,
            subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0,
            random_state=SEED, n_jobs=-1, verbose=-1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.model.fit(train[F.FEATURES], train["y"])
        return self

    def score(self, test):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return self.model.predict_proba(test[F.FEATURES])[:, 1]

    def importance(self):
        imp = self.model.booster_.feature_importance(importance_type="gain")
        return dict(zip(F.FEATURES, imp / max(imp.sum(), 1e-12)))


def default_models():
    return [RandomModel()] + [RuleModel(f) for f in RULES] + [LogisticModel(), LGBMModel()]


# ============================================================================
# WALK-FORWARD
# ============================================================================

def folds(dates, step_months=1, min_train_years=MIN_TRAIN_YEARS):
    """(początek testu, koniec testu) - kolejne okresy po `min_train_years` od pierwszej daty."""
    dates = pd.DatetimeIndex(sorted(pd.unique(dates)))
    start = (dates.min() + pd.DateOffset(years=min_train_years)).to_period("M").to_timestamp()
    out = []
    while start <= dates.max():
        end = start + pd.DateOffset(months=step_months)
        if ((dates >= start) & (dates < end)).any():
            out.append((start, end))
        start = end
    return out


def walk_forward(data, models=None, step_months=1, progress=print):
    """
    Prognozy "na ślepo": dla każdego okresu model uczy się tylko na wierszach sprzed testu
    (z przerwą GAP_DAYS sesji) i ocenia spółki w okresie testu.
    Zwraca (prognozy, ważność cech).
    """
    models = models or default_models()
    sessions = pd.DatetimeIndex(sorted(data["date"].unique()))
    preds, imps = [], {}
    fl = folds(sessions, step_months)
    t0 = time.time()
    for i, (start, end) in enumerate(fl):
        first = sessions.searchsorted(start)
        if first - GAP_DAYS <= 0:
            continue
        train_end = sessions[first - GAP_DAYS]          # nauka: daty < train_end
        train = data[data["date"] < train_end]
        test = data[(data["date"] >= start) & (data["date"] < end)]
        if train.empty or test.empty:
            continue
        out = test[["date", "ticker", "ret_ex", "ret_raw"]].copy()
        for m in models:
            m.fit(train)
            out[m.name] = m.score(test)
            if hasattr(m, "importance"):
                for f, v in m.importance().items():
                    imps.setdefault(m.name, {}).setdefault(f, []).append(v)
        preds.append(out)
        if progress and (i % 12 == 11 or i == len(fl) - 1):
            progress(f"  {start:%Y-%m}: {i + 1}/{len(fl)} okresów, {time.time() - t0:.0f} s")
    if not preds:
        raise RuntimeError("Za mało danych na walk-forward")
    p = pd.concat(preds, ignore_index=True)
    names = [m.name for m in models]
    long = p.melt(id_vars=["date", "ticker", "ret_ex", "ret_raw"], value_vars=names,
                  var_name="model", value_name="score")
    imp = {m: {f: float(np.mean(v)) for f, v in d.items()} for m, d in imps.items()}
    return long, imp


# ============================================================================
# OCENA
# ============================================================================

def daily_picks(preds, k=TOP_K):
    """Dla każdego modelu i dnia: średni wynik k najlepiej ocenionych i k najgorzej ocenionych spółek."""
    p = preds.copy()
    p["rnk"] = p.groupby(["model", "date"])["score"].rank(ascending=False, method="first")
    p["rnk_low"] = p.groupby(["model", "date"])["score"].rank(ascending=True, method="first")
    top = p[p["rnk"] <= k].groupby(["model", "date"]).agg(
        top_ex=("ret_ex", "mean"), top_raw=("ret_raw", "mean"), hit=("ret_ex", lambda s: (s > 0).mean()))
    bot = p[p["rnk_low"] <= k].groupby(["model", "date"])["ret_ex"].mean().rename("bot_ex")
    return top.join(bot).reset_index()


def daily_ic(preds):
    def ic(g):
        if g["score"].nunique() < 3:
            return np.nan
        return g["score"].rank().corr(g["ret_ex"].rank())
    return preds.groupby(["model", "date"])[["score", "ret_ex"]].apply(ic).rename("ic").reset_index()


def evaluate(preds):
    """Tabela wyników na model + rozbicie rok po roku."""
    dp = daily_picks(preds)
    ic = daily_ic(preds)
    rows = []
    order = list(pd.unique(preds["model"]))            # kolejność jak na liście modeli: od najprostszego
    for m in order:
        g = dp[dp["model"] == m]
        ics = ic[ic["model"] == m]["ic"].dropna()
        ex = g["top_ex"]
        rows.append({
            "model": m, "dni": len(g),
            "ic": ics.mean(), "ic_t": ics.mean() / (ics.std() + 1e-12) * np.sqrt(len(ics)),
            "trafnosc": (g["hit"]).mean(),
            "top_ex_pb": ex.mean() * 1e4,
            "top_ex_t": ex.mean() / (ex.std() + 1e-12) * np.sqrt(len(ex)),
            "top_raw_pb": g["top_raw"].mean() * 1e4,
            "spread_pb": (g["top_ex"] - g["bot_ex"]).mean() * 1e4,
            "sharpe_ex": ex.mean() / (ex.std() + 1e-12) * np.sqrt(250),
        })
    summary = pd.DataFrame(rows)
    dp["rok"] = dp["date"].dt.year
    yearly = (dp.groupby(["model", "rok"])["top_ex"].mean() * 1e4).unstack("rok")
    return summary, yearly, dp


# ============================================================================
# LICZNIK PRÓB
# ============================================================================

def trials_path():
    return os.path.join(F.lab_dir(), "trials.json")


def register_trial(config):
    """Zapisuje próbę (unikalna konfiguracja = jedna próba). Zwraca liczbę różnych prób."""
    path = trials_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            trials = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        trials = {}
    key = hashlib.sha1(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]
    trials.setdefault(key, {"config": config, "first": datetime.now().isoformat(timespec="seconds")})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(trials, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)
    return len(trials)


def chance_t(n_tests):
    """Jakie t wychodzi przypadkiem jako NAJLEPSZE z n niezależnych prób (przybliżenie)."""
    return float(np.sqrt(2 * np.log(max(n_tests, 2))))


# ============================================================================
# RAPORT
# ============================================================================

def report(label, data, summary, yearly, imp, n_trials, n_models, out=print):
    _, _, desc = LABEL_INFO[label]
    out(f"\n=== Handel {desc} ===")
    out(f"Dane: {data['date'].min().date()} – {data['date'].max().date()}, "
        f"{data['ticker'].nunique()} spółek, średnio {data.groupby('date').size().mean():.0f} spółek dziennie "
        f"po filtrach. Test na ślepo: {summary['dni'].max()} sesji.")
    out(f"Codziennie {TOP_K} spółek z góry rankingu. Wynik = ich średni zwrot ponad średnią spółek, "
        "w punktach bazowych (1 pb = 0.01%), PRZED kosztami.\n")
    out(f"  {'model':<34} {'trafność':>8} {'ponad śr.':>10} {'t':>6} {'zwrot':>8} {'top-dół':>8} {'IC':>7} {'Sharpe':>7}")
    for _, r in summary.iterrows():
        out(f"  {r['model']:<34} {r['trafnosc']:>8.1%} {r['top_ex_pb']:>+8.1f}pb {r['top_ex_t']:>+6.1f} "
            f"{r['top_raw_pb']:>+6.1f}pb {r['spread_pb']:>+6.1f}pb {r['ic']:>+7.3f} {r['sharpe_ex']:>+7.2f}")
    out("  trafność = jaki odsetek wybranych spółek pobił średnią; zwrot = zwykły zwrot tych spółek "
        "(z ruchem całego rynku) - to zarobiłbyś, kupując tylko je; top-dół = najlepsze 5 minus najgorsze 5.")

    years = [c for c in yearly.columns]
    out(f"\n  Rok po roku (top {TOP_K} ponad średnią, pb dziennie):")
    out(f"  {'model':<34} " + " ".join(f"{y:>7}" for y in years))
    for m in summary["model"]:
        if m in yearly.index:
            out(f"  {m:<34} " + " ".join(
                f"{yearly.loc[m, y]:>+7.1f}" if pd.notna(yearly.loc[m, y]) else f"{'':>7}" for y in years))

    for m, d in imp.items():
        top = sorted(d.items(), key=lambda kv: -abs(kv[1]))[:6]
        if m == "logistyczna":
            out(f"\n  {m}: najważniejsze cechy (waga; + = wyżej w rankingu): " +
                ", ".join(f"{F.FEATURE_INFO[f]} {v:+.2f}" for f, v in top))
        else:
            out(f"  {m}: najważniejsze cechy (udział w decyzjach): " +
                ", ".join(f"{F.FEATURE_INFO[f]} {v:.0%}" for f, v in top))

    best = summary.loc[summary["top_ex_t"].idxmax()]
    tests = n_trials * n_models
    ct = chance_t(tests)
    out(f"\n  Licznik prób: to konfiguracja nr {n_trials} w historii laboratorium, razem ok. {tests} wyników "
        f"modeli. Najlepszy z tylu czysto losowych wyników miałby t ≈ {ct:.1f}.")
    verdict = "wyraźnie ponad przypadek" if best["top_ex_t"] > ct + 1 else \
        ("na granicy przypadku" if best["top_ex_t"] > ct else "w zasięgu przypadku")
    out(f"  Najlepszy tutaj: {best['model']} (t {best['top_ex_t']:+.1f}) - {verdict}.")


def save_predictions(preds, label):
    path = os.path.join(F.lab_dir(), f"predictions_{label}.parquet")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        preds.to_parquet(path + ".tmp", index=False)
    except Exception:
        path = path.replace(".parquet", ".csv.gz")
        preds.to_csv(path + ".tmp", index=False, compression="gzip")
    os.replace(path + ".tmp", path)
    return path


# ============================================================================
# WIERSZ POLECEŃ
# ============================================================================

def run(ds, labels=("oc", "cc"), market="PL", step_months=1, out=print, save=True):
    results = {}
    for label in labels:
        data = prepare(ds, label=label, market=market)
        if data.empty:
            out(f"Brak danych dla {market}/{label}")
            continue
        models = default_models()
        out(f"\nUczę i testuję ({label}): {len(models)} modeli, przeuczanie co {step_months} mies.…")
        preds, imp = walk_forward(data, models, step_months=step_months, progress=out)
        summary, yearly, dp = evaluate(preds)
        n_trials = register_trial({"market": market, "label": label, "step": step_months,
                                   "top_k": TOP_K, "models": [m.name for m in models],
                                   "features": F.FEATURES, "liquid": True}) if save else 1
        report(label, data, summary, yearly, imp, n_trials, len(models), out=out)
        if save:
            out(f"  Prognozy zapisane: {save_predictions(preds, label)} (do backtestu w etapie 5)")
        results[label] = summary
    if len(results) == 2:
        out("\n=== Porównanie: w ciągu dnia (oc) czy z nocą (cc)? ===")
        out(f"  {'model':<34} {'oc ponad śr.':>13} {'oc zwrot':>9} {'cc ponad śr.':>13} {'cc zwrot':>9}")
        a, b = results["oc"].set_index("model"), results["cc"].set_index("model")
        for m in a.index:
            out(f"  {m:<34} {a.loc[m, 'top_ex_pb']:>+11.1f}pb {a.loc[m, 'top_raw_pb']:>+7.1f}pb "
                f"{b.loc[m, 'top_ex_pb']:>+11.1f}pb {b.loc[m, 'top_raw_pb']:>+7.1f}pb")
        out("  'zwrot' zawiera ruch całego rynku: przy handlu w ciągu dnia GPW średnio traci, w nocy zyskuje.")
    return results


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m lab.models", description="QuantPulse - modele i walk-forward")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="naucz i przetestuj modele (walk-forward)")
    r.add_argument("--label", choices=["oc", "cc", "both"], default="both")
    r.add_argument("--market", choices=["PL", "US"], default="PL")
    r.add_argument("--quick", action="store_true", help="przeuczanie co kwartał (szybciej)")
    args = p.parse_args(argv)

    ds = F.load_dataset()
    if ds is None:
        print("Brak tabeli cech - najpierw: python -m lab.features build")
        return 1
    if "y_cc" not in ds.columns:
        print("Tabela cech jest ze starszej wersji - przebuduj ją: python -m lab.features build")
        return 1
    labels = ("oc", "cc") if args.label == "both" else (args.label,)
    t0 = time.time()
    run(ds, labels=labels, market=args.market, step_months=3 if args.quick else 1)
    print(f"\nGotowe w {time.time() - t0:.0f} s. Wszystko PRZED kosztami - koszty doliczy backtest (etap 5).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
