"""
QuantPulse - trzymanie przez tydzień albo miesiąc (opcja A).

Zamiast handlu codziennego: co tydzień (5 sesji) albo co miesiąc (20 sesji) model wybiera
10 spółek z GPW, kupujemy je po równo i trzymamy do następnej zmiany składu. Transakcji jest
kilkadziesiąt razy mniej niż przy handlu codziennym, więc opłaty przestają zjadać zysk.

    cd backend
    python -m lab.swing run                  # tydzień i miesiąc, werdykt w prostych słowach (kilka minut)
    python -m lab.swing run --szczegoly      # z tabelami
    python -m lab.swing run --horyzont miesiac

JAK TO DZIAŁA:
  - decyzja wieczorem, kupno na fixingu zamknięcia NASTĘPNEGO dnia, sprzedaż na zamknięciu po 5 / 20 sesjach,
  - przy zmianie składu sprzedajemy tylko spółki, które wypadły z listy, i kupujemy nowe,
  - koszty: prowizja XTB (0% do 100 000 EUR obrotu w miesiącu, potem 0.2%, min. 10 EUR), poślizg 0.10%
    na stronę, całe akcje (za droga spółka na przydział = pomijamy), bez podatku Belki (IKE/IKZE),
  - punkt odniesienia: wszystkie spółki GPW z listy kupione po równo i trzymane (bez kosztów),
  - test na ślepo (walk-forward) z przerwą H+1 sesji między nauką a testem; sejf (ostatni rok) nietknięty.
"""

import os
import sys
import time
import argparse
from types import SimpleNamespace

import numpy as np
import pandas as pd

from . import features as F
from . import models as M
from . import backtest as B
from .neural import NeuralModel

HORIZONS = {"tydzien": 5, "miesiac": 20}
H_NAME = {5: "tydzień", 20: "miesiąc"}
K = 10                     # ile spółek w portfelu
CAPITAL = 20000.0
SLIPPAGE_BP = 10.0         # na stronę - zlecenia w trakcie sesji; na fixingu bywa mniej
REBAL_BAND = 0.5           # spółkę, która zostaje w portfelu, wyrównujemy dopiero gdy jej waga odjedzie o 50%


# ============================================================================
# DANE I MODELE
# ============================================================================

def prepare_h(ds, h, market="PL", liquid=True, include_vault=False):
    """
    Wiersze do nauki dla trzymania przez h sesji. Jak models.prepare, ale z etykietą y_h{h}.
    include_vault=True tylko dla lab.vault (jednorazowy sprawdzian zamrożonych strategii).
    """
    y = f"y_h{h}"
    d = ds[(ds["market"] == market) & ds["train_ok"] & ds[y].notna()].copy()
    if not include_vault:
        vault_from = ds["date"].max() - pd.Timedelta(days=F.VAULT_DAYS)
        # etykieta sięga h+1 sesji w przód - kończymy wcześniej, żeby wynik nie zajrzał do sejfu
        d = d[d["date"] < vault_from - pd.Timedelta(days=int((h + 2) * 7 / 5) + 2)]
    if liquid:
        med = d.groupby("date")["turnover_20"].transform("median")
        d = d[d["turnover_20"] >= med]
    d = d[d.groupby("date")["ticker"].transform("size") >= M.MIN_STOCKS]
    d["ret_raw"] = d[y]
    d["ret_ex"] = d[y] - d.groupby("date")[y].transform("mean")
    d["y"] = (d["ret_ex"] > 0).astype(int)
    for f in M.STOCK_FEATURES:
        d[f] = d.groupby("date")[f].rank(pct=True) - 0.5
    seq = [c for c in F.SEQ_COLS if c in d.columns]
    for c in [c for c in seq if c.startswith("seq_m")]:        # miesiące: pozycja na tle innych spółek
        d[c] = d.groupby("date")[c].rank(pct=True) - 0.5
    cols = ["date", "ticker"] + F.FEATURES + seq + ["y", "ret_ex", "ret_raw"]
    return d[cols].sort_values(["date", "ticker"]).reset_index(drop=True)


def swing_models():
    return [
        M.RandomModel(),
        M.RuleModel("mom_12_1", "momentum 12-1"),
        M.RuleModel("dist_high_250", "najbliżej rocznego szczytu"),
        M.RuleModel("ret_120", "zwrot 6 miesięcy"),
        M.RuleModel("vol_20", "zmienność 20 dni"),
        M.LogisticModel(),
        M.LGBMModel(),
        NeuralModel(),
    ]


def rebalance_dates(dates, h):
    """Co h-ta sesja testu: dni, w które zmieniamy skład portfela (okresy się nie nakładają)."""
    d = pd.DatetimeIndex(sorted(pd.unique(dates)))
    return d[::h]


def exec_close_prices(store, tickers):
    """(ticker, data decyzji t) -> cena zamknięcia następnej sesji (po niej kupujemy)."""
    parts = []
    for t in tickers:
        df = store.load(t)
        if df is None:
            continue
        df = df[df["volume"].fillna(0) > 0]
        if len(df) < 2:
            continue
        parts.append(pd.DataFrame({"ticker": t, "date": df.index[:-1], "exec_px": df["close"].to_numpy()[1:]}))
    if not parts:
        return pd.DataFrame(columns=["ticker", "date", "exec_px"])
    return pd.concat(parts, ignore_index=True)


# ============================================================================
# PORTFEL
# ============================================================================

def simulate_portfolio(preds, model, h, k=K, capital=CAPITAL, slippage_bp=SLIPPAGE_BP, prices=None,
                       eur=4.30, commission_rate=0.002, min_commission_eur=10.0, free_eur=100_000.0):
    """
    preds: prognozy tylko z dni zmiany składu (date, ticker, model, score, ret_raw = ln zwrotu z okresu, ret_ex).
    Co okres: 10 najlepiej ocenionych spółek po równo; płacimy tylko od zmian w portfelu.
    Zwraca (kapitał po każdym okresie, statystyki).
    """
    p = preds[preds["model"] == model]
    if prices is not None:
        p = p.merge(prices, on=["ticker", "date"], how="left")
    weights, equity = {}, capital
    month, month_eur = None, 0.0
    curve, n_trades, fees, hits = [], 0, 0.0, []
    slip = slippage_bp / 1e4

    def commission(value_pln):
        nonlocal month_eur
        v = value_pln / eur
        before = month_eur
        month_eur += v
        if month_eur <= free_eur:
            return 0.0
        charged = v if before >= free_eur else month_eur - free_eur
        return max(commission_rate * charged, min_commission_eur) * eur

    for d, g in p.groupby("date", sort=True):
        if (d.year, d.month) != month:
            month, month_eur = (d.year, d.month), 0.0
        g = g.sort_values(["score", "ticker"], ascending=[False, True])
        slot = equity / k
        if prices is not None:
            g = g[g["exec_px"].notna() & (g["exec_px"] <= slot)]     # całe akcje: za droga = pomijamy
        pick = g.head(k)
        chosen = list(pick["ticker"])
        tw = 1.0 / k
        target, changes = {}, []
        for t, w in weights.items():                 # wypadły z listy -> sprzedaż
            if t not in chosen:
                changes.append(w)
        for t in chosen:                             # zostają -> zwykle bez ruchu
            if t in weights:
                w = weights[t]
                if abs(w - tw) > REBAL_BAND * tw:    # mocno odjechała -> wyrównanie
                    changes.append(abs(w - tw))
                    w = tw
                target[t] = w
        new = [t for t in chosen if t not in weights]
        if new:                                      # nowe -> kupno za wolną gotówkę
            budget = max(0.0, 1.0 - sum(target.values()))
            w_new = min(tw, budget / len(new))
            for t in new:
                target[t] = w_new
                if w_new > 0:
                    changes.append(w_new)
        cost = 0.0
        for dw in changes:
            value = dw * equity
            cost += value * slip + commission(value)
            n_trades += 1
        fees += cost
        equity -= cost
        r = dict(zip(pick["ticker"], np.expm1(pick["ret_raw"].to_numpy())))
        hits.extend((pick["ret_ex"] > 0).tolist())
        port = sum(target[t] * r[t] for t in target)
        new_equity = equity * (1 + port)
        weights = {t: target[t] * (1 + r[t]) * equity / new_equity for t in target} if new_equity > 0 else {}
        equity = new_equity
        curve.append((d, equity))
        if equity <= 0:
            break
    eq = pd.Series(dict(curve), name="equity")
    eq.index = pd.DatetimeIndex(eq.index)
    return eq, {"transakcji": n_trades, "prowizje": fees, "trafnosc": float(np.mean(hits)) if hits else np.nan}


def benchmark_periods(ds, dates, h, market="PL", capital=CAPITAL):
    """Wszystkie spółki rynku po równo, trzymane okres po okresie (bez kosztów)."""
    y = f"y_h{h}"
    d = ds[(ds["market"] == market) & ds["train_ok"] & ds["date"].isin(dates)]
    r = d.groupby("date")[y].apply(lambda s: np.mean(np.expm1(s.dropna())) if s.notna().any() else 0.0)
    r = r.reindex(dates).fillna(0.0)
    return capital * (1 + r).cumprod()


def period_returns(eq, capital):
    r = eq.pct_change()
    r.iloc[0] = eq.iloc[0] / capital - 1
    return r


def metrics_periodic(eq, capital, h, extra=None, bench=None):
    """Miary dla krzywej kapitału liczonej co okres (h sesji). bench: krzywa punktu odniesienia."""
    ppy = 250 / h
    r = period_returns(eq, capital)
    years = max(len(eq) / ppy, 1e-9)
    final = float(eq.iloc[-1])
    cagr = (final / capital) ** (1 / years) - 1 if final > 0 else -1.0
    sharpe = r.mean() / (r.std() + 1e-12) * np.sqrt(ppy)
    full = pd.concat([pd.Series([capital]), eq.reset_index(drop=True)])
    dd = float((full / full.cummax() - 1).min())
    out = {"koncowy": final, "rocznie": cagr, "sharpe": sharpe, "t": sharpe * np.sqrt(years),
           "obsuniecie": dd, "transakcji": 0, "prowizje": 0.0, "trafnosc": np.nan, "lat": years,
           "t_ex": B.excess_t(r, period_returns(bench, capital)) if bench is not None else np.nan}
    out.update(extra or {})
    return out


def yearly(eq, capital):
    r = eq.pct_change()
    r.iloc[0] = eq.iloc[0] / capital - 1
    return (1 + r).groupby(r.index.year).prod() - 1


# ============================================================================
# PRZEBIEG
# ============================================================================

def run(ds, horizons=(5, 20), prices=None, out=print, save=True, step_months=1, details=True):
    buf = []
    say = buf.append
    all_results, per_h = [], {}
    for h in horizons:
        data = prepare_h(ds, h)
        if data.empty:
            out(f"Brak danych dla horyzontu {h}")
            continue
        models = swing_models()
        out(f"\nUczę i testuję (trzymanie przez {H_NAME[h]}): {len(models)} modeli…")
        preds, imp = M.walk_forward(data, models, step_months=step_months, progress=out, gap=h + 1)
        reb = rebalance_dates(preds["date"], h)
        pr = preds[preds["date"].isin(reb)]
        if save:
            path = os.path.join(F.lab_dir(), f"predictions_h{h}.parquet")
            try:
                preds.to_parquet(path + ".tmp", index=False)
                os.replace(path + ".tmp", path)
            except Exception:
                pass
        bench = benchmark_periods(ds, reb, h)
        hold = metrics_periodic(bench, CAPITAL, h)

        rows = []
        for m in models:
            for k, slip in ((K, SLIPPAGE_BP), (5, SLIPPAGE_BP), (K, 0.0)):
                if m.name == "losowo" and (k, slip) != (K, SLIPPAGE_BP):
                    continue
                eq, st = simulate_portfolio(pr, m.name, h, k=k, slippage_bp=slip, prices=prices)
                met = metrics_periodic(eq, CAPITAL, h, st, bench=bench)
                cfg = SimpleNamespace(model=m.name, k=k, slippage_bp=slip, capital=CAPITAL, h=h)
                rows.append((cfg, met, eq))
                if save:
                    M.register_trial({"swing": {"h": h, "model": m.name, "k": k, "slip": slip}})
        all_results += rows
        per_h[h] = (rows, hold, bench, imp, reb)

        say(f"\n=== Trzymanie przez {H_NAME[h]} (zmiana składu co {h} sesji) ===")
        say(f"Okres testu: {reb.min().date()} – {reb.max().date()}, {len(reb)} zmian składu. Kapitał {B.zl(CAPITAL)} zł.")
        say(B.header())
        say(B.fmt_row("wszystkie spółki GPW po równo - trzymaj (bez kosztów)", hold))
        say("  (konfiguracja: model | ile spółek | poślizg na stronę)")
        for cfg, met, _ in rows:
            name = f"{cfg.model} | {cfg.k} sp. | {cfg.slippage_bp / 100:.2f}%"
            say(B.fmt_row(name[:52], met))
        main = [x for x in rows if x[0].k == K and x[0].slippage_bp == SLIPPAGE_BP and x[0].model != "losowo"]
        cfg, met, eq = max(main, key=lambda x: x[1]["sharpe"])
        say(f"\n  Rok po roku - najlepszy: {cfg.model} ({cfg.k} spółek), na tle wszystkich spółek:")
        yr, by = yearly(eq, CAPITAL), yearly(bench, CAPITAL)
        for y in yr.index:
            say(f"    {y}   strategia {yr[y]:>+7.1%}   wszystkie spółki {by.get(y, np.nan):>+7.1%}")
        for mname, d in imp.items():
            top = sorted(d.items(), key=lambda kv: -abs(kv[1]))[:5]
            if mname == "logistyczna":
                say(f"  {mname}: najważniejsze cechy: " + ", ".join(f"{F.FEATURE_INFO[f]} {v:+.2f}" for f, v in top))
            else:
                say(f"  {mname}: najważniejsze cechy: " + ", ".join(f"{F.FEATURE_INFO[f]} {v:.0%}" for f, v in top))

    n_cfg = len(all_results)
    for h, (rows, hold, bench, imp, reb) in per_h.items():
        main = [x for x in rows if x[0].k == K and x[0].slippage_bp == SLIPPAGE_BP]
        rand = next(m for c, m, _ in main if c.model == "losowo")
        cand = [x for x in main if x[0].model != "losowo"]
        cfg, met, _ = max(cand, key=lambda x: x[1]["sharpe"])
        rand5k = {**rand, "koncowy": rand["koncowy"] / CAPITAL * 5000.0}
        out("")
        for line in B.verdict(
                f"co {H_NAME[h]} model wybiera {K} spółek z GPW; kupujesz je po równo i trzymasz do następnej zmiany",
                cfg, met, hold, CAPITAL, rand5k, n_cfg, met["lat"],
                None if details else "python -m lab.swing run",
                note=f"Poślizg {SLIPPAGE_BP / 100:.2f}% na stronę, całe akcje, bez podatku Belki (IKE/IKZE)."):
            out(line)
    if details:
        out("")
        for line in buf:
            out(line)
    return all_results, per_h


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m lab.swing", description="QuantPulse - trzymanie przez tydzień/miesiąc")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="naucz modele i przetestuj portfel zmieniany co tydzień / co miesiąc")
    r.add_argument("--horyzont", choices=["tydzien", "miesiac", "oba"], default="oba")
    r.add_argument("--szczegoly", action="store_true", help="pokaż też wszystkie tabele")
    r.add_argument("--quick", action="store_true", help="przeuczanie co kwartał (szybciej)")
    args = p.parse_args(argv)

    ds = F.load_dataset()
    if ds is None or "y_h20" not in ds.columns or "mom_12_1" not in ds.columns:
        print("Tabela cech jest ze starszej wersji albo jej brak - najpierw: python -m lab.features build")
        return 1
    from .data_store import MarketStore
    t0 = time.time()
    store = MarketStore()
    tick = sorted(ds.loc[ds["market"] == "PL", "ticker"].unique())
    prices = exec_close_prices(store, tick)
    hz = tuple(HORIZONS.values()) if args.horyzont == "oba" else (HORIZONS[args.horyzont],)
    run(ds, horizons=hz, prices=prices, step_months=3 if args.quick else 1, details=args.szczegoly)
    print(f"\nGotowe w {time.time() - t0:.0f} s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
