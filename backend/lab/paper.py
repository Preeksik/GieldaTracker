"""
QuantPulse - gra na papierze (bez prawdziwych pieniędzy).

Jedno polecenie, uruchamiane raz na jakiś czas (najlepiej wieczorem, po sesji):

    cd backend
    python -m lab.paper run            # aktualizacja danych + rozliczenie + nowe typy, jeśli minął miesiąc
    python -m lab.paper run --szybko   # bez pobierania notowań (gdy już są świeże)
    python -m lab.paper status         # tylko podgląd dziennika

CO SIĘ DZIEJE:
  1. Dociąga najnowsze notowania i przebudowuje tabelę cech.
  2. Rozlicza poprzednie typy: kupno na zamknięciu sesji PO dniu wskazania, sprzedaż na zamknięciu
     sesji po następnym wskazaniu (dokładnie jak w testach). Koszt: 0.10% od każdej kupionej
     i sprzedanej spółki. Punkt odniesienia: wszystkie spółki GPW z listy po równo.
  3. Jeśli od ostatnich typów minęło co najmniej 20 sesji (albo to pierwszy raz) - nowe typy
     A1, A2, A3 (po 10 spółek). Sieć uczy się przy tym na całej historii do dziś.

Strategie są te same, które przeszły sejf (lab/vault.py) - bez żadnych zmian.
Dziennik: backend/data/lab/paper_journal.json. Laboratorium NIE łączy się z kontem w XTB.
"""

import os
import sys
import json
import argparse
from datetime import datetime

import numpy as np
import pandas as pd

from . import features as F
from . import models as M
from . import swing as S
from . import backtest as B
from . import vault as V

H = V.H                       # 20 sesji = zmiana składu co miesiąc
K = S.K
SIDE_COST = S.SLIPPAGE_BP / 1e4


def journal_path():
    return os.path.join(F.lab_dir(), "paper_journal.json")


def load_journal(path=None):
    try:
        with open(path or journal_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"created": datetime.now().isoformat(timespec="seconds"), "sets": []}


def save_journal(j, path=None):
    path = path or journal_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(j, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


# ============================================================================
# TYPY
# ============================================================================

def today_rows(ds, market="PL"):
    """Spółki do oceny na dziś: ostatnia sesja, komplet cech, płynniejsza połowa - jak w testach."""
    d = ds[(ds["market"] == market) & ds["predict_row"]].copy()
    if d.empty:
        return d, None
    day = d["date"].max()
    d = d[d["date"] == day]
    med = d["turnover_20"].median()
    d = d[d["turnover_20"] >= med].copy()
    for f in M.STOCK_FEATURES:
        d[f] = d[f].rank(pct=True) - 0.5
    for c in [c for c in F.SEQ_COLS if c.startswith("seq_m") and c in d.columns]:
        d[c] = d[c].rank(pct=True) - 0.5
    return d, day


def make_picks(ds, last_close, capital):
    """Nowe typy A1/A2/A3. last_close: ticker -> ostatnia cena (do pominięcia zbyt drogich akcji)."""
    train = S.prepare_h(ds, H, include_vault=True)
    today, day = today_rows(ds)
    if today.empty or len(today) < K:
        return None, day
    slot = capital / K
    picks = {}
    for key in V.FROZEN:
        model = V.make_model(key).fit(train)
        scored = today.assign(score=model.score(today)).sort_values(["score", "ticker"], ascending=[False, True])
        ok = [t for t in scored["ticker"] if last_close.get(t, np.inf) <= slot]
        picks[key] = ok[:K]
    return picks, day


# ============================================================================
# ROZLICZENIE
# ============================================================================

def next_session(calendar, d):
    i = calendar.searchsorted(pd.Timestamp(d), side="right")
    return calendar[i] if i < len(calendar) else None


def period_return(prices, tickers, entry, exit_):
    """Średni zwrot spółek od zamknięcia dnia entry do zamknięcia dnia exit_ (ceny skorygowane)."""
    rets = []
    for t in tickers:
        s = prices.get(t)
        if s is None or entry not in s.index or exit_ not in s.index:
            continue
        a, b = s.loc[entry], s.loc[exit_]
        if pd.notna(a) and pd.notna(b) and a > 0:
            rets.append(b / a - 1)
    return (float(np.mean(rets)), len(rets)) if rets else (np.nan, 0)


def settle(journal, prices, calendar, all_tickers):
    """
    Dla każdego zestawu typów liczy wynik: od kupna (zamknięcie sesji po wskazaniu) do sprzedaży
    (zamknięcie sesji po następnym wskazaniu) albo - dla bieżącego - do ostatniej znanej sesji.
    """
    sets = journal["sets"]
    by_strategy = {}
    for s in sets:
        by_strategy.setdefault(s["strategy"], []).append(s)
    last_day = calendar[-1] if len(calendar) else None
    for strat, items in by_strategy.items():
        items.sort(key=lambda s: s["decision_date"])
        prev_tickers = []
        for i, s in enumerate(items):
            entry = next_session(calendar, s["decision_date"])
            nxt = items[i + 1]["decision_date"] if i + 1 < len(items) else None
            exit_ = next_session(calendar, nxt) if nxt else None
            closed = exit_ is not None
            end = exit_ if closed else last_day
            s["entry_date"] = str(entry.date()) if entry is not None else None
            if entry is None or end is None or end <= entry:
                s.update({"status": "czeka na kupno", "return": None, "hold_return": None})
                prev_tickers = s["tickers"]
                continue
            r, n = period_return(prices, s["tickers"], entry, end)
            hr, _ = period_return(prices, all_tickers, entry, end)
            changed = len(set(s["tickers"]) - set(prev_tickers)) + len(set(prev_tickers) - set(s["tickers"]))
            cost = SIDE_COST * changed / K
            s.update({"status": "rozliczony" if closed else "otwarty", "end_date": str(end.date()),
                      "return": None if np.isnan(r) else r - cost, "hold_return": None if np.isnan(hr) else hr,
                      "priced": n})
            prev_tickers = s["tickers"]
    return journal


def summary(journal):
    """Wartość 10 000 zł od startu gry dla każdej strategii i dla trzymania (łańcuch okresów)."""
    out = {}
    for strat in V.FROZEN:
        items = sorted([s for s in journal["sets"] if s["strategy"] == strat and s.get("return") is not None],
                       key=lambda s: s["decision_date"])
        v, hv, better = 10000.0, 10000.0, 0
        for s in items:
            v *= 1 + s["return"]
            hv *= 1 + (s["hold_return"] or 0.0)
            better += int(s["return"] > (s["hold_return"] or 0.0))
        out[strat] = {"value": v, "hold": hv, "periods": len(items), "better": better}
    return out


# ============================================================================
# PRZEBIEG
# ============================================================================

def load_prices(store, tickers):
    prices = {}
    for t in tickers:
        df = store.load(t)
        if df is not None and len(df):
            prices[t] = df["adj_close"]
    return prices


def run(store, update=True, out=print, path=None, capital=S.CAPITAL, ds=None):
    if update:
        out("Pobieram najnowsze notowania…")
        store.update(progress=lambda *_: None)
    if ds is None:
        out("Liczę cechy…")
        ds = F.build_dataset(store, progress=lambda *_: None)
        if update:
            F.save_dataset(ds)
    pl = sorted(ds.loc[ds["market"] == "PL", "ticker"].unique())
    prices = load_prices(store, pl)
    calendar = pd.DatetimeIndex(sorted(ds.loc[ds["market"] == "PL", "date"].unique()))
    journal = load_journal(path)
    settle(journal, prices, calendar, pl)

    out("")
    out("=" * 78)
    out("GRA NA PAPIERZE - bez prawdziwych pieniędzy")
    out("=" * 78)
    sm = summary(journal)
    if any(v["periods"] for v in sm.values()):
        first = min(s["decision_date"] for s in journal["sets"])
        out(f"Od startu gry ({first}), z 10 000 zł:")
        for k, v in sm.items():
            verdict = "lepiej" if v["value"] > v["hold"] else "gorzej"
            out(f"  {k} {V.FROZEN[k]['opis']:<36} {B.zl(v['value']):>8} zł   (trzymanie: {B.zl(v['hold'])} zł, "
                f"{verdict}; miesiące lepsze od trzymania: {v['better']}/{v['periods']})")
        closed = [s for s in journal["sets"] if s.get("status") == "rozliczony"]
        openp = [s for s in journal["sets"] if s.get("status") == "otwarty"]
        if openp:
            out("  (ostatni okres jest jeszcze otwarty - liczony do ostatniej sesji, zmieni się do końca miesiąca)")
        if not closed:
            out("  Żaden pełny miesiąc nie jest jeszcze rozliczony.")
    else:
        out("Dziennik jest pusty - to pierwsze typy.")

    last = max((s["decision_date"] for s in journal["sets"]), default=None)
    day_now = calendar[-1] if len(calendar) else None
    due = last is None or (day_now is not None and
                           calendar.searchsorted(day_now, side="right") - calendar.searchsorted(pd.Timestamp(last), side="right") >= H)
    out("")
    if not due:
        passed = calendar.searchsorted(day_now, side="right") - calendar.searchsorted(pd.Timestamp(last), side="right")
        out(f"Nowe typy będą za {H - passed} sesji (ostatnie wskazanie: {last}). Bieżący skład:")
        for s in sorted([s for s in journal["sets"] if s["decision_date"] == last], key=lambda s: s["strategy"]):
            r = s.get("return")
            rtxt = f"{r:+.1%} od kupna" if r is not None else "czeka na kupno"
            out(f"  {s['strategy']}: {', '.join(s['tickers'])}   ({rtxt})")
        save_journal(journal, path)
        return journal

    last_close = {t: float(p.dropna().iloc[-1]) for t, p in
                  ((t, store.load(t)["close"]) for t in prices) if len(p.dropna())}
    picks, day = make_picks(ds, last_close, capital)
    if picks is None:
        out("Za mało danych na dziś, żeby wskazać spółki.")
        save_journal(journal, path)
        return journal
    created = datetime.now().isoformat(timespec="seconds")
    for k, tick in picks.items():
        journal["sets"].append({"strategy": k, "decision_date": str(day.date()), "tickers": tick,
                                "created_at": created, "capital": capital})
    out(f"NOWE TYPY na podstawie sesji {day.date()} (kupno na zamknięciu NASTĘPNEJ sesji, po równo, "
        f"{B.zl(capital)} zł -> ok. {B.zl(capital / K)} zł na spółkę):")
    for k, tick in picks.items():
        out(f"  {k} {V.FROZEN[k]['opis']}:")
        out(f"     {', '.join(tick)}")
    out(f"Następna zmiana składu: za {H} sesji (ok. miesiąc). Uruchom wtedy znowu: python -m lab.paper run")
    out("To tylko zapis na papierze - nic nie jest kupowane. Spółki zbyt drogie na przydział zostały pominięte.")
    out(f"Dziennik: {path or journal_path()}")
    save_journal(journal, path)
    return journal


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m lab.paper", description="QuantPulse - gra na papierze")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="aktualizacja, rozliczenie i nowe typy (jeśli minął miesiąc)")
    r.add_argument("--szybko", action="store_true", help="bez pobierania notowań")
    r.add_argument("--kapital", type=float, default=S.CAPITAL, help="kwota na papierze (domyślnie 20 000 zł)")
    sub.add_parser("status", help="tylko podgląd dziennika (bez nowych typów)")
    args = p.parse_args(argv)
    import logging
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)   # bez komunikatów o ^WIG20 itp. - nie mają znaczenia

    from .data_store import MarketStore
    store = MarketStore()
    if args.cmd == "status":
        ds = F.load_dataset()
        if ds is None:
            print("Brak tabeli cech - uruchom: python -m lab.paper run")
            return 1
        j = load_journal()
        pl = sorted(ds.loc[ds["market"] == "PL", "ticker"].unique())
        cal = pd.DatetimeIndex(sorted(ds.loc[ds["market"] == "PL", "date"].unique()))
        settle(j, load_prices(store, pl), cal, pl)
        for k, v in summary(j).items():
            print(f"{k}: {B.zl(v['value'])} zł z 10 000 zł (trzymanie {B.zl(v['hold'])} zł), okresów: {v['periods']}")
        return 0
    if args.szybko:
        ds = F.load_dataset()
        run(store, update=False, ds=ds, capital=args.kapital)
    else:
        run(store, update=True, capital=args.kapital)
    return 0


if __name__ == "__main__":
    sys.exit(main())