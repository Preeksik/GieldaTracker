"""
QuantPulse - magazyn notowań dziennych (etap 1).

Jeden plik na instrument w backend/data/market/ (Parquet, a bez pyarrow - CSV.gz) + plik
_meta.json z tym, co, kiedy i skąd pobrano. Działa samodzielnie, bez uruchamiania aplikacji:

    cd backend
    py -m lab.data_store update          # pierwsze pobranie: 10 lat; potem tylko nowe dni
    py -m lab.data_store update --full   # wszystko od nowa
    py -m lab.data_store update --only PKO.WA,AAPL
    py -m lab.data_store status          # raport: co jest, czego brakuje, podejrzane dni

ZASADY:
  - Kolumna `close` to cena zamknięcia skorygowana tylko o splity (tak podaje Yahoo), a
    `adj_close` dodatkowo o dywidendy. Do stóp zwrotu służy adj_close, do kosztów i wielkości
    pozycji - close.
  - Aktualizacja pobiera ostatnie dni z zakładką. Jeśli na zakładce zmieniły się stare ceny
    (split albo dywidenda przeliczyły historię), instrument jest pobierany od nowa w całości -
    inaczej stara i nowa część historii nie pasowałyby do siebie.
  - Podejrzane dni są oznaczane w kolumnie `quality` (flagi bitowe), a nie usuwane. Model
    nie uczy się na dniach z flagami z grupy BAD.
"""

import os
import sys
import json
import time
import logging
import argparse
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

try:
    from . import universe as U
except ImportError:  # uruchomienie jako zwykły skrypt
    import universe as U

logger = logging.getLogger("quantpulse")

try:
    import pyarrow  # noqa: F401
    HAS_PARQUET = True
except Exception:   # brak pakietu albo brak wersji na tę wersję Pythona
    HAS_PARQUET = False

PRICE_COLS = ["open", "high", "low", "close", "adj_close", "volume"]
YEARS = 10
OVERLAP_DAYS = 15          # zakładka przy aktualizacji (dni kalendarzowe)
CHUNK = 20                 # ile symboli w jednym zapytaniu do Yahoo
PAUSE = 1.0                # przerwa między zapytaniami, sekundy
MIN_INDEX_ROWS = 500       # indeks/waluta z krótszą historią = Yahoo podało tylko ostatnie dni (np. WIG20.WA)

# Flagi jakości (bitowe). BAD = dzień, na którym model się nie uczy.
Q_MISSING = 1       # brak którejś z cen
Q_NONPOS = 2        # cena <= 0
Q_RANGE = 4         # niespójne: maks. < min. albo zamknięcie poza zakresem dnia
Q_ZERO_VOL = 8      # zerowy wolumen (informacyjnie - zdarza się przy małych spółkach)
Q_SPIKE = 16        # skok > 40%, który następnego dnia w dużej części wraca (błędny wydruk)
Q_SCALE = 32        # skok ceny ×50 albo /50 (zmiana jednostki, np. pensy -> funty)
BAD = Q_MISSING | Q_NONPOS | Q_RANGE | Q_SPIKE | Q_SCALE

# Kiedy sesja jest już zamknięta (czas lokalny giełdy, z zapasem na fixing i opóźnienie Yahoo).
# Wcześniej dzisiejszy wiersz to notowanie w trakcie dnia - nie zapisujemy go.
SESSION_CLOSE = {"PL": ("Europe/Warsaw", 17, 30), "US": ("America/New_York", 16, 30),
                 "FX": ("UTC", 23, 59)}
INDEX_MARKET = {"WIG20": "PL", "WIG": "PL"}

FLAG_NAMES = {Q_MISSING: "brak ceny", Q_NONPOS: "cena <= 0", Q_RANGE: "niespójny zakres dnia",
              Q_ZERO_VOL: "zerowy wolumen", Q_SPIKE: "szpilka", Q_SCALE: "skok skali"}


def default_root():
    try:
        from paths import data_path
    except ImportError:
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "market")
    return data_path("market")


def items_from_universe(only=None):
    """Lista (nazwa, rodzaj, [kandydaci na symbol Yahoo])."""
    items = [(t, "stock", [t]) for t in U.all_stocks()]
    items += [(k, "index", v) for k, v in U.INDEXES.items()]
    items += [(k, "fx", v) for k, v in U.FX.items()]
    if only:
        wanted = {s.strip().upper() for s in only}
        items = [i for i in items if i[0].upper() in wanted]
    return items


# ============================================================================
# JAKOŚĆ DANYCH
# ============================================================================

def quality_flags(df, kind="stock"):
    """Flagi jakości dla każdego dnia. df: kolumny open..volume, indeks = daty rosnąco."""
    n = len(df)
    q = np.zeros(n, dtype=np.int64)
    if n == 0:
        return q
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    v = df["volume"].to_numpy(dtype=float)

    missing = np.isnan(o) | np.isnan(h) | np.isnan(l) | np.isnan(c)
    q[missing] |= Q_MISSING
    with np.errstate(invalid="ignore"):
        q[(o <= 0) | (h <= 0) | (l <= 0) | (c <= 0)] |= Q_NONPOS
        tol = 0.005
        bad_range = (h < l * (1 - 1e-9)) | (c > h * (1 + tol)) | (c < l * (1 - tol)) \
            | (o > h * (1 + tol)) | (o < l * (1 - tol))
        q[bad_range & ~missing] |= Q_RANGE
        if kind == "stock":
            q[(v == 0)] |= Q_ZERO_VOL

        prev = np.roll(c, 1)
        prev[0] = np.nan
        ratio = c / prev
        q[(ratio > 50) | (ratio < 1 / 50)] |= Q_SCALE

        r = ratio - 1
        r_next = np.roll(r, -1)
        r_next[-1] = np.nan
        spike = (np.abs(r) > 0.40) & (r * r_next < 0) & (np.abs(r_next) > 0.30)
        q[spike] |= Q_SPIKE
    return q


def describe_flags(value):
    return [name for bit, name in FLAG_NAMES.items() if value & bit]


# ============================================================================
# POBIERANIE Z YAHOO
# ============================================================================

def yahoo_download(symbols, start):
    """Domyślne pobieranie (yfinance). Zwraca surową ramkę z yf.download."""
    import yfinance as yf
    kwargs = dict(start=start, interval="1d", auto_adjust=False, actions=False,
                  group_by="ticker", progress=False, threads=True)
    try:
        return yf.download(symbols, multi_level_index=True, **kwargs)
    except TypeError:   # starsze yfinance nie znają multi_level_index
        return yf.download(symbols, **kwargs)


FIELD_MAP = {"Open": "open", "High": "high", "Low": "low", "Close": "close",
             "Adj Close": "adj_close", "Volume": "volume"}


def extract(raw, symbol):
    """Wyciąga jeden symbol z ramki yf.download - obsługuje oba układy kolumn i pojedynczy symbol."""
    if raw is None or getattr(raw, "empty", True):
        return None
    cols = raw.columns
    part = None
    if isinstance(cols, pd.MultiIndex):
        lvl0, lvl1 = set(cols.get_level_values(0)), set(cols.get_level_values(1))
        if symbol in lvl0:
            part = raw[symbol]
        elif symbol in lvl1:
            part = raw.xs(symbol, axis=1, level=1)
        elif len(lvl0) == 1 and set(FIELD_MAP) & lvl1:
            part = raw.droplevel(0, axis=1)
        elif len(lvl1) == 1 and set(FIELD_MAP) & lvl0:
            part = raw.droplevel(1, axis=1)
    elif set(FIELD_MAP) & set(cols):
        part = raw
    if part is None:
        return None
    part = part.rename(columns=FIELD_MAP)
    if "close" not in part.columns:
        return None
    if "adj_close" not in part.columns or part["adj_close"].isna().all():
        # brak albo pusta kolumna (Yahoo tak robi dla części indeksów) - bez dywidend adj = close
        part = part.assign(adj_close=part["close"])
    if "volume" not in part.columns:
        part = part.assign(volume=np.nan)
    out = part[PRICE_COLS].astype(float)
    idx = pd.to_datetime(out.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    out.index = idx.normalize()
    out.index.name = "date"
    out = out[~out[["open", "high", "low", "close"]].isna().all(axis=1)]
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out if len(out) else None


# ============================================================================
# MAGAZYN
# ============================================================================

def _safe_name(name):
    return "".join(ch if ch.isalnum() or ch in "-." else "_" for ch in name)


class MarketStore:
    def __init__(self, root=None, download_fn=None, sleep=time.sleep, today=None, parquet=None, now=None):
        self.root = root or default_root()
        self.download_fn = download_fn or yahoo_download
        self.sleep = sleep
        self.today = pd.Timestamp(today or datetime.now()).date()
        # "Teraz" w UTC - do odcinania niedokończonej sesji. W testach można podać na sztywno.
        t = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
        self.now = t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")
        self.parquet = HAS_PARQUET if parquet is None else parquet
        os.makedirs(self.root, exist_ok=True)
        self.meta_path = os.path.join(self.root, "_meta.json")
        self.meta = self._load_meta()

    # ---------- pliki ----------
    def _paths(self, name):
        base = os.path.join(self.root, _safe_name(name))
        return base + ".parquet", base + ".csv.gz"

    def load(self, name):
        """Zapisane notowania jako DataFrame (indeks = data) albo None."""
        pq, csv = self._paths(name)
        try:
            if os.path.exists(pq) and HAS_PARQUET:
                df = pd.read_parquet(pq)
            elif os.path.exists(csv):
                df = pd.read_csv(csv, parse_dates=["date"])
            else:
                return None
        except Exception:
            logger.exception("Nie udało się odczytać notowań %s", name)
            return None
        df = df.set_index("date").sort_index()
        df.index = pd.to_datetime(df.index)
        return df

    def save(self, name, df):
        pq, csv = self._paths(name)
        out = df.reset_index()
        target = pq if self.parquet else csv
        tmp = target + ".tmp"
        if self.parquet:
            out.to_parquet(tmp, index=False)
        else:
            out.to_csv(tmp, index=False, compression="gzip")
        self._replace(tmp, target)
        other = csv if self.parquet else pq
        if os.path.exists(other):      # zmiana formatu - nie zostawiamy starej kopii
            try:
                os.remove(other)
            except OSError:
                pass

    @staticmethod
    def _replace(tmp, target, attempts=5):
        # Na Windowsie (zwłaszcza w OneDrive) plik bywa chwilowo zablokowany - ponawiamy.
        for i in range(attempts):
            try:
                os.replace(tmp, target)
                return
            except PermissionError:
                if i == attempts - 1:
                    raise
                time.sleep(0.5 * (i + 1))

    def _load_meta(self):
        try:
            with open(self.meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_meta(self):
        tmp = self.meta_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.meta, f, ensure_ascii=False, indent=1)
        self._replace(tmp, self.meta_path)

    # ---------- pobieranie ----------
    def _fetch(self, symbols, start):
        """Pobiera kilka symboli naraz, z jedną ponowną próbą przy limicie zapytań."""
        for attempt in range(2):
            try:
                raw = self.download_fn(list(symbols), start)
                return {s: extract(raw, s) for s in symbols}
            except Exception as e:
                if attempt == 0 and "rate" in str(e).lower():
                    self.sleep(30)
                    continue
                logger.warning("Pobieranie %s nie powiodło się: %s", symbols, e)
                return {s: None for s in symbols}
        return {s: None for s in symbols}

    @staticmethod
    def market(name, kind):
        if kind == "stock":
            return U.market_of(name)
        if kind == "fx":
            return "FX"
        return INDEX_MARKET.get(name, "US")

    def last_closed_day(self, market):
        """Ostatni dzień, którego sesja na pewno się skończyła (data lokalna giełdy)."""
        tz, hh, mm = SESSION_CLOSE[market]
        local = self.now.tz_convert(tz)
        closed = (local.hour, local.minute) >= (hh, mm)
        day = local.normalize().tz_localize(None)
        return day if closed else day - pd.Timedelta(days=1)

    def _trim(self, df, name, kind):
        """Odcina dzisiejszy wiersz, jeśli sesja jeszcze trwa (Yahoo podaje wtedy ceny z trakcie dnia)."""
        if df is None:
            return None
        df = df[df.index <= self.last_closed_day(self.market(name, kind))]
        return df if len(df) else None

    def _full_start(self):
        return (self.today - timedelta(days=int(365.25 * YEARS) + 10)).isoformat()

    def update(self, only=None, full=False, progress=print):
        """
        Pobiera albo uzupełnia notowania. Zwraca raport:
        {nowe, uzupelnione, pelne_odswiezenie, bez_zmian, puste, bledy}.
        """
        items = items_from_universe(only)
        report = {"nowe": [], "uzupelnione": [], "pelne_odswiezenie": [], "bez_zmian": [],
                  "puste": [], "bledy": []}
        existing = {name: self.load(name) for name, _, _ in items}

        full_items, inc_items = [], []
        for it in items:
            df = existing[it[0]]
            too_short = it[1] != "stock" and df is not None and len(df) < MIN_INDEX_ROWS
            (full_items if (full or df is None or df.empty or too_short) else inc_items).append(it)

        # Przyrostowo: od ostatniej zapisanej daty minus zakładka (jedna data startu na paczkę).
        refetch = []
        for i in range(0, len(inc_items), CHUNK):
            chunk = inc_items[i:i + CHUNK]
            start = min(existing[n].index.max() for n, _, _ in chunk) - pd.Timedelta(days=OVERLAP_DAYS)
            got = self._fetch([self._symbol(n, c) for n, _, c in chunk], start.date().isoformat())
            for name, kind, cands in chunk:
                raw_new = got.get(self._symbol(name, cands))
                new = self._trim(raw_new, name, kind)
                old = existing[name]
                if raw_new is None:
                    report["bledy"].append(name)
                    continue
                if new is None:                  # przyszedł tylko niedokończony dzisiejszy dzień
                    report["bez_zmian"].append(name)
                    continue
                if self._history_changed(old, new):
                    refetch.append((name, kind, cands))
                    continue
                merged = pd.concat([old[old.index < new.index.min()][PRICE_COLS], new])
                merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                added = len(merged) - len(old)
                self._store(name, kind, merged, self._symbol(name, cands))
                report["uzupelnione" if added > 0 else "bez_zmian"].append(name)
            progress(f"  uzupełniono {min(i + CHUNK, len(inc_items))}/{len(inc_items)}")
            self.sleep(PAUSE)

        # Pełne pobranie: nowe instrumenty, --full i te, którym zmieniła się historia.
        todo = full_items + refetch
        start = self._full_start()
        stocks = [it for it in todo if it[1] == "stock"]
        for i in range(0, len(stocks), CHUNK):
            chunk = stocks[i:i + CHUNK]
            got = self._fetch([n for n, _, _ in chunk], start)
            for name, kind, cands in chunk:
                self._finish_full(name, kind, cands[0], got.get(name), existing[name], refetch, report)
            progress(f"  pobrano {min(i + CHUNK, len(stocks))}/{len(stocks)} spółek")
            self.sleep(PAUSE)
        # Indeksy i waluty po kolei - każdy ma kilku kandydatów na symbol. Bierzemy ten z NAJDŁUŻSZĄ
        # historią: Yahoo dla części symboli (np. WIG20.WA) zwraca tylko ostatnią sesję.
        for name, kind, cands in [it for it in todo if it[1] != "stock"]:
            best, used = None, cands[0]
            for sym in cands:
                df = self._fetch([sym], start).get(sym)
                if df is not None and (best is None or len(df) > len(best)):
                    best, used = df, sym
                if best is not None and len(best) >= MIN_INDEX_ROWS:
                    break
                self.sleep(PAUSE)
            old = existing[name]
            if best is not None and old is not None and len(old) > len(best):
                best = None          # nie zastępujemy dłuższej historii krótszą
            if best is None and old is not None:
                report["bez_zmian"].append(name)
                continue
            self._finish_full(name, kind, used, best, old, refetch, report)

        ok = sum(len(report[k]) for k in ("nowe", "uzupelnione", "pelne_odswiezenie", "bez_zmian"))
        if ok == 0 and (report["puste"] or report["bledy"]):
            # Nic nie przyszło - to raczej brak internetu albo blokada Yahoo niż złe symbole.
            report["brak_polaczenia"] = True
            report["bledy"] += report["puste"]
            report["puste"] = []
        else:
            for name in report["puste"]:
                if existing[name] is None:      # stare dane zostają, jeśli były
                    self.meta[name] = {**(self.meta.get(name) or {}), "kind": dict((n, k) for n, k, _ in items)[name],
                                       "status": "brak danych w Yahoo",
                                       "checked_at": datetime.now().isoformat(timespec="seconds")}
        self._compute_gaps()
        self._save_meta()
        return report

    def _symbol(self, name, cands):
        return (self.meta.get(name) or {}).get("symbol") or cands[0]

    def _finish_full(self, name, kind, symbol, df, old, refetch, report):
        df = self._trim(df, name, kind)
        if df is None:
            report["puste"].append(name)     # zapis do meta dopiero na końcu (patrz update)
            return
        self._store(name, kind, df, symbol)
        if any(r[0] == name for r in refetch):
            report["pelne_odswiezenie"].append(name)
        else:
            report["nowe"].append(name)

    @staticmethod
    def _history_changed(old, new):
        """Czy Yahoo przeliczyło stare ceny (split, dywidenda) na zakładce?
        Ostatni zapisany dzień pomijamy - mógł zostać zapisany w trakcie sesji."""
        common = old.index[old.index < old.index.max()].intersection(new.index)
        if len(common) == 0:
            return False
        a, b = old.loc[common], new.loc[common]
        for col in ("close", "adj_close"):
            x, y = a[col].to_numpy(float), b[col].to_numpy(float)
            ok = ~(np.isnan(x) | np.isnan(y))
            if ok.any() and np.max(np.abs(x[ok] - y[ok]) / np.maximum(np.abs(y[ok]), 1e-12)) > 1e-4:
                return True
        return False

    def _store(self, name, kind, df, symbol):
        df = df[PRICE_COLS].copy()
        df["quality"] = quality_flags(df, kind)
        self.save(name, df)
        bad = int(((df["quality"] & BAD) != 0).sum())
        zero_vol = int(((df["quality"] & Q_ZERO_VOL) != 0).sum())
        self.meta[name] = {
            "kind": kind, "symbol": symbol, "status": "ok",
            "market": U.market_of(name) if kind == "stock" else None,
            "rows": int(len(df)), "first": df.index.min().date().isoformat(),
            "last": df.index.max().date().isoformat(), "bad_rows": bad, "zero_volume_rows": zero_vol,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _compute_gaps(self):
        """Dni sesyjne, których brakuje spółce - względem kalendarza jej indeksu odniesienia."""
        calendars = {}
        for market, index_name in U.BENCHMARK.items():
            df = self.load(index_name)
            if df is not None:
                calendars[market] = df.index
        for name, m in self.meta.items():
            if m.get("kind") != "stock" or m.get("status") != "ok":
                continue
            cal = calendars.get(m.get("market"))
            df = self.load(name)
            if cal is None or df is None:
                continue
            span = cal[(cal >= df.index.min()) & (cal <= df.index.max())]
            m["gaps"] = int(len(span.difference(df.index)))

    # ---------- raport ----------
    def status(self):
        """Podsumowanie stanu bazy: liczby, braki, podejrzane dni, nieaktualne instrumenty."""
        # tylko instrumenty z aktualnej listy (universe.py) - usunięte z listy nie wracają w raporcie
        listed = {n for n, _, _ in items_from_universe()}
        meta = {n: m for n, m in self.meta.items() if n in listed}
        ok = {n: m for n, m in meta.items() if m.get("status") == "ok"}
        empty = sorted(n for n, m in meta.items() if m.get("status") != "ok")
        last_by_market = {}
        for mk, idx in U.BENCHMARK.items():
            if idx in ok:
                last_by_market[mk] = ok[idx]["last"]
        stale = sorted(n for n, m in ok.items() if m.get("kind") == "stock"
                       and last_by_market.get(m.get("market")) and m["last"] < last_by_market[m["market"]])
        return {
            "katalog": self.root,
            "format": "parquet" if self.parquet else "csv.gz",
            "instrumentow": len(ok),
            "spolek_pl": sum(1 for m in ok.values() if m.get("market") == "PL"),
            "spolek_us": sum(1 for m in ok.values() if m.get("market") == "US"),
            "wierszy": sum(m["rows"] for m in ok.values()),
            "bez_danych": empty,
            "krotka_historia": {n: m["rows"] for n, m in ok.items()
                                if m.get("kind") != "stock" and m["rows"] < MIN_INDEX_ROWS},
            "nieaktualne": stale,
            "podejrzane_dni": {n: m["bad_rows"] for n, m in ok.items() if m.get("bad_rows")},
            "dziury": {n: m["gaps"] for n, m in ok.items() if m.get("gaps", 0) > 5},
            "ostatnia_sesja": last_by_market,
        }


# ============================================================================
# WIERSZ POLECEŃ
# ============================================================================

def _print_report(rep):
    names = {"nowe": "pobrane po raz pierwszy", "uzupelnione": "uzupełnione o nowe dni",
             "pelne_odswiezenie": "pobrane od nowa (split/dywidenda zmieniły historię)",
             "bez_zmian": "bez nowych dni", "puste": "Yahoo nie zwróciło danych", "bledy": "błąd pobierania"}
    for k, label in names.items():
        items = rep.get(k) or []
        if items:
            tail = f": {', '.join(items[:12])}{' …' if len(items) > 12 else ''}" if k in ("puste", "bledy", "pelne_odswiezenie") else ""
            print(f"  {label}: {len(items)}{tail}")


def _print_status(st):
    print(f"Katalog: {st['katalog']}  (format: {st['format']})")
    rows = f"{st['wierszy']:,}".replace(",", " ")
    print(f"Instrumentów z danymi: {st['instrumentow']}  (GPW: {st['spolek_pl']}, USA: {st['spolek_us']}), "
          f"wierszy: {rows}")
    if st["ostatnia_sesja"]:
        print("Ostatnia sesja: " + ", ".join(f"{k} {v}" for k, v in st["ostatnia_sesja"].items()))
    if st["bez_danych"]:
        print(f"Bez danych w Yahoo ({len(st['bez_danych'])}) - jeśli powtórzy się przy kolejnym pobraniu, "
              f"usuń z lab/universe.py: {', '.join(st['bez_danych'])}")
    if st.get("krotka_historia"):
        print("Indeksy z krótką historią (Yahoo nie ma pełnych danych; cechy rynku policzą się wtedy "
              "ze średniej spółek): " + ", ".join(f"{n} {r} dni" for n, r in st["krotka_historia"].items()))
    if st["nieaktualne"]:
        print(f"Nieaktualne (brak ostatniej sesji): {', '.join(st['nieaktualne'])}")
    if st["podejrzane_dni"]:
        top = sorted(st["podejrzane_dni"].items(), key=lambda x: -x[1])[:10]
        print("Podejrzane dni (model ich nie użyje): " + ", ".join(f"{n} {c}" for n, c in top))
    if st["dziury"]:
        top = sorted(st["dziury"].items(), key=lambda x: -x[1])[:10]
        print("Brakujące sesje (powyżej 5): " + ", ".join(f"{n} {c}" for n, c in top))


def main(argv=None):
    p = argparse.ArgumentParser(prog="py -m lab.data_store", description="QuantPulse - magazyn notowań")
    sub = p.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("update", help="pobierz albo uzupełnij notowania")
    up.add_argument("--full", action="store_true", help="pobierz wszystko od nowa")
    up.add_argument("--only", default="", help="tylko te symbole, po przecinku")
    sub.add_parser("status", help="raport o stanie bazy")
    sh = sub.add_parser("show", help="ostatnie notowania i podejrzane dni jednego instrumentu")
    sh.add_argument("name", help="np. PKO.WA, AAPL, WIG20")
    sh.add_argument("-n", type=int, default=8, help="ile ostatnich dni pokazać")
    args = p.parse_args(argv)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)   # yfinance krzyczy o każdym pustym symbolu

    store = MarketStore()
    if args.cmd == "show":
        df = store.load(args.name)
        if df is None:
            print(f"Brak zapisanych notowań dla {args.name}.")
            return 1
        pd.set_option("display.width", 140)
        print(df.tail(args.n).round(4).to_string())
        bad = df[df["quality"] != 0]
        if len(bad):
            print(f"\nOznaczone dni ({len(bad)}):")
            for d, row in bad.tail(20).iterrows():
                print(f"  {d.date()}  {', '.join(describe_flags(int(row['quality'])))}")
        return 0
    if args.cmd == "update":
        only = [s for s in args.only.split(",") if s.strip()] or None
        print(f"Pobieram notowania do {store.root} (format: {'parquet' if store.parquet else 'csv.gz'})…")
        t0 = time.time()
        rep = store.update(only=only, full=args.full)
        print(f"Gotowe w {time.time() - t0:.0f} s:")
        _print_report(rep)
        if rep.get("brak_polaczenia"):
            print("\n  Nie przyszły żadne dane. Sprawdź internet albo spróbuj za kilka minut "
                  "(Yahoo czasem chwilowo blokuje zbyt wiele zapytań). Zapisane dane są nietknięte.")
        print()
    _print_status(store.status())


if __name__ == "__main__":
    sys.exit(main())
