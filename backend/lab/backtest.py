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
    short_k: int = 0                # ile najgorzej ocenionych spółek sprzedajemy krótko (tylko CFD)
    instrument: str = "akcje"       # "akcje" (prowizja XTB z limitem) albo "cfd" (bez prowizji, spread)
    cfd_spread_bp: float = 30.0     # spread CFD na polskie akcje w XTB: 0.30% (po 0.15% na stronę)
    gross: float = 1.0              # łączna wartość pozycji jako wielokrotność kapitału (dźwignia)

    def label(self):
        q = f", próg top {self.top_q:.0%}" if self.top_q else ""
        side = f"{self.k} kupno + {self.short_k} krótko" if self.short_k else f"{self.k} spółek"
        inst = f" | CFD spread {self.cfd_spread_bp / 100:.2f}%" if self.instrument == "cfd" else ""
        return f"{self.model} | {side}{q} | {zl(self.capital)} zł | poślizg {self.slippage_bp:g} pb{inst}"


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

def thresholds(day_scores, top_q, side="top"):
    """
    Próg na każdy dzień z ocen z poprzednich THRESHOLD_LOOKBACK dni (tylko przeszłość).
    side="top": kwantyl 1 - top_q (do kupna); side="bottom": kwantyl top_q (do krótkiej sprzedaży).
    """
    out, hist = {}, []
    q = 1 - top_q if side == "top" else top_q
    blocked = np.inf if side == "top" else -np.inf
    for d in sorted(day_scores):
        if len(hist) >= THRESHOLD_MIN_DAYS:
            past = np.concatenate(hist[-THRESHOLD_LOOKBACK:])
            out[d] = float(np.quantile(past, q))
        else:
            out[d] = blocked
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
    scores = {d: g["score"].to_numpy() for d, g in by_day.items()}
    thr = thresholds(scores, cfg.top_q) if cfg.top_q else {}
    thr_low = thresholds(scores, cfg.top_q, side="bottom") if (cfg.top_q and cfg.short_k) else {}

    equity = cfg.capital
    month, turnover_eur = None, 0.0
    curve, trades = [], []
    cfd = cfg.instrument == "cfd"
    # koszt na stronę jako ułamek ceny: poślizg, a przy CFD dodatkowo połowa spreadu
    side_cost = cfg.slippage_bp / 1e4 + (cfg.cfd_spread_bp / 2e4 if cfd else 0.0)
    n_pos = max(cfg.k + cfg.short_k, 1)

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
        day = by_day[d]
        longs = day[day["score"] >= thr[d]] if cfg.top_q else day
        longs = longs.head(cfg.k)
        shorts = day.iloc[::-1]                                   # od najgorzej ocenionej
        shorts = shorts[~shorts["ticker"].isin(longs["ticker"])]
        if cfg.top_q and cfg.short_k:
            shorts = shorts[shorts["score"] <= thr_low[d]]
        shorts = shorts.head(cfg.short_k)
        rate = float(eurpln.get(d, 4.30))
        day_pnl = 0.0
        slot = equity * cfg.gross * min(1.0 / n_pos, cfg.max_weight)
        for side, rows in ((1, longs), (-1, shorts)):
            for _, r in rows.iterrows():
                open_px = r["next_open"]
                close_px = open_px * np.exp(r["ret_raw"])
                entry = open_px * (1 + side * side_cost)         # kupno drożej / krótka sprzedaż taniej
                exit_ = close_px * (1 - side * side_cost)        # sprzedaż taniej / odkupienie drożej
                shares = np.floor(slot / entry) if cfg.whole_shares else slot / entry
                if shares <= 0:
                    continue
                gross = side * shares * (close_px - open_px)     # wynik przed wszystkimi kosztami
                fee = 0.0 if cfd else commission(shares * entry, rate) + commission(shares * exit_, rate)
                pnl = side * shares * (exit_ - entry) - fee
                day_pnl += pnl
                trades.append({"date": d, "ticker": r["ticker"], "side": side, "shares": shares,
                               "buy": shares * entry, "pnl": pnl, "fee": gross - pnl, "gross": gross})
        equity += day_pnl
        curve.append((d, equity))
        if equity <= 0:
            break
    eq = pd.Series(dict(curve), name="equity")
    eq.index = pd.DatetimeIndex(eq.index)
    return eq, pd.DataFrame(trades)


def excess_t(r, bench_r):
    """
    Czy strategia bije punkt odniesienia ponad przypadek? t-statystyka różnicy zwrotów
    (strategia minus trzymanie) w każdym okresie. Około 2 = na granicy, powyżej 3 = wyraźnie.
    """
    d = (r - bench_r.reindex(r.index).fillna(0.0)).dropna()
    if len(d) < 3 or d.std() == 0:
        return np.nan
    return float(d.mean() / d.std() * np.sqrt(len(d)))


def metrics(eq, trades, capital, all_days=None, bench_r=None):
    """
    Miary wyniku. all_days: wszystkie dni okresu (dni bez transakcji = 0% zwrotu).
    bench_r: dzienne zwroty punktu odniesienia (do testu, czy przewaga nad nim nie jest przypadkiem).
    """
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
        "t_ex": excess_t(r, bench_r) if bench_r is not None else np.nan,
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
            f"{'obsun.':>7} {'transak.':>6} {'koszty':>11} {'trafn.':>6}")


def zl10k(m, capital):
    """Ile zostałoby z 10 000 zł przy tym samym przebiegu (przeliczenie proporcjonalne)."""
    return m["koncowy"] / capital * 10000


def plural(n, one, few, many):
    """Polska odmiana: 1 wariant, 2-4 warianty, 5+ wariantów (12-14 też 'wariantów')."""
    n = int(round(n))
    if n == 1:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def verdict(what, best_cfg, best_m, hold_m, hold_capital, rand_m, n_cfg, years, cmd, note=""):
    """Werdykt w prostych słowach: 3 pytania i wniosek. Bez żargonu."""
    ct = M.chance_t(n_cfg)
    beats = best_m["rocznie"] > hold_m["rocznie"]
    t_ex = best_m.get("t_ex", np.nan)
    trusted = beats and pd.notna(t_ex) and t_ex > ct + 1
    borderline = beats and pd.notna(t_ex) and ct < t_ex <= ct + 1
    best10, hold10, rand10 = zl10k(best_m, best_cfg.capital), zl10k(hold_m, hold_capital), zl10k(rand_m, 5000.0)

    def money(x):
        diff = x - 10000
        return f"{zl(x)} zł ({'zysk' if diff >= 0 else 'strata'} {zl(abs(diff))} zł)"

    ny = max(int(round(years)), 1)
    lat = f"{ny} {plural(ny, 'rok', 'lata', 'lat')}"
    lines = ["=" * 78, "WERDYKT (w prostych słowach)", "=" * 78,
             f"Sprawdzane: {what}",
             f"Okres: ok. {lat} historii, opłaty XTB wliczone. {note}".rstrip(), ""]
    lines.append("1. Czy to lepsze niż zwykłe kupienie akcji i trzymanie ich?")
    lines.append(f"   {'TAK' if beats else 'NIE'}. Najlepszy z {n_cfg} sprawdzonych wariantów: {best_m['rocznie']:+.1%} rocznie. "
                 f"Zwykłe trzymanie spółek GPW: {hold_m['rocznie']:+.1%} rocznie.")
    lines.append(f"2. Ile zostałoby z 10 000 zł po ok. {lat}?" if ny == 1 else
                 f"2. Ile zostałoby z 10 000 zł po ok. {ny} {plural(ny, 'roku', 'latach', 'latach')}?")
    lines.append(f"   najlepszy wariant: {money(best10)}")
    lines.append(f"   zwykłe trzymanie:  {money(hold10)}")
    lines.append(f"   losowe typowanie:  {money(rand10)}")
    lines.append("3. Czy można ufać, że ta przewaga nad zwykłym trzymaniem nie jest przypadkiem?")
    if best_m["rocznie"] <= 0:
        lines.append("   Nie ma tu czemu ufać - nawet najlepszy wariant traci pieniądze.")
    elif not beats:
        lines.append("   Nie ma czego potwierdzać - nawet najlepszy wariant nie bije zwykłego trzymania.")
    elif trusted:
        lines.append(f"   RACZEJ TAK. Przewaga jest wyraźnie większa, niż zdarza się przypadkiem przy {n_cfg} próbach.")
        lines.append("   Ostatecznie potwierdzi to dopiero sejf (ostatni rok, którego nikt jeszcze nie oglądał).")
    elif borderline:
        lines.append(f"   NA GRANICY. Przewaga jest trochę większa niż typowy 'szczęśliwy traf' przy {n_cfg} próbach,")
        lines.append("   ale za mało, żeby na niej polegać. Potrzeba więcej dowodów (sejf, gra na papierze).")
    else:
        lines.append(f"   NIE. Sprawdziliśmy {n_cfg} {plural(n_cfg, 'wariant', 'warianty', 'wariantów')} - przy tylu "
                     "próbach jeden, który bije trzymanie o tyle, trafia się zwykle przypadkiem.")
    lines.append("")
    if beats and trusted:
        lines.append("WNIOSEK: obiecujące. Następny krok to gra na papierze - nadal BEZ prawdziwych pieniędzy.")
    elif borderline:
        lines.append("WNIOSEK: najciekawszy wynik do tej pory, ale jeszcze nie dowód. Nie wkładaj pieniędzy -")
        lines.append("         sprawdźmy dalej (szczegóły, stabilność rok po roku, potem sejf).")
    elif beats:
        lines.append("WNIOSEK: wygląda nieźle, ale za mało dowodów. Nie wkładaj w to pieniędzy.")
    else:
        lines.append("WNIOSEK: nie wkładaj w to pieniędzy. Zwykłe trzymanie akcji (np. ETF na IKE) wypada lepiej.")
    if cmd:
        lines.append(f"(Szczegółowe tabele: {cmd} --szczegoly)")
    return lines


def run(ds, preds, prices, eurpln, out=print, save=True, details=True):
    days = pd.DatetimeIndex(sorted(preds["date"].unique()))
    buf = []
    say = buf.append
    bench = market_benchmark(ds, days) if ds is not None else pd.Series(0.0, index=days)
    models = list(pd.unique(preds["model"]))
    results = []

    def test(cfg):
        eq, tr = simulate(preds, prices, eurpln, cfg)
        m = metrics(eq, tr, cfg.capital, all_days=days, bench_r=bench)
        if save:
            M.register_trial({"backtest": asdict(cfg)})
        results.append((cfg, m, eq, tr))
        return m

    say(f"Okres testu: {days.min().date()} – {days.max().date()} ({len(days)} sesji), prognozy na ślepo z etapu 4.")
    say("Kupno na otwarciu, sprzedaż na zamknięciu tego samego dnia. Prowizja XTB z limitem 100 000 EUR/mies.,")
    say("całe akcje, BEZ podatku Belki (na IKE/IKZE go nie ma). Sejf (ostatni rok) nadal nietknięty.\n")

    # punkt odniesienia: rynek kupiony i trzymany
    b_eq = 5000.0 * (1 + bench).cumprod()
    bm = metrics(b_eq, pd.DataFrame(columns=["date", "pnl", "fee", "gross"]), 5000.0)
    say("1) Wszyscy zawodnicy: 5 000 zł, 5 spółek dziennie, bez progu")
    say(header())
    say(fmt_row("średnia spółek GPW - kup i trzymaj (bez kosztów)", {**bm, "trafnosc": np.nan}))
    for slip in (0.0, 10.0):
        say(f"  -- poślizg {slip:g} pb na stronę " + ("(zlecenia na fixing)" if slip == 0 else "(zlecenia w trakcie sesji)"))
        for mname in models:
            m = test(Config(model=mname, slippage_bp=slip))
            say(fmt_row(mname, m))

    best_models = [x for x in ("lightgbm", "logistyczna") if x in models] or models[-1:]
    say("\n2) Wielkość kapitału: przy większym obrót szybciej przekracza darmowy limit prowizji")
    say(header())
    for mname in best_models:
        for cap in (5000.0, 20000.0, 50000.0):
            m = test(Config(model=mname, capital=cap))
            say(fmt_row(f"{mname} | {zl(cap)} zł", m))

    say("\n3) Mniej, ale pewniej: mniej spółek i próg z przeszłych ocen (20 000 zł - tu limit prowizji boli)")
    say(header())
    for mname in best_models:
        for k, q in ((5, None), (3, None), (5, 0.10), (3, 0.05), (2, 0.02)):
            m = test(Config(model=mname, capital=20000.0, k=k, top_q=q))
            qtxt = f", tylko top {q:.0%} ocen" if q else ""
            say(fmt_row(f"{mname} | {k} spółek{qtxt}", m))

    # najlepsza konfiguracja wg Sharpe (poza losowym)
    cand = [x for x in results if x[0].model != "losowo"]
    cfg, m, eq, tr = max(cand, key=lambda x: x[1]["sharpe"])
    say(f"\n4) Najlepsza tutaj (wg Sharpe): {cfg.label()}")
    yr = yearly_returns(eq, cfg.capital, all_days=days)
    byr = yearly_returns(b_eq, 5000.0)
    say("  rok        strategia   średnia spółek GPW")
    for y in yr.index:
        say(f"  {y}      {yr[y]:>+9.1%}   {byr.get(y, np.nan):>+9.1%}")
    gross, fees = m["brutto"], m["prowizje"]
    say(f"  Zysk przed kosztami {zl(gross)} zł, koszty (prowizje i poślizg) {zl(fees)} zł, "
        f"średnio {m['transakcji'] / max(m['dni_z_handlem'], 1):.1f} spółki w dniu z handlem, "
        f"handel w {m['dni_z_handlem'] / len(days):.0%} sesji.")

    n_cfg = len(results)
    trials = M.register_trial({"backtest_run": [asdict(c) for c, *_ in results]}) if save else 1
    ct = M.chance_t(max(n_cfg, trials))
    say(f"\n  Licznik prób: w tym przebiegu {n_cfg} konfiguracji. Najlepsza z tylu przypadkowych miałaby t ≈ {ct:.1f}; "
        f"ta ma t {m['t']:+.1f} - " + ("ponad przypadek." if m["t"] > ct + 1 else "to może być przypadek."))
    say("  Uwaga: 'najlepsza' wybrana spośród wielu - jej wynik jest trochę zawyżony. Rozstrzygnie sejf (ostatni rok).")

    if save:
        path = os.path.join(F.lab_dir(), "backtest_equity.parquet")
        curves = pd.DataFrame({"rynek": b_eq})
        for c, mm, e, _ in results:
            if c.slippage_bp == 0 and c.capital == 5000.0 and c.k == 5 and not c.top_q:
                curves[c.model] = e.reindex(days).ffill().fillna(c.capital)
        curves.index.name = "date"
        try:
            curves.reset_index().to_parquet(path, index=False)
            say(f"  Krzywe kapitału zapisane: {path} (do zakładki w aplikacji)")
        except Exception:
            pass

    rand = next(m for c, m, *_ in results if c.model == "losowo" and c.slippage_bp == 0)
    realistic = [x for x in results if x[0].model != "losowo" and x[0].slippage_bp == 0]
    bc, bmm, *_ = max(realistic, key=lambda x: x[1]["sharpe"])
    for line in verdict("kupno akcji rano i sprzedaż wieczorem tego samego dnia, spółki wybrane przez model",
                        bc, bmm, bm, 5000.0, rand, len(results), len(days) / 250,
                        None if details else "python -m lab.backtest run",
                        note="Założenie korzystne: zlecenia po cenie z fixingu (bez poślizgu)."):
        out(line)
    if details:
        out("")
        for line in buf:
            out(line)
    return results


def run_cfd(ds, preds, prices, eurpln, out=print, save=True, capital=20000.0, details=True):
    """Gra na różnicy przez CFD: kupno najlepiej ocenionych i krótka sprzedaż najgorzej ocenionych."""
    days = pd.DatetimeIndex(sorted(preds["date"].unique()))
    buf = []
    say = buf.append
    bench = market_benchmark(ds, days) if ds is not None else pd.Series(0.0, index=days)
    models = [m for m in ("lightgbm", "logistyczna", "reguła: najbliżej rocznego szczytu", "losowo")
              if m in set(preds["model"])]
    results = []

    def test(cfg):
        eq, tr = simulate(preds, prices, eurpln, cfg)
        m = metrics(eq, tr, cfg.capital, all_days=days, bench_r=bench)
        if save:
            M.register_trial({"backtest": asdict(cfg)})
        results.append((cfg, m, eq, tr))
        return m, tr

    say(f"Okres testu: {days.min().date()} – {days.max().date()} ({len(days)} sesji), prognozy na ślepo z etapu 4.")
    say("Gra na różnicy (CFD): rano kupno najlepiej ocenionych i krótka sprzedaż najgorzej ocenionych spółek,")
    say("wieczorem zamknięcie obu stron. Ruch całego rynku się znosi - zostaje sama różnica między spółkami.")
    say("CFD na polskie akcje w XTB: prowizja 0%, spread 0.30% (po 0.15% przy otwarciu i zamknięciu),")
    say("bez opłat za noc (zamykamy tego samego dnia). Pozycje łącznie = kapitał (bez dodatkowej dźwigni).")
    say("Uwaga: XTB nie ma CFD na wszystkie spółki z listy - wynik zakłada, że każdą da się sprzedać krótko.\n")

    say(f"5) Gra na różnicy, kapitał {zl(capital)} zł")
    say(header())
    W = 52
    for spread, title in ((0.0, "BEZ kosztów - ile w ogóle jest do zarobienia"),
                          (30.0, "spread XTB 0.30%"),
                          (10.0, "gdyby spread był 0.10% (dla porównania)")):
        say(f"  -- {title}")
        for mname in models:
            for k, q in ((5, None), (3, 0.05), (1, 0.01)):
                if mname == "losowo" and q:
                    continue
                cfg = Config(model=mname, k=k, short_k=k, top_q=q, capital=capital,
                             instrument="cfd", cfd_spread_bp=spread)
                m, _ = test(cfg)
                qtxt = f", top {q:.0%}" if q else ""
                say(fmt_row(f"{mname} | {k}+{k}{qtxt}"[:W], m, W))

    say("\n  Dla porównania: samo kupno przez CFD (bez krótkiej sprzedaży), spread 0.30%")
    for mname in models[:2]:
        m, _ = test(Config(model=mname, k=5, capital=capital, instrument="cfd"))
        say(fmt_row(f"{mname} | 5 kupno, CFD", m, W))

    # próg opłacalności: przy jakim spreadzie strategia wychodzi na zero
    say("\n  Próg opłacalności (z wersji bez kosztów): średni zysk na transakcję przed kosztami")
    for cfg, m, eq, tr in results:
        if cfg.cfd_spread_bp == 0 and cfg.short_k and len(tr) and cfg.model != "losowo":
            per_trade = tr["gross"].sum() / tr["buy"].sum()
            qtxt = f", top {cfg.top_q:.0%}" if cfg.top_q else ""
            say(f"  {cfg.model + ' | ' + str(cfg.k) + '+' + str(cfg.k) + qtxt:<52} "
                f"{per_trade * 100:+.3f}% na transakcję -> opłaca się, gdy spread < {max(per_trade, 0) * 100:.2f}% "
                f"(XTB: 0.30%)")
    best = max((x for x in results if x[0].cfd_spread_bp == 30.0 and x[0].model != "losowo"),
               key=lambda x: x[1]["sharpe"])
    say(f"\n  Najlepsza przy prawdziwym spreadzie: {best[0].label()}")
    say(f"  -> {best[1]['rocznie']:+.1%} rocznie, Sharpe {best[1]['sharpe']:+.2f}, t {best[1]['t']:+.1f} "
        f"(przypadkiem przy {len(results)} konfiguracjach: t ≈ {M.chance_t(len(results)):.1f}).")

    b_eq = 5000.0 * (1 + bench).cumprod()
    hold = metrics(b_eq, pd.DataFrame(columns=["date", "pnl", "fee", "gross"]), 5000.0)
    rand = next((m for c, m, *_ in results if c.model == "losowo" and c.cfd_spread_bp == 30.0), best[1])
    real = [x for x in results if x[0].cfd_spread_bp == 30.0 and x[0].model != "losowo"]
    n_real = len(real) + 2                      # + dwa warianty "samo kupno przez CFD"
    rand_scaled = {**rand, "koncowy": rand["koncowy"] / capital * 5000.0}
    for line in verdict("gra na różnicy przez CFD - rano kupno najlepszych i krótka sprzedaż najgorszych spółek",
                        best[0], best[1], hold, 5000.0, rand_scaled, n_real, len(days) / 250,
                        None if details else "python -m lab.backtest cfd",
                        note="Spread XTB 0.30%. CFD to dźwignia i ryzyko - większość kont detalicznych na CFD traci."):
        out(line)
    if details:
        out("")
        for line in buf:
            out(line)
    return results


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m lab.backtest", description="QuantPulse - backtest z kosztami XTB")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, hlp in (("run", "symulacja handlu akcjami z kosztami dla prognoz z etapu 4"),
                      ("cfd", "gra na różnicy przez CFD: kupno najlepszych, krótka sprzedaż najgorszych")):
        sp = sub.add_parser(name, help=hlp)
        sp.add_argument("--szczegoly", action="store_true", help="pokaż też wszystkie tabele")
    args = p.parse_args(argv)

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
    if args.cmd == "cfd":
        run_cfd(ds, preds, prices, eurpln, details=args.szczegoly)
    else:
        run(ds, preds, prices, eurpln, details=args.szczegoly)
    print(f"\nGotowe w {time.time() - t0:.0f} s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
