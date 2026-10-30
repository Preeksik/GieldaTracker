"""
QuantPulse - cechy i etykiety (etap 3).

Z notowań w backend/data/market buduje jedną tabelę: wiersz = spółka × dzień sesji t.
  - cechy: tylko z danych znanych PO ZAMKNIĘCIU sesji t (decyzja zapada wieczorem),
  - etykiety: co spółka zrobiła NASTĘPNEGO dnia (t+1), od otwarcia do zamknięcia.

    cd backend
    python -m lab.features build        # buduje backend/data/lab/dataset.parquet i pokazuje raport
    python -m lab.features show PKO.WA  # ostatnie dni jednej spółki

ETYKIETA (to, co model ma przewidzieć):
    y_oc     = ln(C[t+1] / O[t+1])                 zwrot spółki jutro, otwarcie -> zamknięcie
    y_oc_ex  = y_oc - średnia y_oc wszystkich spółek tego rynku tego dnia   <- GŁÓWNA
    y_up     = 1 jeśli y_oc_ex > 0, inaczej 0
    y_cc_ex  = to samo, ale od zamknięcia dziś do zamknięcia jutro (z nocą) - do porównania
    y_oc_ex_idx = y_oc minus zwrot indeksu (WIG20 / S&P 500) od otwarcia do zamknięcia

    Dlaczego "minus średnia spółek", a nie "minus indeks"? Yahoo dla indeksów często podaje
    otwarcie równe wczorajszemu zamknięciu, więc zwrot indeksu "od otwarcia" bywa fałszywy.
    Średnia z naszych spółek tego dnia jest policzona z tych samych danych co etykieta.
    Wariant z indeksem zostaje w tabeli, a raport pokazuje, jak często otwarcie indeksu jest podejrzane.

CZAS (żeby nic nie podglądało przyszłości):
    GPW zamyka się 17:05, USA o 22:00 naszego czasu. Dla spółek z GPW decyzja zapada przed
    końcem sesji w USA, więc VIX z dnia t jest jeszcze nieznany - bierzemy VIX z dnia t-1.
    Test `test_no_lookahead` sprawdza to automatycznie: cechy z dnia t muszą wyjść identyczne,
    gdy z danych usunie się wszystko po dniu t.
"""

import os
import sys
import argparse

import numpy as np
import pandas as pd

from . import engine
from . import universe as U
from .data_store import MarketStore, BAD, Q_ZERO_VOL, HAS_PARQUET

EPS = 1e-8
VAULT_DAYS = 365          # ostatni rok - "sejf", nie oglądamy go aż do końcowego testu
CAL_MIN_SHARE = 0.3       # dzień jest sesją rynku, jeśli notowała go co najmniej 30% spółek
IDX_MIN_COVER = 0.8       # indeks z Yahoo musi mieć co najmniej 80% sesji, inaczej liczymy własny

FEATURES = [
    "ret_1", "ret_5", "ret_20", "vol_20", "z_price_20", "z_volume_20", "mom_20",
    "gap", "range", "close_loc", "dist_high_250", "rsi_14", "rel_str_20",
    "idx_ret_1", "idx_ret_5", "vix_chg_1", "dow",
]
FEATURE_INFO = {
    "ret_1": "zwrot dzienny (log)", "ret_5": "zwrot 5 dni", "ret_20": "zwrot 20 dni",
    "vol_20": "zmienność 20 dni", "z_price_20": "z-score ceny 20 dni",
    "z_volume_20": "z-score wolumenu 20 dni", "mom_20": "przewaga dni wzrostowych (-1..1)",
    "gap": "luka na otwarciu", "range": "zakres dnia / cena", "close_loc": "miejsce zamknięcia w zakresie (0..1)",
    "dist_high_250": "odległość od rocznego szczytu", "rsi_14": "RSI 14",
    "rel_str_20": "siła względna 20 dni", "idx_ret_1": "zwrot indeksu 1 dzień",
    "idx_ret_5": "zwrot indeksu 5 dni", "vix_chg_1": "zmiana VIX", "dow": "dzień tygodnia (0=pon)",
}
LABELS = ["y_oc", "y_oc_ex", "y_up", "y_cc_ex", "y_oc_ex_idx"]


def lab_dir():
    try:
        from paths import data_path
        return data_path("lab")
    except ImportError:
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "lab")


# ============================================================================
# CECHY JEDNEJ SPÓŁKI
# ============================================================================

def rsi(prices, n=14):
    """RSI Wildera (0..100). Same wzrosty -> 100, same spadki -> 0."""
    d = prices.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + gain / loss.where(loss > 0))
    out[(loss == 0) & (gain > 0)] = 100.0
    out[(loss == 0) & (gain == 0)] = 50.0
    return out


def market_context(index_df, vix_df, market):
    """Cechy rynku na dzień (indeks: zwroty; VIX: zmiana, dla GPW przesunięta o dzień)."""
    ctx = pd.DataFrame(index=index_df.index)
    # Indeksy nie wypłacają dywidend, więc zwykłe zamknięcie wystarcza - a Yahoo dla części
    # indeksów (np. WIG20) zostawia kolumnę "skorygowane zamknięcie" pustą.
    ac = index_df["close"]
    ctx["idx_ret_1"] = np.log(ac / ac.shift(1))
    ctx["idx_ret_5"] = np.log(ac / ac.shift(5))
    ctx["idx_ret_20"] = np.log(ac / ac.shift(20))
    # zwrot indeksu jutro od otwarcia do zamknięcia (do wariantu etykiety) + test wiarygodności otwarcia
    oc = np.log(index_df["close"] / index_df["open"])
    ctx["idx_oc_next"] = oc.shift(-1)
    suspect = (index_df["open"] - index_df["close"].shift(1)).abs() < 1e-9 * index_df["close"]
    ctx["idx_open_suspect"] = suspect.astype(float).shift(-1)      # dotyczy jutrzejszego otwarcia

    vix = None
    if vix_df is not None and len(vix_df):
        v = np.log(vix_df["close"] / vix_df["close"].shift(1))
        if market == "PL":
            # VIX z dnia d znamy dopiero o 22:00 dnia d -> dla GPW użyteczny od dnia d+1
            v.index = v.index + pd.Timedelta(days=1)
        vix = v
    return ctx, vix


def stock_features(df, ctx, vix):
    """Cechy + pomocnicze kolumny dla jednej spółki. df: notowania z data_store (indeks = data)."""
    df = df.copy()
    # Dni z błędem w danych (szpilka, skok skali, brak ceny) wyłączamy z obliczeń - inaczej np. cena ×2
    # z jednego dnia psułaby "odległość od rocznego szczytu" przez cały rok.
    bad_day = ((df["quality"].fillna(0).astype(int) & BAD) != 0)
    df.loc[bad_day, ["open", "high", "low", "close", "adj_close", "volume"]] = np.nan
    ac, c, o, h, l, v = (df[k] for k in ("adj_close", "close", "open", "high", "low", "volume"))
    f = pd.DataFrame(index=df.index)
    lr = np.log(ac / ac.shift(1))
    f["ret_1"] = lr
    f["ret_5"] = np.log(ac / ac.shift(5))
    f["ret_20"] = np.log(ac / ac.shift(20))
    f["vol_20"] = engine.rolling_std(lr.to_numpy(), 20)
    f["z_price_20"] = engine.rolling_zscore(ac.to_numpy(), 20)
    f["z_volume_20"] = engine.rolling_zscore(np.log1p(v.fillna(0)).to_numpy(), 20)
    f["mom_20"] = engine.rolling_mean(np.sign(lr).to_numpy(), 20)
    # Otwarcie z Yahoo nie jest skorygowane o dywidendy - przeliczamy je tym samym współczynnikiem
    # co zamknięcie, inaczej w dniu odcięcia dywidendy wychodziłaby sztuczna luka.
    f["gap"] = np.log(o * (ac / c) / ac.shift(1))
    rng = h - l
    f["range"] = rng / c
    f["close_loc"] = ((c - l) / rng.where(rng > 0)).fillna(0.5).clip(0, 1)
    # min_periods 240: kilka wyłączonych (błędnych) dni nie blokuje cechy na cały rok;
    # pierwsze 249 dni historii i tak bez wyniku - za krótko na roczny szczyt.
    high_250 = ac.rolling(250, min_periods=240).max()
    high_250.iloc[:249] = np.nan
    f["dist_high_250"] = ac / high_250 - 1
    f["rsi_14"] = rsi(ac, 14)

    cx = ctx.reindex(df.index)
    f["rel_str_20"] = f["ret_20"] - cx["idx_ret_20"]
    f["idx_ret_1"] = cx["idx_ret_1"]
    f["idx_ret_5"] = cx["idx_ret_5"]
    if vix is not None:
        # ostatnia znana zmiana VIX na dzień t (asof), najwyżej 5 dni wstecz
        f["vix_chg_1"] = vix.reindex(vix.index.union(df.index)).ffill(limit=5).reindex(df.index)
    else:
        f["vix_chg_1"] = np.nan
    f["dow"] = df.index.dayofweek.astype(float)

    # pomocnicze: płynność, jakość, surowe etykiety
    f["turnover_20"] = (c * v).rolling(20, min_periods=20).median()
    bad = bad_day.astype(float)
    f["bad_recent"] = bad.rolling(21, min_periods=1).max() > 0       # zły dzień w oknie cech
    nxt = df.shift(-1)
    f["next_date"] = pd.Series(df.index, index=df.index).shift(-1)
    nq = nxt["quality"].fillna(0).astype(int)
    f["next_bad"] = (nq & (BAD | Q_ZERO_VOL)) != 0
    f["next_zero_vol"] = (nq & Q_ZERO_VOL) != 0
    f["y_oc"] = np.log(nxt["close"] / nxt["open"])
    f["y_cc"] = np.log(nxt["adj_close"] / ac)
    f["idx_oc_next"] = cx["idx_oc_next"]
    f["idx_open_suspect"] = cx["idx_open_suspect"]
    # jutrzejsze otwarcie = dzisiejsze zamknięcie co do grosza - czasem prawda, ale gdy to częste, Yahoo nie ma otwarć
    f["open_suspect"] = ((nxt["open"] - c).abs() < 1e-9 * c).astype(float)
    return f


# ============================================================================
# CAŁA TABELA
# ============================================================================

def market_calendar(frames):
    """
    Dni sesji rynku: handlowano (wolumen > 0) co najmniej CAL_MIN_SHARE spółek.
    Yahoo wstawia czasem wiersze ze świąt (np. GPW 2019-2020: 1 maja, Boże Ciało, Wigilia) -
    cena przepisana z poprzedniego dnia i zerowy wolumen. To nie były sesje.
    """
    traded = [f.index[f["volume"].fillna(0) > 0] for f in frames]
    counts = pd.concat([pd.Series(1, index=ix) for ix in traded]).groupby(level=0).sum()
    return counts.index[counts >= max(1, CAL_MIN_SHARE * len(frames))].sort_values()


def synthetic_index(frames):
    """
    Własny indeks rynku: średnia (równe wagi) dziennych zmian wszystkich spółek - gdy Yahoo nie ma
    historii prawdziwego indeksu (WIG20.WA zwraca tylko ostatnią sesję). Dni z błędami pomijamy.
    Zwraca ramkę w formacie data_store (open, high, low, close, adj_close, volume, quality).
    """
    lr, gap = [], []
    for df in frames:
        d = df.copy()
        bad = (d["quality"].fillna(0).astype(int) & BAD) != 0
        d.loc[bad, ["open", "close", "adj_close"]] = np.nan
        k = d["adj_close"] / d["close"]
        prev = d["adj_close"].shift(1)
        lr.append(np.log(d["adj_close"] / prev))
        gap.append(np.log(d["open"] * k / prev))
    r = pd.concat(lr, axis=1).mean(axis=1, skipna=True).fillna(0.0)
    g = pd.concat(gap, axis=1).mean(axis=1, skipna=True).fillna(0.0)
    close = 1000.0 * np.exp(r.cumsum())
    open_ = close.shift(1) * np.exp(g)
    open_.iloc[0] = close.iloc[0]
    out = pd.DataFrame({"open": open_, "high": np.maximum(open_, close), "low": np.minimum(open_, close),
                        "close": close, "adj_close": close, "volume": np.nan, "quality": 0})
    out.index.name = "date"
    return out


def build_dataset(store, progress=print):
    stocks = sorted(n for n, m in store.meta.items() if m.get("kind") == "stock" and m.get("status") == "ok")
    vix_df = store.load("VIX")
    parts = []
    for market in ("PL", "US"):
        names = [n for n in stocks if U.market_of(n) == market]
        idx_df = store.load(U.BENCHMARK[market])
        if not names:
            progress(f"  {market}: brak spółek - pomijam")
            continue
        raw = {n: store.load(n) for n in names}
        raw = {n: d for n, d in raw.items() if d is not None and len(d) > 60}
        cal = market_calendar(list(raw.values()))
        # wiersze spoza kalendarza (święta wstawione przez Yahoo) wyrzucamy - ze spółek i z indeksu
        raw = {n: d[d.index.isin(cal)] for n, d in raw.items()}
        cover = 0.0 if idx_df is None else idx_df.index.isin(cal).sum() / max(1, len(cal))
        if cover >= IDX_MIN_COVER:
            idx_df = idx_df[idx_df.index.isin(cal)]
            source = f"indeks {U.BENCHMARK[market]}"
        else:
            # Yahoo nie ma historii indeksu - liczymy własny ze spółek tego rynku
            idx_df = synthetic_index(list(raw.values())).reindex(cal).ffill()
            source = (f"średnia {len(raw)} spółek (indeks {U.BENCHMARK[market]} z Yahoo ma tylko "
                      f"{cover:.0%} sesji)")
        ctx, vix = market_context(idx_df, vix_df, market)
        next_session = pd.Series(cal[1:], index=cal[:-1])
        for n, df in raw.items():
            f = stock_features(df, ctx, vix)
            # następny wiersz musi być NASTĘPNĄ sesją rynku (nie po kilkudniowym zawieszeniu)
            expected = next_session.reindex(f.index)
            f["next_is_session"] = f["next_date"].notna() & (f["next_date"] == expected)
            f.insert(0, "ticker", n)
            f.insert(1, "market", market)
            parts.append(f)
        progress(f"  {market}: {len(raw)} spółek, rynek = {source}")
    if not parts:
        raise RuntimeError("Brak danych - najpierw: python -m lab.data_store update")

    data = pd.concat(parts)
    data.index.name = "date"
    data = data.reset_index()

    y_missing = data["y_oc"].isna()
    label_ok = data["next_is_session"] & ~data["next_bad"] & ~y_missing
    feat_ok = data[FEATURES].notna().all(axis=1) & ~data["bad_recent"]
    data.loc[~label_ok, ["y_oc", "y_cc"]] = np.nan

    # nadwyżka nad średnią rynku tego dnia (tylko z wierszy z poprawną etykietą)
    grp = data.groupby(["market", "date"])
    data["y_oc_ex"] = data["y_oc"] - grp["y_oc"].transform("mean")
    data["y_cc_ex"] = data["y_cc"] - grp["y_cc"].transform("mean")
    data["y_oc_ex_idx"] = data["y_oc"] - data["idx_oc_next"]
    data["y_up"] = np.where(data["y_oc_ex"].notna(), (data["y_oc_ex"] > 0).astype(float), np.nan)

    data["train_ok"] = feat_ok & label_ok
    diagnose(data, feat_ok, label_ok, y_missing, progress)
    # wiersze do jutrzejszej prognozy: ostatni dzień, cechy są, etykiety jeszcze nie ma
    last = data.groupby("market")["date"].transform("max")
    data["predict_row"] = feat_ok & (data["date"] == last)

    keep = data["train_ok"] | data["predict_row"]
    cols = ["date", "ticker", "market"] + FEATURES + LABELS + \
        ["turnover_20", "idx_open_suspect", "open_suspect", "train_ok", "predict_row"]
    out = data.loc[keep, cols].sort_values(["date", "market", "ticker"]).reset_index(drop=True)
    return out


def diagnose(data, feat_ok, label_ok, y_missing, out=print):
    """Ile wierszy odpada i dlaczego - osobno dla każdego rynku. Szczegóły, gdy odpada ponad połowa."""
    pct = lambda x: f"{100 * float(x):.0f}%"
    for mk in sorted(data["market"].unique()):
        m = data["market"] == mk
        d, fo, lo, ym = data[m], feat_ok[m], label_ok[m], y_missing[m]
        ok = (fo & lo).mean()
        out(f"  {mk}: wierszy {len(d):,}".replace(",", " ") +
            f", z kompletem cech {pct(fo.mean())}, z etykietą {pct(lo.mean())}, do nauki {pct(ok)}")
        if ok >= 0.5:
            continue
        out(f"    ⚠ {mk}: do nauki nadaje się mniej niż połowa wierszy. Powody:")
        nan = d[FEATURES].isna().mean().sort_values(ascending=False)
        for f, share in nan[nan > 0.2].items():
            out(f"      brak cechy '{FEATURE_INFO[f]}': {pct(share)} wierszy")
        out(f"      błędny dzień w ostatnich 21 sesjach: {pct(d['bad_recent'].mean())}")
        out(f"      następny wiersz to nie następna sesja rynku: {pct((~d['next_is_session']).mean())}")
        out(f"      jutro zerowy wolumen: {pct(d['next_zero_vol'].mean())}, "
            f"jutro błędny dzień: {pct((d['next_bad'] & ~d['next_zero_vol']).mean())}")
        out(f"      brak jutrzejszego zwrotu (otwarcie/zamknięcie): {pct(ym.mean())}")


def save_dataset(df, root=None):
    root = root or lab_dir()
    os.makedirs(root, exist_ok=True)
    if HAS_PARQUET:
        path = os.path.join(root, "dataset.parquet")
        df.to_parquet(path + ".tmp", index=False)
    else:
        path = os.path.join(root, "dataset.csv.gz")
        df.to_csv(path + ".tmp", index=False, compression="gzip")
    MarketStore._replace(path + ".tmp", path)
    return path


def load_dataset(root=None):
    root = root or lab_dir()
    pq, csv = os.path.join(root, "dataset.parquet"), os.path.join(root, "dataset.csv.gz")
    if os.path.exists(pq) and HAS_PARQUET:
        return pd.read_parquet(pq)
    if os.path.exists(csv):
        return pd.read_csv(csv, parse_dates=["date"])
    return None


# ============================================================================
# RAPORT
# ============================================================================

MARKET_WIDE = {"idx_ret_1", "idx_ret_5", "vix_chg_1", "dow"}   # te same dla wszystkich spółek danego dnia


def daily_ic(df, feature, label="y_oc_ex", min_stocks=15):
    """Średnia dzienna korelacja rangowa cechy z etykietą (IC), jej t-stat i liczba dni."""
    d = df[["market", "date", feature, label]].dropna()
    keys = [d["market"], d["date"]]
    d = d[d.groupby(keys)[label].transform("size") >= min_stocks]
    if d.empty:
        return np.nan, np.nan, 0
    keys = [d["market"], d["date"]]
    a = d.groupby(keys)[feature].rank()
    b = d.groupby(keys)[label].rank()
    a = a - a.groupby(keys).transform("mean")
    b = b - b.groupby(keys).transform("mean")
    num = (a * b).groupby(keys).sum()
    den = np.sqrt((a * a).groupby(keys).sum() * (b * b).groupby(keys).sum())
    ics = (num / den.where(den > 0)).dropna()
    if len(ics) < 2:
        return np.nan, np.nan, len(ics)
    return ics.mean(), ics.mean() / (ics.std() + EPS) * np.sqrt(len(ics)), len(ics)


def report(ds, out=print):
    tr = ds[ds["train_ok"]]
    vault_from = ds["date"].max() - pd.Timedelta(days=VAULT_DAYS)
    research = tr[tr["date"] < vault_from]
    num = lambda n: f"{n:,}".replace(",", " ")
    out(f"Próbek do nauki: {num(len(tr))}  ({tr['date'].min().date()} – {tr['date'].max().date()})")
    out(f"  z tego do badań: {num(len(research))}, w sejfie (od {vault_from.date()}, nie oglądamy): "
        f"{num(len(tr) - len(research))}")
    for mk, g in research.groupby("market"):
        out(f"  {mk}: {g['ticker'].nunique()} spółek, {g['date'].nunique()} sesji, "
            f"średni ruch jutro od otwarcia do zamknięcia {g['y_oc'].mean() * 1e4:+.1f} pb, "
            f"typowy rozrzut {g['y_oc_ex'].std() * 100:.2f}%")
        out(f"      jutrzejsze otwarcie = dzisiejsze zamknięcie: spółki {g['open_suspect'].mean():.0%} dni, "
            f"indeks {g['idx_open_suspect'].mean():.0%} dni (kilka % to norma, dużo więcej = Yahoo nie ma prawdziwych otwarć)")
    pred = ds[ds["predict_row"]]
    if len(pred):
        out(f"Gotowe do prognozy na następną sesję: " +
            ", ".join(f"{mk} {g['date'].max().date()} ({len(g)} spółek)" for mk, g in pred.groupby("market")))

    out("\nPierwszy rzut oka (tylko dane sprzed sejfu): IC = średnia dzienna korelacja rangowa cechy")
    out("z jutrzejszym wynikiem ponad rynek. |IC| 0.01–0.03 to w finansach już coś; |t| < 2 to szum.")
    rows = []
    for f in FEATURES:
        if f in MARKET_WIDE or research[f].nunique() < 3:
            continue
        for mk in sorted(research["market"].unique()):
            ic, t, n = daily_ic(research[research["market"] == mk], f)
            rows.append((f, mk, ic, t))
    tab = pd.DataFrame(rows, columns=["cecha", "rynek", "IC", "t"])
    if len(tab):
        piv = tab.pivot(index="cecha", columns="rynek", values=["IC", "t"])
        for f in [x for x in FEATURES if x in piv.index]:
            cells = []
            for mk in sorted(tab["rynek"].unique()):
                ic, t = piv.loc[f, ("IC", mk)], piv.loc[f, ("t", mk)]
                mark = " *" if abs(t) >= 3 else ""
                cells.append(f"{mk} {ic:+.3f} (t {t:+.1f}){mark}")
            out(f"  {FEATURE_INFO[f]:<36} " + "   ".join(cells))
        n_f, n_m = tab["cecha"].nunique(), tab["rynek"].nunique()
        out(f"  * |t| ≥ 3. Uwaga: {n_f} cech × {n_m} rynki = {n_f * n_m} prób - pojedyncza 'gwiazdka' może wyjść przypadkiem.")
        out("  Cechy całego rynku (" + ", ".join(FEATURE_INFO[f] for f in FEATURES if f in MARKET_WIDE) +
            ") są dla wszystkich spółek danego dnia takie same - nie da się ich tak ocenić; "
            "model może z nich korzystać w połączeniu z innymi cechami.")
    return tab


# ============================================================================
# WIERSZ POLECEŃ
# ============================================================================

def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m lab.features", description="QuantPulse - cechy i etykiety")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="zbuduj tabelę cech i pokaż raport")
    sub.add_parser("report", help="raport z zapisanej tabeli")
    sh = sub.add_parser("show", help="ostatnie dni jednej spółki")
    sh.add_argument("ticker")
    sh.add_argument("-n", type=int, default=5)
    args = p.parse_args(argv)
    print(engine.info())

    if args.cmd == "build":
        store = MarketStore()
        print("Liczę cechy…")
        ds = build_dataset(store)
        path = save_dataset(ds)
        print(f"Zapisano: {path}\n")
        report(ds)
        return 0

    ds = load_dataset()
    if ds is None:
        print("Brak tabeli - najpierw: python -m lab.features build")
        return 1
    if args.cmd == "report":
        report(ds)
    else:
        d = ds[ds["ticker"].str.upper() == args.ticker.upper()].tail(args.n)
        if d.empty:
            print(f"Brak {args.ticker} w tabeli.")
            return 1
        pd.set_option("display.width", 160)
        print(d.set_index("date")[FEATURES + ["y_oc_ex"]].T.round(4).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
