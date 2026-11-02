"""
QuantPulse - backtest z kosztami XTB (etap 5).

Odtwarza handel dzień po dniu na prognozach "na ślepo" z etapu 4 (predictions_oc.parquet):
  wieczorem dnia t model ocenia spółki -> rano dnia t+1 kupujemy najlepsze po cenie otwarcia
  -> sprzedajemy po cenie zamknięcia tego samego dnia -> płacimy prowizję i poślizg.

    cd backend
    python -m lab.backtest run

KOSZTY (akcje GPW w XTB):
  - prowizja 0% dopóki miesięczny obrót (kupno + sprzedaż) nie przekroczy 100 000 EUR,
    potem 0.2% od transakcji, ale nie mniej niż 10 EUR,
  - poślizg: kupno o X pb drożej niż otwarcie, sprzedaż o X pb taniej niż zamknięcie.
    0 pb = zlecenia na fixing otwarcia i zamknięcia (cena dokładnie z notowań),
  - kupujemy całe akcje: przy małym kapitale drogich spółek (np. LPP) nie da się kupić,
  - bez przewalutowania (GPW w złotych), bez opłat za noc (pozycje zamykane tego samego dnia),
  - BEZ podatku Belki (19% od rocznego zysku) - na IKE/IKZE go nie ma, na zwykłym koncie odejmij.

UCZCIWOŚĆ:
  - ta sama konfiguracja zawsze daje ten sam wynik (żadnego losowania w symulacji),
  - próg "kupuj tylko pewne typy" liczony wyłącznie z ocen z przeszłości,
  - każda przetestowana konfiguracja trafia do licznika prób.
"""

import os
import sys
import time
import argparse
from dataclasses import dataclass, asdict, replace

import numpy as np
import pandas as pd

from . import features as F
from . import models as M

THRESHOLD_LOOKBACK = 250     # próg z ocen z ostatnich 250 dni decyzji
THRESHOLD_MIN_DAYS = 60      # wcześniej nie handlujemy z progiem (za mało historii)


@dataclass(frozen=True)
class Config:
    model: str = "lightgbm"
    k: int = 5                      # najwięcej spółek dziennie
    top_q: float = None             # np. 0.05: tylko oceny z górnych 5% ocen z przeszłości
    capital: float = 5000.0         # kapitał startowy, zł
    slippage_bp: float = 0.0        # na stronę
    commission_rate: float = 0.002
    min_commission_eur: float = 10.0
    free_turnover_eur: float = 100_000.0
    max_weight: float = 0.20        # najwięcej kapitału w jednej spółce
    whole_shares: bool = True

    def label(self):
        q = f", próg top {self.top_q:.0%}" if self.top_q else ""
        return f"{self.model} | {self.k} spółek{q} | {zl(self.capital)} zł | poślizg {self.slippage_bp:g} pb"


# ============================================================================
# DANE
# ============================================================================

def load_predictions(label="oc"):
    root = F.lab_dir()
    for ext, reader in ((".parquet", pd.read_parquet), (".csv.gz", lambda p: pd.read_csv(p, parse_dates=["date"]))):
        path = os.path.join(root, f"predictions_{label}{ext}")
        if os.path.exists(path):
            return reader(path)
    return None


def next_open_prices(store, tickers):
    """(ticker, data decyzji t) -> cena otwarcia następnej sesji tej spółki (po tej cenie kupujemy)."""
    parts = []
    for t in tickers:
        df = store.load(t)
        if df is None:
            continue
        # wiersze z zerowym wolumenem to święta wstawione przez Yahoo albo dni bez handlu - nie sesje
        df = df[df["volume"].fillna(0) > 0]
        if len(df) < 2:
            continue
        parts.append(pd.DataFrame({"ticker": t, "date": df.index[:-1], "next_open": df["open"].to_numpy()[1:]}))
    if not parts:
        return pd.DataFrame(columns=["ticker", "date", "next_open"])
    return pd.concat(parts, ignore_index=True)


def eur_rate(store, dates):
    """Kurs EUR/PLN na każdy dzień decyzji (ostatni znany); brak danych -> 4.30."""
    df = store.load("EURPLN")
    if df is None or df.empty:
        return pd.Series(4.30, index=pd.DatetimeIndex(dates))
    s = df["close"].sort_index()
    return s.reindex(s.index.union(dates)).ffill().bfill().reindex(dates)


def market_benchmark(ds, dates, market="PL"):
    """Średnia wszystkich spółek rynku, kupiona i trzymana (codziennie po równo), bez kosztów."""
    d = ds[(ds["market"] == market) & ds["train_ok"]]
    r = d.groupby("date")["y_cc"].apply(lambda s: np.mean(np.expm1(s.dropna())))
    return r.reindex(dates).fillna(0.0)


# ============================================================================
# SYMULACJA
# ============================================================================

def thresholds(day_scores, top_q):
    """Próg na każdy dzień: kwantyl (1 - top_q) ocen z poprzednich THRESHOLD_LOOKBACK dni. Tylko przeszłość."""
    out, hist = {}, []
    for d in sorted(day_scores):
        if len(hist) >= THRESHOLD_MIN_DAYS:
            past = np.concatenate(hist[-THRESHOLD_LOOKBACK:])
            out[d] = float(np.quantile(past, 1 - top_q))
        else:
            out[d] = np.inf
        hist.append(day_scores[d])
    return out


def simulate(preds, prices, eurpln, cfg):
    """
    preds: date, ticker, model, score, ret_raw (= ln(zamknięcie/otwarcie) jutro)
    prices: ticker, date, next_open;  eurpln: Series kursu na dzień decyzji.
    Zwraca (dzienna krzywa kapitału, lista transakcji).
    """
    p = preds[preds["model"] == cfg.model].merge(prices, on=["ticker", "date"], how="left")
    p = p.dropna(subset=["score", "ret_raw", "next_open"])
    p = p[p["next_open"] > 0]
    days = sorted(p["date"].unique())
    by_day = {d: g.sort_values(["score", "ticker"], ascending=[False, True]) for d, g in p.groupby("date")}
    thr = thresholds({d: g["score"].to_numpy() for d, g in by_day.items()}, cfg.top_q) if cfg.top_q else {}

    equity = cfg.capital
    month, turnover_eur = None, 0.0
    curve, trades = [], []
    slip = cfg.slippage_bp / 1e4

    def commission(value_pln, rate_eur):
        nonlocal turnover_eur
        v_eur = value_pln / rate_eur
        before = turnover_eur
        turnover_eur += v_eur
        if turnover_eur <= cfg.free_turnover_eur:
            return 0.0
        charged_eur = v_eur if before >= cfg.free_turnover_eur else turnover_eur - cfg.free_turnover_eur
        return max(cfg.commission_rate * charged_eur, cfg.min_commission_eur) * rate_eur

    for d in days:
        m = (d.year, d.month)            # miesiąc liczymy po dniu decyzji (handel następnego dnia - przybliżenie)
        if m != month:
            month, turnover_eur = m, 0.0
        g = by_day[d]
        if cfg.top_q:
            g = g[g["score"] >= thr[d]]
        g = g.head(cfg.k)
        rate = float(eurpln.get(d, 4.30))
        day_pnl = 0.0
        slot = equity * min(1.0 / cfg.k, cfg.max_weight)
        for _, r in g.iterrows():
            buy_px = r["next_open"] * (1 + slip)
            shares = np.floor(slot / buy_px) if cfg.whole_shares else slot / buy_px
            if shares <= 0:
                continue
            sell_px = r["next_open"] * np.exp(r["ret_raw"]) * (1 - slip)
            buy_val, sell_val = shares * buy_px, shares * sell_px
            fee = commission(buy_val, rate) + commission(sell_val, rate)
            pnl = sell_val - buy_val - fee
            day_pnl += pnl
            trades.append({"date": d, "ticker": r["ticker"], "shares": shares, "buy": buy_val,
                           "pnl": pnl, "fee": fee, "gross": sell_val - buy_val})
        equity += day_pnl
        curve.append((d, equity))
        if equity <= 0:
            break
    eq = pd.Series(dict(curve), name="equity")
    eq.index = pd.DatetimeIndex(eq.index)
    return eq, pd.DataFrame(trades)


def metrics(eq, trades, capital, all_days=None):
    """Miary wyniku. all_days: wszystkie dni okresu (dni bez transakcji = 0% zwrotu)."""
    if all_days is not None:
        eq = eq.reindex(all_days).ffill().fillna(capital)
    r = eq.pct_change().fillna(eq.iloc[0] / capital - 1)
    years = max(len(eq) / 250, 1e-9)
    final = float(eq.iloc[-1])
    cagr = (final / capital) ** (1 / years) - 1 if final > 0 else -1.0
    vol = r.std() * np.sqrt(250)
    sharpe = r.mean() / (r.std() + 1e-12) * np.sqrt(250)
    dd = (eq / eq.cummax() - 1).min()
    n = len(trades)
    return {
        "koncowy": final, "rocznie": cagr, "zmiennosc": vol, "sharpe": sharpe,
        "t": sharpe * np.sqrt(years), "obsuniecie": dd,
        "transakcji": n, "dni_z_handlem": trades["date"].nunique() if n else 0,
        "trafnosc": float((trades["pnl"] > 0).mean()) if n else np.nan,
        "prowizje": float(trades["fee"].sum()) if n else 0.0,
        "brutto": float(trades["gross"].sum()) if n else 0.0,
        "dni": len(eq),
    }


def yearly_returns(eq, capital, all_days=None):
    """Zwrot w każdym roku kalendarzowym (tylko lata, w których trwał test)."""
    if all_days is not None:
        eq = eq.reindex(all_days).ffill().fillna(capital)
    r = eq.pct_change()
    r.iloc[0] = eq.iloc[0] / capital - 1
    return (1 + r).groupby(r.index.year).prod() - 1


# ============================================================================
# RAPORT
# ============================================================================

def zl(x):
    return f"{x:,.0f}".replace(",", " ")


def fmt_row(name, m, width=52):
    hit = "     -" if pd.isna(m["trafnosc"]) else f"{m['trafnosc']:>6.1%}"
    return (f"  {name:<{width}} {zl(m['koncowy']):>9} zł {m['rocznie']:>+7.1%} {m['sharpe']:>+6.2f} "
            f"{m['obsuniecie']:>7.1%} {m['transakcji']:>6} {zl(m['prowizje']):>8} zł {hit}")


def header(width=52):
    return (f"  {'konfiguracja':<{width}} {'kapitał na koniec':>12} {'rocznie':>7} {'Sharpe':>6} "
            f"{'obsun.':>7} {'transak.':>6} {'prowizje':>11} {'trafn.':>6}")


def run(ds, preds, prices, eurpln, out=print, save=True):
    days = pd.DatetimeIndex(sorted(preds["date"].unique()))
    bench = market_benchmark(ds, days) if ds is not None else pd.Series(0.0, index=days)
    models = list(pd.unique(preds["model"]))
    results = []

    def test(cfg):
        eq, tr = simulate(preds, prices, eurpln, cfg)
        m = metrics(eq, tr, cfg.capital, all_days=days)
        if save:
            M.register_trial({"backtest": asdict(cfg)})
        results.append((cfg, m, eq, tr))
        return m

    out(f"Okres testu: {days.min().date()} – {days.max().date()} ({len(days)} sesji), prognozy na ślepo z etapu 4.")
    out("Kupno na otwarciu, sprzedaż na zamknięciu tego samego dnia. Prowizja XTB z limitem 100 000 EUR/mies.,")
    out("całe akcje, BEZ podatku Belki (na IKE/IKZE go nie ma). Sejf (ostatni rok) nadal nietknięty.\n")

    # punkt odniesienia: rynek kupiony i trzymany
    b_eq = 5000.0 * (1 + bench).cumprod()
    bm = metrics(b_eq, pd.DataFrame(columns=["date", "pnl", "fee", "gross"]), 5000.0)
    out("1) Wszyscy zawodnicy: 5 000 zł, 5 spółek dziennie, bez progu")
    out(header())
    out(fmt_row("średnia spółek GPW - kup i trzymaj (bez kosztów)", {**bm, "trafnosc": np.nan}))
    for slip in (0.0, 10.0):
        out(f"  -- poślizg {slip:g} pb na stronę " + ("(zlecenia na fixing)" if slip == 0 else "(zlecenia w trakcie sesji)"))
        for mname in models:
            m = test(Config(model=mname, slippage_bp=slip))
            out(fmt_row(mname, m))

    best_models = [x for x in ("lightgbm", "logistyczna") if x in models] or models[-1:]
    out("\n2) Wielkość kapitału: przy większym obrót szybciej przekracza darmowy limit prowizji")
    out(header())
    for mname in best_models:
        for cap in (5000.0, 20000.0, 50000.0):
            m = test(Config(model=mname, capital=cap))
            out(fmt_row(f"{mname} | {zl(cap)} zł", m))

    out("\n3) Mniej, ale pewniej: mniej spółek i próg z przeszłych ocen (20 000 zł - tu limit prowizji boli)")
    out(header())
    for mname in best_models:
        for k, q in ((5, None), (3, None), (5, 0.10), (3, 0.05), (2, 0.02)):
            m = test(Config(model=mname, capital=20000.0, k=k, top_q=q))
            qtxt = f", tylko top {q:.0%} ocen" if q else ""
            out(fmt_row(f"{mname} | {k} spółek{qtxt}", m))

    # najlepsza konfiguracja wg Sharpe (poza losowym)
    cand = [x for x in results if x[0].model != "losowo"]
    cfg, m, eq, tr = max(cand, key=lambda x: x[1]["sharpe"])
    out(f"\n4) Najlepsza tutaj (wg Sharpe): {cfg.label()}")
    yr = yearly_returns(eq, cfg.capital, all_days=days)
    byr = yearly_returns(b_eq, 5000.0)
    out("  rok        strategia   średnia spółek GPW")
    for y in yr.index:
        out(f"  {y}      {yr[y]:>+9.1%}   {byr.get(y, np.nan):>+9.1%}")
    gross, fees = m["brutto"], m["prowizje"]
    out(f"  Zysk przed prowizjami {zl(gross)} zł, prowizje {zl(fees)} zł, "
        f"średnio {m['transakcji'] / max(m['dni_z_handlem'], 1):.1f} spółki w dniu z handlem, "
        f"handel w {m['dni_z_handlem'] / len(days):.0%} sesji.")

    n_cfg = len(results)
    trials = M.register_trial({"backtest_run": [asdict(c) for c, *_ in results]}) if save else 1
    ct = M.chance_t(max(n_cfg, trials))
    out(f"\n  Licznik prób: w tym przebiegu {n_cfg} konfiguracji. Najlepsza z tylu przypadkowych miałaby t ≈ {ct:.1f}; "
        f"ta ma t {m['t']:+.1f} - " + ("ponad przypadek." if m["t"] > ct + 1 else "to może być przypadek."))
    out("  Uwaga: 'najlepsza' wybrana spośród wielu - jej wynik jest trochę zawyżony. Rozstrzygnie sejf (ostatni rok).")

    if save:
        path = os.path.join(F.lab_dir(), "backtest_equity.parquet")
        curves = pd.DataFrame({"rynek": b_eq})
        for c, mm, e, _ in results:
            if c.slippage_bp == 0 and c.capital == 5000.0 and c.k == 5 and not c.top_q:
                curves[c.model] = e.reindex(days).ffill().fillna(c.capital)
        curves.index.name = "date"
        try:
            curves.reset_index().to_parquet(path, index=False)
            out(f"  Krzywe kapitału zapisane: {path} (do zakładki w aplikacji)")
        except Exception:
            pass
    return results


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m lab.backtest", description="QuantPulse - backtest z kosztami XTB")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="symulacja handlu z kosztami dla prognoz z etapu 4")
    p.parse_args(argv)

    from .data_store import MarketStore
    preds = load_predictions("oc")
    if preds is None:
        print("Brak prognoz - najpierw: python -m lab.models run")
        return 1
    store = MarketStore()
    t0 = time.time()
    prices = next_open_prices(store, sorted(preds["ticker"].unique()))
    eurpln = eur_rate(store, pd.DatetimeIndex(sorted(preds["date"].unique())))
    ds = F.load_dataset()
    run(ds, preds, prices, eurpln)
    print(f"\nGotowe w {time.time() - t0:.0f} s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
