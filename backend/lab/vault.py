"""
QuantPulse - sejf: jednorazowy sprawdzian zamrożonych strategii na ostatnim roku danych.

Ostatnie 12 miesięcy nie było używane w żadnym wcześniejszym teście. Tutaj, RAZ, sprawdzamy
na nich trzy strategie wybrane wcześniej (zamrożone - bez żadnych poprawek):

    A1  reguła "najbliżej rocznego szczytu"   10 spółek, zmiana co miesiąc
    A2  reguła "zwrot 6 miesięcy"             10 spółek, zmiana co miesiąc
    A3  sieć neuronowa                         10 spółek, zmiana co miesiąc

    cd backend
    python -m lab.vault run              # werdykt
    python -m lab.vault run --szczegoly  # z rozbiciem na miesiące i składem portfela

ZASADY UCZCIWOŚCI:
  - zasady handlu dokładnie jak w lab.swing (fixing zamknięcia, poślizg 0.10%, prowizja XTB, całe akcje),
  - sieć i reguły uczą się co miesiąc WYŁĄCZNIE na danych sprzed tego miesiąca (z przerwą 21 sesji),
    tak jak przy prawdziwym używaniu - wliczając w to miesiące sejfu, które już "minęły",
  - wynik jest pieczętowany w data/lab/vault.json razem z odciskiem konfiguracji. Jeśli konfiguracja
    się zmieni, laboratorium ostrzega, że to już NIE jest uczciwy test (sejf był już otwarty).
"""

import os
import sys
import json
import time
import hashlib
import argparse
from datetime import datetime

import numpy as np
import pandas as pd

from . import features as F
from . import models as M
from . import backtest as B
from . import swing as S
from .neural import NeuralModel

H = 20
FROZEN = {                       # zamrożone 2026-11-03 - NIE ZMIENIAĆ (inaczej test traci sens)
    "A1": {"opis": "reguła: najbliżej rocznego szczytu", "model": ("rule", "dist_high_250")},
    "A2": {"opis": "reguła: zwrot 6 miesięcy", "model": ("rule", "ret_120")},
    "A3": {"opis": "sieć neuronowa", "model": ("neural", None)},
}
SETTINGS = {"h": H, "k": S.K, "slippage_bp": S.SLIPPAGE_BP, "capital": S.CAPITAL, "liquid": True}


def config_hash():
    blob = json.dumps({"frozen": {k: v["model"] for k, v in FROZEN.items()}, "settings": SETTINGS,
                       "features": F.FEATURES, "seq": F.SEQ_COLS}, sort_keys=True, default=list)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def seal_path():
    return os.path.join(F.lab_dir(), "vault.json")


def make_model(key):
    kind, arg = FROZEN[key]["model"]
    if kind == "rule":
        return M.RuleModel(arg, FROZEN[key]["opis"].replace("reguła: ", ""))
    return NeuralModel()


def vault_window(ds):
    return ds["date"].max() - pd.Timedelta(days=F.VAULT_DAYS)


def predict_vault(ds, progress=print):
    """Prognozy dla dni zmiany składu w sejfie. Każdy miesiąc: nauka tylko na przeszłości."""
    vault_from = vault_window(ds)
    data = S.prepare_h(ds, H, include_vault=True)
    sessions = pd.DatetimeIndex(sorted(data["date"].unique()))
    reb = S.rebalance_dates(sessions[sessions >= vault_from], H)
    keys = list(FROZEN) + ["losowo"]
    models = {k: make_model(k) for k in FROZEN}
    models["losowo"] = M.RandomModel()
    out = []
    t0 = time.time()
    for i, d in enumerate(reb):
        first = sessions.searchsorted(d)
        train = data[data["date"] < sessions[first - (H + 1)]]          # przerwa H+1 sesji
        test = data[data["date"] == d]
        if train.empty or test.empty:
            continue
        part = test[["date", "ticker", "ret_ex", "ret_raw"]].copy()
        for k in keys:
            models[k].fit(train)
            part[k] = models[k].score(test)
        out.append(part)
        if progress:
            progress(f"  {d:%Y-%m-%d}: {i + 1}/{len(reb)} zmian składu, {time.time() - t0:.0f} s")
    p = pd.concat(out, ignore_index=True)
    long = p.melt(id_vars=["date", "ticker", "ret_ex", "ret_raw"], value_vars=keys,
                  var_name="model", value_name="score")
    return long, reb


def run(ds, prices=None, out=print, details=False, progress=print, seal=True):
    vault_from = vault_window(ds)
    h = config_hash()
    prev = None
    if os.path.exists(seal_path()):
        try:
            with open(seal_path(), "r", encoding="utf-8") as f:
                prev = json.load(f)
        except (OSError, json.JSONDecodeError):
            prev = None

    out(f"Otwieram sejf: dane od {vault_from.date()} do {ds['date'].max().date()} (nieużywane w żadnym teście).")
    preds, reb = predict_vault(ds, progress=progress)
    bench = S.benchmark_periods(ds, reb, H)
    hold = S.metrics_periodic(bench, S.CAPITAL, H)
    res = {}
    for k in list(FROZEN) + ["losowo"]:
        eq, st = S.simulate_portfolio(preds, k, H, k=S.K, slippage_bp=S.SLIPPAGE_BP, prices=prices)
        m = S.metrics_periodic(eq, S.CAPITAL, H, st, bench=bench)
        r = S.period_returns(eq, S.CAPITAL)
        br = S.period_returns(bench, S.CAPITAL).reindex(r.index)
        m["miesiace_lepsze"] = int((r > br).sum())
        m["miesiace"] = int(len(r))
        res[k] = (m, eq)

    hold_total = float(bench.iloc[-1] / S.CAPITAL - 1)
    out("")
    out("=" * 78)
    out("SEJF - WERDYKT (w prostych słowach)")
    out("=" * 78)
    out(f"Sprawdzane raz, na ostatnim roku, którego nikt wcześniej nie oglądał: {len(reb)} miesięcznych zmian składu")
    out(f"({reb.min().date()} – {reb.max().date()}). Zasady jak wcześniej: 10 spółek, opłaty XTB, poślizg 0.10%.")
    out(f"Zwykłe trzymanie wszystkich spółek w tym czasie: {hold_total:+.1%}.")
    rand_total = float(res["losowo"][0]["koncowy"] / S.CAPITAL - 1)
    out(f"Losowe 10 spółek (dla porównania, to pojedynczy rzut kostką): {rand_total:+.1%}.")
    out("")
    passed = []
    for k in FROZEN:
        m, _ = res[k]
        total = m["koncowy"] / S.CAPITAL - 1
        ok = total > hold_total            # kryterium: lepsza od zwykłego trzymania
        if ok:
            passed.append(k)
        verdict = ("ZDAŁA" if ok else "NIE ZDAŁA")
        out(f"{k}  {FROZEN[k]['opis']:<38} {total:>+7.1%}  -> {verdict}")
        out(f"    z 10 000 zł: {B.zl(10000 * (1 + total))} zł (trzymanie: {B.zl(10000 * (1 + hold_total))} zł); "
            f"lepsza od trzymania w {m['miesiace_lepsze']} z {m['miesiace']} miesięcy; "
            f"najgorszy spadek {m['obsuniecie']:.0%}")
    out("")
    out("Jak to rozumieć: rok to tylko kilkanaście miesięcznych zmian składu, więc nawet 'ZDAŁA' nie jest")
    out("dowodem - to brak powodu, żeby odrzucić strategię. 'NIE ZDAŁA' to poważny sygnał, że wcześniejszy")
    out("wynik był w dużej części szczęściem.")
    if passed:
        out(f"WNIOSEK: dalej idą tylko te, które zdały ({', '.join(passed)}) - na grę na papierze, nadal BEZ prawdziwych pieniędzy.")
    else:
        out("WNIOSEK: żadna strategia nie zdała. Zwykłe trzymanie akcji (np. ETF na IKE) pozostaje lepszym wyborem.")

    if prev and prev.get("config_hash") != h:
        out("")
        out("!!! UWAGA: sejf był już otwarty " + str(prev.get("opened_at", "?"))[:16] +
            " dla INNEJ konfiguracji strategii. Ten wynik NIE jest już uczciwym testem -")
        out("!!! strategie zmieniono po zobaczeniu danych z sejfu. Pierwszy, zapieczętowany wynik jest w data/lab/vault.json.")
    elif prev:
        out(f"(Sejf otwarto po raz pierwszy {str(prev.get('opened_at'))[:16]} - ta sama konfiguracja, ten sam wynik.)")

    if details:
        out("\nMiesiąc po miesiącu (zwrot w okresie do następnej zmiany składu):")
        out("  data zmiany    " + "  ".join(f"{k:>7}" for k in FROZEN) + "   trzymanie")
        br = S.period_returns(bench, S.CAPITAL)
        rets = {k: S.period_returns(res[k][1], S.CAPITAL) for k in FROZEN}
        for d in br.index:
            out(f"  {d.date()}    " + "  ".join(f"{rets[k].get(d, np.nan):>+7.1%}" for k in FROZEN) + f"   {br[d]:>+8.1%}")
        last = preds["date"].max()
        out(f"\nSkład portfela przy ostatniej zmianie ({last.date()}):")
        for k in FROZEN:
            top = preds[(preds["model"] == k) & (preds["date"] == last)].sort_values("score", ascending=False).head(S.K)
            out(f"  {k}: " + ", ".join(top["ticker"]))

    if seal and (prev is None or prev.get("config_hash") != h):
        record = {"opened_at": datetime.now().isoformat(timespec="seconds"), "config_hash": h,
                  "frozen": {k: v["opis"] for k, v in FROZEN.items()}, "settings": SETTINGS,
                  "period": [str(reb.min().date()), str(reb.max().date())], "hold_total": hold_total,
                  "results": {k: {"total": res[k][0]["koncowy"] / S.CAPITAL - 1,
                                  "months_better": res[k][0]["miesiace_lepsze"], "months": res[k][0]["miesiace"],
                                  "max_drawdown": res[k][0]["obsuniecie"]} for k in list(FROZEN) + ["losowo"]}}
        if prev is not None:
            record["UWAGA"] = "sejf otwarty ponownie po zmianie konfiguracji - wynik nie jest uczciwym testem"
            record["pierwsze_otwarcie"] = prev
        os.makedirs(os.path.dirname(seal_path()), exist_ok=True)
        with open(seal_path(), "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=1)
    return res, hold_total


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m lab.vault", description="QuantPulse - jednorazowe otwarcie sejfu")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="sprawdź zamrożone strategie na ostatnim roku")
    r.add_argument("--szczegoly", action="store_true")
    args = p.parse_args(argv)
    ds = F.load_dataset()
    if ds is None or "y_h20" not in ds.columns or "seq_m1" not in ds.columns:
        print("Tabela cech jest ze starszej wersji albo jej brak - najpierw: python -m lab.features build")
        return 1
    from .data_store import MarketStore
    t0 = time.time()
    prices = S.exec_close_prices(MarketStore(), sorted(ds.loc[ds["market"] == "PL", "ticker"].unique()))
    run(ds, prices=prices, details=args.szczegoly)
    print(f"\nGotowe w {time.time() - t0:.0f} s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
