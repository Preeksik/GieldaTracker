"""
HossaLab - daty WYPŁATY dywidend (zakładka Dywidendy).

Yahoo podaje datę wypłaty tylko dla spółek z USA. Dla GPW bierzemy ją z tabeli
https://www.biznesradar.pl/dywidendy/ (wszystkie spółki GPW na jednej stronie: ostatni dzień
z prawem do dywidendy, dzień wypłaty, kwota, status: rekomendowana / uchwalona / wypłacona).

Zasady grzecznego pobierania: jedna strona, najwyżej raz na 12 godzin (pamięć podręczna
w data/cache/dividend_payments.json), robots.txt serwisu na to pozwala. Gdy strona nie odpowiada
albo zmieni wygląd - zostaje ostatnia zapisana wersja, a reszta zakładki działa jak dawniej.

Dane z serwisu to informacja pomocnicza - wiążące są komunikaty spółki (ESPI) i KDPW.
"""

import io
import os
import json
import logging
from datetime import datetime, date, timedelta

logger = logging.getLogger("gpw-api")

SOURCE_URL = "https://www.biznesradar.pl/dywidendy/"
SOURCE_NAME = "BiznesRadar"
CACHE_HOURS = 12
USER_AGENT = "Mozilla/5.0 (HossaLab - prywatny program do portfela)"
MONTHS = {"sty": 1, "lut": 2, "mar": 3, "kwi": 4, "maj": 5, "cze": 6,
          "lip": 7, "sie": 8, "wrz": 9, "paź": 10, "lis": 11, "gru": 12}
STATUSES = {"wypłacona", "uchwalona", "rekomendowana"}


def parse_pl_date(text):
    """'01 paź 26' -> '2026-10-01'. 'bd.' / puste -> None."""
    try:
        d, m, y = str(text).strip().split()
        return date(2000 + int(y) if len(y) == 2 else int(y), MONTHS[m.lower()], int(d)).isoformat()
    except (ValueError, KeyError):
        return None


def _col(columns, *words):
    for c in columns:
        low = str(c).lower()
        if all(w in low for w in words):
            return c
    return None


def parse_table(html):
    """Tabela z BiznesRadar -> lista wierszy. Pusta lista, gdy strona zmieniła wygląd."""
    import pandas as pd
    try:
        tables = pd.read_html(io.StringIO(html), flavor="lxml")   # bez zapasowego parsera (html5lib nie jest instalowany)
    except Exception:
        return []
    for t in tables:
        c_prof = _col(t.columns, "profil")
        c_pay = _col(t.columns, "wypłaty")
        c_right = _col(t.columns, "z prawem")
        if not (c_prof and c_pay and c_right):
            continue
        c_amt, c_stat, c_year = _col(t.columns, "na akcj"), _col(t.columns, "status"), _col(t.columns, "za rok")
        rows = []
        for _, r in t.iterrows():
            symbol = str(r[c_prof]).split()[0].strip().upper() if str(r[c_prof]).strip() else ""
            if not symbol or symbol == "NAN":
                continue
            status = str(r[c_stat]).strip().lower() if c_stat is not None else ""
            rows.append({
                "symbol": symbol,
                "payment_date": parse_pl_date(r[c_pay]),
                "last_with_right": parse_pl_date(r[c_right]),
                "amount": str(r[c_amt]).strip() if c_amt is not None and str(r[c_amt]) not in ("nan", "bd.") else None,
                "status": status if status in STATUSES else None,
                "for_year": str(r[c_year]).strip() if c_year is not None else None,
            })
        return rows
    return []


def _cache_file():
    from paths import cache_path
    return cache_path("dividend_payments.json")


def load_rows(fetch_fn=None, cache_file=None, now=None):
    """
    Wiersze z pamięci podręcznej (świeższe niż 12 h) albo pobrane na nowo.
    Zwraca (wiersze, informacja o źródle). Nigdy nie rzuca wyjątkiem.
    """
    now = now or datetime.now()
    cache_file = cache_file or _cache_file()
    cached = None
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            cached = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    if cached:
        try:
            age = now - datetime.fromisoformat(cached["fetched_at"])
            if age < timedelta(hours=CACHE_HOURS):
                return cached["rows"], {"name": SOURCE_NAME, "url": SOURCE_URL, "fetched_at": cached["fetched_at"]}
        except (KeyError, ValueError):
            pass

    def default_fetch():
        import requests
        r = requests.get(SOURCE_URL, headers={"User-Agent": USER_AGENT}, timeout=20)
        r.raise_for_status()
        return r.text

    try:
        rows = parse_table((fetch_fn or default_fetch)())
        if not rows:
            raise ValueError("nie znaleziono tabeli dywidend (strona mogła zmienić wygląd)")
        data = {"fetched_at": now.isoformat(timespec="seconds"), "rows": rows}
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(cache_file + ".tmp", cache_file)
        return rows, {"name": SOURCE_NAME, "url": SOURCE_URL, "fetched_at": data["fetched_at"]}
    except Exception as e:
        logger.warning("Daty wypłat dywidend (%s) niedostępne: %s - używam ostatniej zapisanej wersji", SOURCE_NAME, e)
        if cached and cached.get("rows"):
            return cached["rows"], {"name": SOURCE_NAME, "url": SOURCE_URL,
                                    "fetched_at": cached.get("fetched_at"), "stale": True}
        return [], {"name": SOURCE_NAME, "url": SOURCE_URL, "error": str(e)}


def gpw_symbol(ticker):
    """'VOX.WA' -> 'VOX'; spółki spoza GPW -> None."""
    t = (ticker or "").upper()
    return t[:-3] if t.endswith(".WA") else None


def enrich(positions, rows, today=None):
    """
    Dopisuje do każdej pozycji z /api/portfolio/dividends:
      next_payment  - najbliższa wypłata (albo ostatnia, jeśli w tym sezonie już wypłacono): data, status, źródło
      payments      - wszystkie wypłaty z tabeli (np. dwie raty jak w Kętach)
    i do każdej realnej wypłaty z Yahoo (realized_dividends) pole payment_date, gdy da się ją dopasować.
    """
    today = (today or date.today()).isoformat()
    by_symbol = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)

    for p in positions:
        sym = gpw_symbol(p.get("ticker"))
        mine = sorted([r for r in by_symbol.get(sym, []) if r.get("payment_date")],
                      key=lambda r: r["payment_date"]) if sym else []
        p["payments"] = mine
        nxt = None
        upcoming = [r for r in mine if r["payment_date"] >= today]
        if upcoming:
            r = upcoming[0]
            nxt = {"date": r["payment_date"], "status": r["status"] or "zapowiedziana", "source": SOURCE_NAME,
                   "amount": r["amount"], "last_with_right": r["last_with_right"], "paid": False,
                   "more": len(upcoming) - 1}
        elif mine:
            r = mine[-1]
            nxt = {"date": r["payment_date"], "status": "wypłacona", "source": SOURCE_NAME,
                   "amount": r["amount"], "last_with_right": r["last_with_right"], "paid": True, "more": 0}
        elif p.get("yahoo_payment_date"):
            d = p["yahoo_payment_date"]
            nxt = {"date": d, "status": "wypłacona" if d < today else "zapowiedziana", "source": "Yahoo",
                   "amount": None, "last_with_right": None, "paid": d < today, "more": 0}
        p["next_payment"] = nxt

        # Yahoo zapisuje wypłaty pod datą odcięcia - dopasowujemy dzień wypłaty z tabeli
        for item in p.get("realized_dividends") or []:
            ex = item.get("date")
            hits = []
            for r in mine:
                lw = r.get("last_with_right")
                if lw and ex and lw < ex <= (date.fromisoformat(lw) + timedelta(days=7)).isoformat():
                    hits.append(r["payment_date"])
            if hits:
                item["payment_date"] = hits[0] if len(hits) == 1 else ", ".join(hits)
    return positions
