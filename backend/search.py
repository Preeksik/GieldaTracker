"""
HossaLab - wyszukiwarka spółek po NAZWIE, nie po tickerze.

Problem: żeby dodać Orlen do portfela, trzeba było wiedzieć, że w Yahoo Finance
siedzi on pod "PKN.WA". Dla spółek zagranicznych i ETF-ów jest jeszcze gorzej.

Rozwiązanie: podpowiedzi z trzech źródeł, łączone i sortowane według trafności:

  1. TWOJE DANE (portfel, watchlista, historia sprzedaży) - natychmiast, bez sieci.
     Spółki, które już masz, mają się podpowiadać nawet przy padniętym internecie.
  2. PAMIĘĆ PODRĘCZNA - każde udane wyszukanie zapisuje symbol na stałe do
     search_index.json. Raz znaleziony Orlen podpowiada się potem offline.
  3. YAHOO FINANCE - publiczny endpoint wyszukiwania (ten sam, którego używa
     wyszukiwarka na finance.yahoo.com). Bez klucza API.

Gdy Yahoo nie odpowiada, punkty 1 i 2 działają dalej, a pole i tak przyjmuje
wpisany ręcznie ticker - wyszukiwarka nigdy nie blokuje dodania pozycji.

Podpięcie na końcu main.py:
    from search import setup_search
    setup_search(app, price_fn=get_current_price, name_fn=get_company_name,
                 currency_fn=get_currency, portfolio_fn=load_portfolio,
                 watchlist_fn=load_watchlist, sales_fn=load_sales)
"""

import os
import re
import json
import time
import logging
import threading
import unicodedata
from datetime import datetime, timezone

import requests

logger = logging.getLogger("gpw-api")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(BASE_DIR, "search_index.json")

YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
# Yahoo odrzuca domyślny User-Agent pythona, więc podajemy przeglądarkowy.
YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept": "application/json",
}

QUERY_CACHE_TTL = 6 * 3600
MIN_QUERY_LEN = 2

_lock = threading.RLock()
_index = None                  # trwały indeks symbolów
_query_cache = {}              # zapytanie -> (timestamp, wyniki)

# Giełdy, których symbole mają sens w tej aplikacji (reszta to głównie duplikaty
# tej samej spółki na kilkunastu rynkach, które tylko zaśmiecają podpowiedzi).
EXCHANGE_LABEL = {
    "WSE": "GPW", "WAR": "GPW",
    "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NASDAQ": "NASDAQ",
    "NYQ": "NYSE", "NYSE": "NYSE", "PCX": "NYSE Arca", "ASE": "NYSE American",
    "GER": "Xetra", "FRA": "Frankfurt", "STU": "Stuttgart", "MUN": "Monachium",
    "LSE": "Londyn", "AMS": "Amsterdam", "PAR": "Paryż", "MIL": "Mediolan",
    "EBS": "Szwajcaria", "MCE": "Madryt", "STO": "Sztokholm", "CPH": "Kopenhaga",
}

TYPE_LABEL = {
    "EQUITY": "akcje", "ETF": "ETF", "INDEX": "indeks",
    "MUTUALFUND": "fundusz", "CURRENCY": "waluta", "CRYPTOCURRENCY": "krypto",
    "FUTURE": "kontrakt",
}

# Typy, których nie chcemy w podpowiedziach przy budowaniu portfela.
SKIP_TYPES = {"OPTION", "FUTURE", "CURRENCY"}


# ============================================================================
# NORMALIZACJA TEKSTU
# ============================================================================

def norm(text):
    """
    'Żabka Polska S.A.' -> 'zabka polska sa'

    Bez zdejmowania polskich znaków wpisanie 'zabka' nie znalazłoby 'Żabka',
    a to najczęstszy sposób pisania w pośpiechu.
    """
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ł", "l").replace("Ł", "L")   # 'ł' nie rozkłada się przez NFKD
    return re.sub(r"[^a-z0-9 ]+", "", s.lower()).strip()


def looks_like_ticker(q):
    """'CDR.WA', 'NVDA' - tak. 'orlen' - nie."""
    return bool(re.fullmatch(r"[A-Za-z0-9]{1,6}(\.[A-Za-z]{1,3})?", q.strip())) and (
        q.strip().isupper() or "." in q
    )


# ============================================================================
# TRWAŁY INDEKS
# ============================================================================

def _load_index():
    global _index
    if _index is not None:
        return _index
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _index = data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        _index = {}
    except Exception:
        logger.exception("Nie udało się wczytać indeksu wyszukiwarki")
        _index = {}
    return _index


def _save_index():
    try:
        tmp = INDEX_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_load_index(), f, ensure_ascii=False, indent=1)
        os.replace(tmp, INDEX_FILE)
    except Exception:
        logger.exception("Nie udało się zapisać indeksu wyszukiwarki")


def known_symbols():
    """Kopia trwałego indeksu symboli - używana m.in. przez skaner Doradcy."""
    with _lock:
        return dict(_load_index())


def remember(entries):
    """Dopisuje znalezione symbole do trwałego indeksu (offline na przyszłość)."""
    if not entries:
        return
    with _lock:
        idx = _load_index()
        changed = False
        for e in entries:
            key = e["ticker"].upper()
            prev = idx.get(key)
            if not prev or prev.get("name") != e.get("name"):
                idx[key] = {"name": e.get("name"), "exchange": e.get("exchange"),
                            "type": e.get("type"), "seen": datetime.now(timezone.utc).isoformat()}
                changed = True
        if changed:
            _save_index()


# ============================================================================
# ŹRÓDŁA PODPOWIEDZI
# ============================================================================

def _entry(ticker, name, exchange=None, typ=None, source="yahoo", owned=False):
    return {
        "ticker": (ticker or "").upper(),
        "name": name or ticker,
        "exchange": EXCHANGE_LABEL.get((exchange or "").upper(), exchange or ""),
        "type": TYPE_LABEL.get((typ or "").upper(), (typ or "").lower()),
        "source": source,
        "owned": owned,
    }


def _local_entries(portfolio_fn, watchlist_fn, sales_fn):
    """Spółki, które użytkownik już zna: portfel, watchlista, sprzedaże."""
    out = {}

    def add(ticker, name, owned):
        if not ticker:
            return
        key = ticker.upper()
        if key not in out or owned:
            out[key] = _entry(ticker, name, source="portfel" if owned else "watchlista", owned=owned)

    for fn, owned in ((portfolio_fn, True), (sales_fn, False)):
        if not fn:
            continue
        try:
            for row in fn() or []:
                add(row.get("ticker"), row.get("name"), owned)
        except Exception:
            logger.exception("Nie udało się odczytać lokalnych pozycji do wyszukiwarki")

    if watchlist_fn:
        try:
            for row in watchlist_fn() or []:
                if isinstance(row, str):
                    add(row, None, False)
                elif isinstance(row, dict):
                    add(row.get("ticker"), row.get("name"), False)
        except Exception:
            logger.exception("Nie udało się odczytać watchlisty do wyszukiwarki")

    return list(out.values())


def _index_entries():
    with _lock:
        idx = dict(_load_index())
    return [_entry(t, v.get("name"), v.get("exchange"), v.get("type"), source="pamięć")
            for t, v in idx.items()]


def _yahoo_entries(query, limit=12):
    """Publiczny endpoint wyszukiwania Yahoo Finance. Bez klucza API."""
    try:
        resp = requests.get(
            YAHOO_SEARCH_URL,
            params={"q": query, "quotesCount": limit, "newsCount": 0, "listsCount": 0},
            headers=YAHOO_HEADERS,
            timeout=8,
        )
        if resp.status_code != 200:
            return [], f"Yahoo zwróciło {resp.status_code}"
        quotes = resp.json().get("quotes", [])
    except requests.exceptions.RequestException as e:
        return [], f"Brak połączenia z Yahoo: {type(e).__name__}"
    except ValueError:
        return [], "Yahoo zwróciło nieprawidłową odpowiedź"
    except Exception as e:
        logger.exception("Błąd wyszukiwania w Yahoo")
        return [], str(e)[:120]

    out = []
    for q in quotes:
        symbol = q.get("symbol")
        qtype = (q.get("quoteType") or "").upper()
        if not symbol or qtype in SKIP_TYPES:
            continue
        name = q.get("longname") or q.get("shortname") or symbol
        out.append(_entry(symbol, name, q.get("exchange"), qtype))
    return out, None


# ============================================================================
# SCALANIE I SORTOWANIE
# ============================================================================

def _score(entry, q_norm, q_raw):
    """
    Im mniej, tym wyżej. Kolejność ma odpowiadać temu, czego człowiek szuka:
    dokładny ticker, potem to co już ma, potem nazwa od początku, potem reszta.
    """
    ticker = entry["ticker"]
    name_n = norm(entry["name"])
    base = ticker.split(".")[0].lower()

    if ticker.lower() == q_raw.lower():
        s = 0
    elif base == q_raw.lower():
        s = 1
    elif name_n == q_norm:
        s = 2
    elif name_n.startswith(q_norm):
        s = 3
    elif base.startswith(q_raw.lower()):
        s = 4
    elif q_norm and q_norm in name_n:
        s = 5
    elif q_raw.lower() in ticker.lower():
        s = 6
    else:
        s = 9

    if entry.get("owned"):
        s -= 2          # spółki z portfela zawsze wyżej
    if entry["exchange"] == "GPW":
        s -= 0.5        # aplikacja jest przede wszystkim o GPW
    if entry["type"] == "ETF":
        s -= 0.25
    return (s, len(entry["ticker"]), entry["ticker"])


def _matches(entry, q_norm, q_raw):
    return (
        q_raw.lower() in entry["ticker"].lower()
        or (q_norm and q_norm in norm(entry["name"]))
    )


def search_tickers(query, limit, portfolio_fn, watchlist_fn, sales_fn):
    q_raw = (query or "").strip()
    q_norm = norm(q_raw)
    if len(q_raw) < MIN_QUERY_LEN:
        return {"results": [], "warning": None}

    # Lokalne źródła zawsze, sieć tylko gdy trzeba.
    local = [e for e in _local_entries(portfolio_fn, watchlist_fn, sales_fn)
             if _matches(e, q_norm, q_raw)]
    cached = [e for e in _index_entries() if _matches(e, q_norm, q_raw)]

    key = q_norm or q_raw.lower()
    with _lock:
        hit = _query_cache.get(key)
    if hit and time.monotonic() - hit[0] < QUERY_CACHE_TTL:
        remote, warning = hit[1], None
    else:
        remote, warning = _yahoo_entries(q_raw, limit=max(limit, 10))
        if remote:
            with _lock:
                _query_cache[key] = (time.monotonic(), remote)
            remember(remote)

    owned_tickers = {e["ticker"] for e in local if e["owned"]}
    merged = {}
    for e in remote + cached + local:          # local na końcu - nadpisuje źródłem
        t = e["ticker"]
        if t in merged:
            # Zachowujemy najlepszą nazwę (z Yahoo) i informację o posiadaniu.
            if e["owned"]:
                merged[t]["owned"] = True
                merged[t]["source"] = e["source"]
            if not merged[t]["name"] or merged[t]["name"] == t:
                merged[t]["name"] = e["name"]
            continue
        e = dict(e)
        e["owned"] = e["owned"] or t in owned_tickers
        merged[t] = e

    results = sorted(merged.values(), key=lambda e: _score(e, q_norm, q_raw))[:limit]

    # Gdy nic nie znaleziono, a wpisane wygląda na ticker - pozwólmy go użyć wprost.
    if not results and looks_like_ticker(q_raw):
        results = [_entry(q_raw, q_raw, source="wpisane ręcznie")]

    if warning and not results:
        warning = f"{warning}. Podpowiedzi działają tylko z Twojego portfela i pamięci."
    elif warning:
        warning = None      # coś pokazaliśmy lokalnie, nie ma po co straszyć

    return {"results": results, "warning": warning}


# ============================================================================
# ENDPOINTY
# ============================================================================

def setup_search(app, price_fn=None, name_fn=None, currency_fn=None,
                 portfolio_fn=None, watchlist_fn=None, sales_fn=None):
    from fastapi import HTTPException

    @app.get("/api/search/tickers")
    def search_endpoint(q: str = "", limit: int = 8):
        """
        Podpowiedzi spółek po nazwie albo tickerze.
        Przykład: /api/search/tickers?q=orlen
        """
        limit = max(1, min(int(limit or 8), 20))
        return search_tickers(q, limit, portfolio_fn, watchlist_fn, sales_fn)

    @app.get("/api/search/resolve")
    def resolve_endpoint(ticker: str):
        """
        Sprawdza, czy symbol faktycznie zwraca dane, i podaje nazwę, walutę i cenę.
        Wołane dopiero po wybraniu podpowiedzi - nie przy każdym wciśniętym klawiszu.
        """
        ticker = (ticker or "").strip().upper()
        if not ticker:
            raise HTTPException(status_code=400, detail="Podaj ticker.")

        name = None
        currency = None
        price = None
        try:
            if name_fn:
                name = name_fn(ticker)
            if currency_fn:
                currency = currency_fn(ticker)
            if price_fn:
                price = price_fn(ticker)
        except Exception:
            logger.exception("Błąd sprawdzania tickera %s", ticker)

        if price is None:
            with _lock:
                known = _load_index().get(ticker)
            return {
                "ticker": ticker,
                "ok": False,
                "name": name or (known or {}).get("name"),
                "currency": currency,
                "price": None,
                "detail": "Yahoo Finance nie zwróciło ceny dla tego symbolu. "
                          "Sprawdź sufiks giełdy (GPW: .WA, Xetra: .DE, Londyn: .L).",
            }

        if name:
            remember([_entry(ticker, name)])

        return {"ticker": ticker, "ok": True, "name": name or ticker,
                "currency": currency or "PLN", "price": price, "detail": None}

    @app.get("/api/search/index")
    def index_endpoint():
        """Podgląd zapamiętanych symboli - działa offline."""
        with _lock:
            idx = dict(_load_index())
        return {"count": len(idx), "symbols": idx}

    logger.info("Wyszukiwarka spółek gotowa (zapamiętanych symboli: %d).", len(_load_index()))