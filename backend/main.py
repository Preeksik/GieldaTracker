from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yfinance as yf
import requests
import os
import json
import uuid
import math
import csv
import io
import re
import logging
import xml.etree.ElementTree as ET
from urllib.parse import quote
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()  # wczytuje zmienne z pliku .env leżącego obok main.py

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gpw-api")

# Najlepiej trzymać klucz w zmiennej środowiskowej, a nie w kodzie:
#   export GOOGLE_API_KEY="twoj_klucz"   (Linux/Mac)
#   $env:GOOGLE_API_KEY="twoj_klucz"     (Windows PowerShell)
API_KEY = os.environ.get("GOOGLE_API_KEY", "WKLEJ_TU_SWOJ_KLUCZ_JESLI_NIE_UZYWASZ_ENV")

# Nazwa modelu - jeśli nie masz pewności które masz dostępne,
# uruchom GET /api/models (zdefiniowany niżej) i sprawdź w przeglądarce/curl.
MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

# Plik, w którym trzymamy pozycje portfela (prosty JSON, bez bazy danych)
PORTFOLIO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio.json")

# Plik z watchlistą - spółki obserwowane pod kątem nadchodzących wydarzeń/raportów,
# NIEZALEŻNIE od tego, czy są w portfelu (np. Nvidia, o której inwestor myśli, ale jej nie ma)
WATCHLIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist.json")

app = FastAPI(title="GPW Analyst API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    ticker: str = "CDR.WA"
    question: str = "Jak oceniasz aktualny trend spółki na podstawie ostatnich dni?"
    days: int = 14


class PortfolioEntryCreate(BaseModel):
    ticker: str
    quantity: float
    buy_price: float
    buy_date: str  # format "YYYY-MM-DD"
    note: str = ""
    currency: str = ""  # "PLN"/"USD"/"EUR"/"GBP"... puste = autodetekcja po tickerze


class PositionAnalysisRequest(BaseModel):
    horizon: str = "sredni"  # "krotki" | "sredni" | "dlugi"
    custom_note: str = ""  # np. "rozważam czasowe wyjście pod konferencję Nvidii"
    previous_analysis: str = ""  # wypełnione tylko przy pytaniu uzupełniającym
    follow_up_question: str = ""  # jeśli podane, generujemy odpowiedź na to pytanie zamiast pełnej analizy


def get_company_name(ticker):
    """Próbuje pobrać pełną nazwę spółki (np. 'Creepy Jar S.A.'). Przy niepowodzeniu zwraca None."""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        name = info.get("longName") or info.get("shortName")
        if name:
            return name
    except Exception:
        logger.warning("Nie udało się pobrać nazwy spółki dla %s", ticker)
    return None


def get_currency(ticker):
    """Ustala walutę notowań danego tickera (np. 'USD' dla ETF-ów notowanych w USD, 'PLN' dla GPW)."""
    try:
        stock = yf.Ticker(ticker)
        currency = None
        try:
            currency = stock.fast_info.get("currency")
        except Exception:
            pass
        if not currency:
            currency = stock.info.get("currency")
        if currency:
            return currency.upper()
    except Exception:
        logger.warning("Nie udało się ustalić waluty dla %s", ticker)
    return None


def get_fx_rate(currency, cache=None):
    """
    Zwraca kurs wymiany 1 jednostki danej waluty na PLN (np. USD -> ~4.0).
    Dla PLN zwraca zawsze 1.0. `cache` to opcjonalny słownik do przekazania
    między wywołaniami w obrębie jednego requestu, żeby nie pytać Yahoo
    o ten sam kurs kilka razy.
    """
    currency = (currency or "PLN").upper()
    if currency == "PLN":
        return 1.0
    if cache is not None and currency in cache:
        return cache[currency]

    rate = None
    try:
        fx_ticker = yf.Ticker(f"{currency}PLN=X")
        hist = fx_ticker.history(period="5d")
        if not hist.empty:
            last = float(hist["Close"].iloc[-1])
            if not math.isnan(last) and not math.isinf(last):
                rate = last
    except Exception:
        logger.warning("Nie udało się pobrać kursu %sPLN", currency)

    if rate is None:
        try:
            fx_ticker = yf.Ticker(f"{currency}PLN=X")
            fast_price = fx_ticker.fast_info.get("lastPrice")
            if fast_price is not None:
                rate = float(fast_price)
        except Exception:
            logger.warning("Nie udało się pobrać kursu %sPLN (fast_info)", currency)

    if cache is not None:
        cache[currency] = rate
    return rate


def normalize_date(raw):
    """Próbuje sprowadzić różne formaty dat z CSV do YYYY-MM-DD."""
    raw = raw.strip()
    formats = ["%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"]
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw  # nie rozpoznano - zostawiamy jak jest, użytkownik poprawi ręcznie w razie czego


def get_news_headlines(company_name, max_items=6):
    """
    Pobiera świeże nagłówki newsów dla spółki z Google News RSS (darmowe, bez klucza API,
    zgodne z regulaminem - to publicznie dostępny kanał RSS, nie scraping).
    """
    try:
        url = f"https://news.google.com/rss/search?q={quote(company_name)}&hl=pl&gl=PL&ceid=PL:pl"
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = root.findall(".//item")[:max_items]
        headlines = []
        for item in items:
            title = (item.findtext("title") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            source = item.find("source")
            source_name = source.text if source is not None else ""
            if title:
                headlines.append(f"- {title} [{source_name}, {pub_date}]")
        return headlines
    except Exception:
        logger.exception("Nie udało się pobrać newsów dla %s", company_name)
        return []


def load_portfolio():
    if not os.path.exists(PORTFOLIO_FILE):
        return []
    try:
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.exception("Nie udało się odczytać portfolio.json - traktuję jako pusty portfel")
        return []


def save_portfolio(entries):
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def load_watchlist():
    """
    Wczytuje watchlistę spółek do obserwowania pod kątem katalizatorów.
    Przy pierwszym uruchomieniu tworzy przykładową listę znanych, zmiennych
    spółek (można ją dowolnie edytować/wyczyścić w interfejsie).
    """
    if not os.path.exists(WATCHLIST_FILE):
        default = ["NVDA", "TSLA", "AMD", "CDR.WA", "CRI.WA", "RVU.WA"]
        save_watchlist(default)
        return default
    try:
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.exception("Nie udało się odczytać watchlist.json - traktuję jako pustą listę")
        return []


def save_watchlist(tickers):
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(tickers, f, ensure_ascii=False, indent=2)


def safe_round(value, digits=2):
    """Zaokrągla liczbę, ale zamienia NaN/inf na None (żeby nie wywalić serializacji JSON)."""
    if value is None:
        return None
    try:
        if math.isnan(value) or math.isinf(value):
            return None
    except TypeError:
        return None
    return round(value, digits)


def get_current_price(ticker):
    """
    Próbuje pobrać aktualną cenę na kilka sposobów, bo yfinance bywa niestabilne
    (puste wyniki, rate limiting Yahoo). Loguje każdą nieudaną próbę, żeby było
    widać w terminalu co dokładnie zawiodło.
    """
    # Metoda 1: krótka historia (5 dni)
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="5d")
        if not hist.empty:
            price = float(hist["Close"].iloc[-1])
            if not math.isnan(price) and not math.isinf(price):
                return price
        logger.warning("history(period='5d') zwróciło pusty/nieprawidłowy wynik dla %s", ticker)
    except Exception:
        logger.exception("Błąd history(period='5d') dla %s", ticker)

    # Metoda 2: dłuższa historia - czasem 5d trafia w dziurę w danych
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1mo")
        if not hist.empty:
            price = float(hist["Close"].iloc[-1])
            if not math.isnan(price) and not math.isinf(price):
                return price
        logger.warning("history(period='1mo') też puste/nieprawidłowe dla %s", ticker)
    except Exception:
        logger.exception("Błąd history(period='1mo') dla %s", ticker)

    # Metoda 3: fast_info - inny endpoint Yahoo, czasem działa gdy history() zawodzi
    try:
        stock = yf.Ticker(ticker)
        price = stock.fast_info.get("lastPrice") or stock.fast_info.get("last_price")
        if price is not None and not math.isnan(price) and not math.isinf(price):
            return float(price)
        logger.warning("fast_info nie zwróciło ceny dla %s", ticker)
    except Exception:
        logger.exception("Błąd fast_info dla %s", ticker)

    logger.error("Wszystkie metody pobrania ceny zawiodły dla %s", ticker)
    return None


def get_next_earnings_date(ticker):
    """
    Próbuje znaleźć datę najbliższego raportu finansowego (earnings) spółki.
    Pokrycie danych dla mniejszych spółek GPW w Yahoo Finance bywa niepełne,
    więc to najlepszy wysiłek - jeśli się nie uda, zwraca None.
    """
    try:
        stock = yf.Ticker(ticker)
        cal = stock.calendar
        if isinstance(cal, dict) and cal.get("Earnings Date"):
            dates = cal["Earnings Date"]
            if isinstance(dates, (list, tuple)) and len(dates) > 0:
                return str(dates[0])
            return str(dates)
    except Exception:
        logger.warning("Brak danych kalendarza (earnings) dla %s", ticker)

    try:
        stock = yf.Ticker(ticker)
        edates = stock.get_earnings_dates(limit=8)
        if edates is not None and not edates.empty:
            future = edates[edates.index >= edates.index.max().normalize()]
            if not future.empty:
                return str(future.index[0].date())
    except Exception:
        logger.warning("Brak danych get_earnings_dates dla %s", ticker)

    return None


@app.get("/api/models")
def list_models():
    """Pomocniczy endpoint: pokazuje jakie modele Gemini są dostępne dla Twojego klucza."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    resp = requests.get(url)
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    data = resp.json()
    # Filtrujemy tylko te, które wspierają generateContent (czyli zwykłe zapytania tekstowe)
    usable = [
        m["name"] for m in data.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]
    return {"usable_models": usable, "raw_count": len(data.get("models", []))}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/portfolio")
def get_portfolio():
    """
    Zwraca wszystkie pozycje portfela wraz z aktualną wyceną i zyskiem/stratą.
    Koszt/wartość/zysk-strata są ZAWSZE przeliczone na PLN, niezależnie od tego,
    w jakiej walucie notowany jest dany instrument (np. ETF w USD).
    """
    entries = load_portfolio()
    enriched = []
    total_cost = 0.0
    total_value = 0.0
    price_cache = {}
    currency_cache = {}
    fx_cache = {}

    for entry in entries:
        ticker = entry["ticker"]

        if ticker not in price_cache:
            price_cache[ticker] = get_current_price(ticker)
        if ticker not in currency_cache:
            currency_cache[ticker] = get_currency(ticker)

        current_price = price_cache[ticker]
        # Waluta zakupu - to co zapisaliśmy przy dodawaniu pozycji (ważne dla starych
        # wpisów sprzed wprowadzenia walut, które domyślnie zakładamy jako PLN)
        buy_currency = entry.get("currency") or "PLN"
        # Waluta notowań bieżącej ceny - zwykle taka sama jak buy_currency, ale
        # liczymy osobno na wszelki wypadek (np. inna klasa udziałów)
        quote_currency = currency_cache[ticker] or buy_currency

        fx_buy = get_fx_rate(buy_currency, fx_cache)
        fx_quote = get_fx_rate(quote_currency, fx_cache)

        cost = entry["quantity"] * entry["buy_price"] * fx_buy if fx_buy is not None else None
        value = (
            entry["quantity"] * current_price * fx_quote
            if current_price is not None and fx_quote is not None
            else None
        )
        profit = (value - cost) if (value is not None and cost is not None) else None
        profit_pct = (profit / cost * 100) if profit is not None and cost else None

        if cost is not None:
            total_cost += cost
        if value is not None:
            total_value += value

        enriched.append({
            **entry,
            "name": entry.get("name") or ticker,
            "currency": buy_currency,
            "quote_currency": quote_currency,
            "current_price": safe_round(current_price),
            "cost": safe_round(cost),
            "value": safe_round(value),
            "profit": safe_round(profit),
            "profit_pct": safe_round(profit_pct),
        })

    summary = {
        "total_cost": safe_round(total_cost) or 0.0,
        "total_value": safe_round(total_value) or 0.0,
        "total_profit": safe_round(total_value - total_cost) or 0.0,
        "total_profit_pct": safe_round((total_value - total_cost) / total_cost * 100) if total_cost > 0 else 0.0,
    }

    return {"positions": enriched, "summary": summary}


@app.post("/api/portfolio")
def add_portfolio_entry(entry: PortfolioEntryCreate):
    """Dodaje nową pozycję (transakcję kupna) do portfela."""
    entries = load_portfolio()
    ticker = entry.ticker.upper().strip()
    currency = entry.currency.strip().upper() if entry.currency else (get_currency(ticker) or "PLN")
    new_entry = {
        "id": str(uuid.uuid4()),
        "ticker": ticker,
        "name": get_company_name(ticker) or ticker,
        "quantity": entry.quantity,
        "buy_price": entry.buy_price,
        "currency": currency,
        "buy_date": entry.buy_date,
        "note": entry.note,
    }
    entries.append(new_entry)
    save_portfolio(entries)
    return new_entry


def extract_name_and_ticker(raw_value):
    """
    Rozbija pole typu 'NASDAQ 100 ETF (CNDX.UK)' na (nazwa, ticker).
    Jeśli nie ma nawiasu, traktuje całość jako ticker.
    """
    match = re.search(r"\(([^)]+)\)\s*$", raw_value)
    if match:
        ticker = match.group(1).strip()
        name = raw_value[: match.start()].strip()
        return name, ticker
    return None, raw_value.strip()


def find_header_row(lines):
    """
    Niektóre eksporty z platform brokerskich mają nad tabelą wiersze podsumowania
    (np. 'Łączny wolumen...'), więc szukamy pierwszego wiersza, który wygląda jak
    prawdziwy nagłówek tabeli - zawiera jednocześnie coś w stylu 'data' i
    'instrument/ticker/symbol' lub 'ilość/wolumen/quantity'.
    """
    for idx, line in enumerate(lines):
        lower = line.lower()
        delimiter = ";" if lower.count(";") > lower.count(",") else ","
        cells = [c.strip().lower() for c in line.split(delimiter)]
        has_date = any("data" in c or "date" in c for c in cells)
        has_instrument = any(
            any(key in c for key in ["instrument", "ticker", "symbol", "spółka", "spolka"])
            for c in cells
        )
        has_qty = any(
            any(key in c for key in ["ilość", "ilosc", "quantity", "qty", "wolumen"])
            for c in cells
        )
        if has_date and (has_instrument or has_qty):
            return idx, delimiter
    return None, None


@app.post("/api/portfolio/import-csv")
async def import_portfolio_csv(file: UploadFile = File(...)):
    """
    Importuje pozycje portfela z pliku CSV. Radzi sobie z dwoma typami plików:
    1. Prosty, płaski CSV z kolumnami ticker/ilość/cena/data.
    2. Eksporty z platform brokerskich, które mają nad tabelą wiersze podsumowania,
       nazwy kolumn typu 'Cena zakupu USD' czy 'Wolumen pozostały', oraz pole
       instrumentu w formie 'Nazwa spółki (TICKER)'.
    Importuje TYLKO otwarte/aktywne pozycje - jeśli plik ma osobną sekcję z
    zamkniętymi/zrealizowanymi transakcjami (np. nagłówek zawierający 'zamkniet'
    lub 'closed'), przestaje czytać w tym miejscu, żeby nie dodać ich jako aktywnych.
    """
    raw_bytes = await file.read()
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1")

    all_lines = text.splitlines()
    header_idx, delimiter = find_header_row(all_lines)

    if header_idx is None:
        raise HTTPException(
            status_code=400,
            detail="Nie znaleziono w pliku wiersza, który wygląda jak nagłówek tabeli pozycji."
        )

    # Zbieramy wiersze tabeli od nagłówka, aż do pustej linii albo kolejnej sekcji
    # (np. '=== POZYCJE ZAMKNIETE ===' - to już nie są aktywne pozycje).
    table_lines = [all_lines[header_idx]]
    for line in all_lines[header_idx + 1:]:
        stripped = line.strip()
        if not stripped:
            break
        # Zatrzymujemy się tylko na prawdziwym nagłówku nowej sekcji (np. '=== POZYCJE ZAMKNIETE ==='),
        # NIE na dowolnym wystąpieniu słowa 'zamknięta' - to słowo może pojawić się też w statusie
        # częściowo zrealizowanej, ale wciąż otwartej pozycji (np. 'Otwarta (częściowo zamknięta...)').
        if stripped.startswith("=") and stripped.endswith("="):
            break
        table_lines.append(line)

    reader = csv.DictReader(io.StringIO("\n".join(table_lines)), delimiter=delimiter)
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="Nie udało się odczytać nagłówków tabeli pozycji.")

    def find_col(keywords):
        for name in reader.fieldnames:
            lower = name.strip().lower()
            if any(kw in lower for kw in keywords):
                return name
        return None

    col_instrument = find_col(["instrument", "ticker", "symbol", "spółka", "spolka"])
    col_qty = find_col(["pozostał", "pozostal", "ilość", "ilosc", "quantity", "qty", "wolumen"])
    col_price = find_col(["cena", "price"])
    col_date = find_col(["data", "date"])
    col_note = find_col(["notatka", "note", "uwagi", "komentarz", "status"])

    if not all([col_instrument, col_qty, col_price, col_date]):
        raise HTTPException(
            status_code=400,
            detail=(
                "Nie rozpoznano wymaganych kolumn (instrument/ticker, ilość, cena, data). "
                "Znalezione nagłówki: " + ", ".join(reader.fieldnames)
            ),
        )

    # Waluta z nagłówka kolumny ceny, np. 'Cena zakupu USD' -> 'USD'. Jeśli nagłówek
    # jej nie podaje, dociągamy autodetekcję po tickerze (osobno dla każdej spółki niżej).
    known_currencies = {"PLN", "USD", "EUR", "GBP", "CHF", "JPY"}
    header_currency = None
    for code in known_currencies:
        if code in col_price.upper():
            header_currency = code
            break

    entries = load_portfolio()
    added = []
    errors = []
    currency_cache = {}

    for i, row in enumerate(reader, start=header_idx + 2):
        try:
            raw_instrument = (row.get(col_instrument) or "").strip()
            if not raw_instrument:
                continue

            company_name, ticker = extract_name_and_ticker(raw_instrument)
            ticker = ticker.upper()

            quantity = float(str(row.get(col_qty, "")).replace(",", ".").strip())
            buy_price = float(str(row.get(col_price, "")).replace(",", ".").strip())

            raw_date = (row.get(col_date, "") or "").strip()
            raw_date = raw_date.split(" ")[0]  # obcinamy godzinę, jeśli jest ('24.10.2025 10:38' -> '24.10.2025')
            buy_date = normalize_date(raw_date)

            note = (row.get(col_note) or "").strip() if col_note else ""

            if header_currency:
                currency = header_currency
            else:
                if ticker not in currency_cache:
                    currency_cache[ticker] = get_currency(ticker) or "PLN"
                currency = currency_cache[ticker]

            new_entry = {
                "id": str(uuid.uuid4()),
                "ticker": ticker,
                "name": company_name or get_company_name(ticker) or ticker,
                "quantity": quantity,
                "buy_price": buy_price,
                "currency": currency,
                "buy_date": buy_date,
                "note": note,
            }
            entries.append(new_entry)
            added.append(new_entry)
        except Exception as e:
            errors.append(f"Wiersz {i}: {e}")

    save_portfolio(entries)
    return {"added": len(added), "errors": errors, "total_rows_processed": len(added) + len(errors)}


@app.post("/api/portfolio/fix-currencies")
def fix_currencies():
    """
    Naprawcza operacja dla pozycji dodanych PRZED wprowadzeniem obsługi walut
    (nie miały pola 'currency', więc były domyślnie liczone jako PLN nawet jeśli
    faktycznie chodziło o ETF w USD/EUR). Dla każdej pozycji bez ustawionej waluty
    (albo ustawionej na PLN mimo że ticker faktycznie notowany jest w innej walucie)
    dociąga prawdziwą walutę z Yahoo Finance i nadpisuje wpis.
    """
    entries = load_portfolio()
    fixed = []
    currency_cache = {}

    for entry in entries:
        ticker = entry["ticker"]
        current_currency = entry.get("currency")

        if ticker not in currency_cache:
            currency_cache[ticker] = get_currency(ticker)
        real_currency = currency_cache[ticker]

        # Naprawiamy tylko gdy brakuje waluty w ogóle, albo mamy realną (różną) walutę
        # z Yahoo, a zapisana wartość to nadal domyślne "PLN" - to sygnał, że nikt
        # świadomie nie wybrał PLN, tylko zadziałał stary fallback sprzed poprawki.
        if real_currency and (not current_currency or current_currency == "PLN") and real_currency != "PLN":
            entry["currency"] = real_currency
            fixed.append({"ticker": ticker, "id": entry["id"], "new_currency": real_currency})
        elif not current_currency:
            entry["currency"] = "PLN"

    save_portfolio(entries)
    return {"fixed_count": len(fixed), "fixed": fixed}


@app.delete("/api/portfolio/{entry_id}")
def delete_portfolio_entry(entry_id: str):
    """Usuwa pozycję z portfela po jej id."""
    entries = load_portfolio()
    filtered = [e for e in entries if e["id"] != entry_id]
    if len(filtered) == len(entries):
        raise HTTPException(status_code=404, detail="Nie znaleziono pozycji o podanym id")
    save_portfolio(filtered)
    return {"deleted": entry_id}


@app.post("/api/portfolio/{entry_id}/analyze")
def analyze_position(entry_id: str, request: PositionAnalysisRequest):
    """
    Analiza AI pojedynczej pozycji z portfela, dopasowana do wybranego horyzontu
    czasowego: krótko-, średnio- lub długoterminowego. Pomaga odpowiedzieć na
    pytanie "co z tym zrobić" - trzymać, sprzedać, dokupić, czy np. czasowo
    wyjść pod inną okazję i wrócić później.
    """
    entries = load_portfolio()
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono pozycji o podanym id")

    ticker = entry["ticker"]

    horizon_config = {
        "krotki": {
            "opis": "krótkoterminową (najbliższe kilka dni do dwóch tygodni)",
            "period": "1mo",
        },
        "sredni": {
            "opis": "średnioterminową (najbliższe 3-6 miesięcy)",
            "period": "6mo",
        },
        "dlugi": {
            "opis": "długoterminową (najbliższy rok lub dłużej)",
            "period": "2y",
        },
    }
    config = horizon_config.get(request.horizon, horizon_config["sredni"])

    current_price = get_current_price(ticker)
    buy_currency = entry.get("currency") or "PLN"
    quote_currency = get_currency(ticker) or buy_currency
    fx_cache = {}
    fx_buy = get_fx_rate(buy_currency, fx_cache)
    fx_quote = get_fx_rate(quote_currency, fx_cache)

    cost_pln = entry["quantity"] * entry["buy_price"] * fx_buy if fx_buy is not None else None
    value_pln = (
        entry["quantity"] * current_price * fx_quote
        if current_price is not None and fx_quote is not None
        else None
    )
    profit_pln = (value_pln - cost_pln) if (value_pln is not None and cost_pln is not None) else None
    profit_pct = (profit_pln / cost_pln * 100) if profit_pln is not None and cost_pln else None

    # Trend dopasowany do horyzontu - krótki termin patrzy na 1mo, długi na 2y
    trend_summary = "brak danych o trendzie"
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=config["period"])
        if not hist.empty:
            hist = hist.dropna()
            start_price = float(hist["Close"].iloc[0])
            end_price = float(hist["Close"].iloc[-1])
            min_price = float(hist["Close"].min())
            max_price = float(hist["Close"].max())
            change_pct = ((end_price - start_price) / start_price * 100) if start_price else 0
            avg_volume = float(hist["Volume"].mean())
            recent_volume = float(hist["Volume"].iloc[-5:].mean()) if len(hist) >= 5 else avg_volume
            trend_summary = (
                f"Okres {config['period']}: zmiana {change_pct:+.1f}% "
                f"(od {start_price:.2f} do {end_price:.2f} {quote_currency}), "
                f"zakres {min_price:.2f}-{max_price:.2f} {quote_currency}, "
                f"śr. wolumen dzienny {avg_volume:,.0f} "
                f"(ostatnio: {recent_volume:,.0f})."
            )
    except Exception:
        logger.exception("Nie udało się pobrać trendu (%s) dla %s", config["period"], ticker)

    earnings_info = get_next_earnings_date(ticker) or "brak dostępnych danych o terminie najbliższego raportu"

    # Wspólny blok danych o pozycji - używany zarówno w pełnej analizie, jak i w follow-upie
    position_facts = (
        f"Spółka: {entry.get('name') or ticker} ({ticker})\n"
        f"- Ilość: {entry['quantity']}, cena zakupu: {entry['buy_price']} {buy_currency} "
        f"(data zakupu: {entry['buy_date']})\n"
        f"- Aktualna cena: {current_price if current_price is not None else 'brak danych'} {quote_currency}\n"
        f"- Aktualny zysk/strata (przeliczone na PLN): "
        f"{safe_round(profit_pln) if profit_pln is not None else 'brak danych'} PLN "
        f"({safe_round(profit_pct) if profit_pct is not None else '?'}%)\n"
        f"- Trend: {trend_summary}\n"
        f"- Najbliższy raport finansowy / wydarzenie: {earnings_info}\n"
    )
    if entry.get("note"):
        position_facts += f"- Notatka użytkownika przy zakupie: {entry['note']}\n"

    if request.follow_up_question:
        # Tryb pytania uzupełniającego - odwołujemy się do wcześniejszej analizy zamiast pisać ją od nowa
        prompt = (
            "Jesteś doświadczonym analitykiem giełdowym prowadzącym dalszą rozmowę z inwestorem "
            "o jego pozycji. Oto aktualne dane pozycji:\n\n"
            f"{position_facts}\n"
            f"Wcześniej przygotowałeś dla tej pozycji następującą analizę ({config['opis']}):\n\n"
            f"{request.previous_analysis}\n\n"
            f"Inwestor ma teraz dodatkowe pytanie: \"{request.follow_up_question}\"\n\n"
            "Odpowiedz konkretnie i zwięźle na to pytanie, odwołując się do powyższego kontekstu "
            "(nie powtarzaj całej wcześniejszej analizy). Jeśli pytanie dotyczy decyzji finansowej "
            "(np. dokupienia akcji za nowe środki), podaj jasne rozumowanie za i przeciw. "
            "Zakończ jednym zdaniem zastrzeżenia, że to analiza edukacyjna, nie porada inwestycyjna."
        )
    else:
        prompt = (
            "Jesteś doświadczonym analitykiem giełdowym. Inwestor ma pozycję opisaną poniżej:\n\n"
            f"{position_facts}"
        )
        if request.custom_note:
            prompt += f"- Dodatkowy kontekst od inwestora teraz: {request.custom_note}\n"

        prompt += (
            f"\nZrób analizę {config['opis']} tej pozycji. Odpowiedz w tej strukturze:\n"
            "1. WYCENA: czy akcja wygląda na niedowartościowaną, sprawiedliwie wycenioną, czy przewartościowaną "
            "na podstawie dostępnego trendu i wolumenu (bez zmyślania wskaźników fundamentalnych, których nie masz).\n"
            "2. REKOMENDACJA na wskazany horyzont: SPRZEDAJ / TRZYMAJ / DOKUP - z konkretnym uzasadnieniem.\n"
            "3. PRAWDOPODOBIEŃSTWO: przybliżone szanse na zysk vs stratę w tym horyzoncie "
            "(np. \"~55% szans na wzrost\") z krótkim uzasadnieniem.\n"
            "4. ELASTYCZNOŚĆ KAPITAŁU: czy to sensowny moment, żeby czasowo wycofać kapitał z tej pozycji "
            "(np. pod inną okazję inwestycyjną) i wrócić później, czy lepiej trzymać nieprzerwanie - i dlaczego.\n"
            "Na końcu jedno zdanie zastrzeżenia, że to analiza edukacyjna, nie porada inwestycyjna."
        )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        resp = requests.post(url, json=payload, timeout=45)
    except requests.exceptions.RequestException as e:
        logger.exception("Błąd sieci przy wywołaniu Gemini (analiza pozycji)")
        raise HTTPException(status_code=502, detail=f"Nie udało się połączyć z Gemini API: {e}")

    try:
        data = resp.json()
    except ValueError:
        logger.error("Gemini zwrócił nie-JSON (analiza pozycji): %s", resp.text[:500])
        raise HTTPException(status_code=502, detail="Gemini API zwróciło nieprawidłową odpowiedź.")

    if resp.status_code != 200:
        err = data.get("error", {}).get("message", "Nieznany błąd API")
        logger.error("BŁĄD Z GOOGLE (analiza pozycji, status %s): %s", resp.status_code, err)
        raise HTTPException(status_code=502, detail=err)

    try:
        analysis_text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        logger.error("Nieoczekiwany kształt odpowiedzi Gemini (analiza pozycji): %s", data)
        raise HTTPException(status_code=502, detail="Gemini nie zwróciło treści analizy.")

    return {
        "ticker": ticker,
        "horizon": request.horizon,
        "analysis": analysis_text,
    }


def get_sector_info(ticker):
    """Próbuje pobrać sektor/branżę/kraj notowania spółki - potrzebne do oceny dywersyfikacji."""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        return {
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "country": info.get("country"),
        }
    except Exception:
        logger.warning("Nie udało się pobrać sektora/kraju dla %s", ticker)
        return {"sector": None, "industry": None, "country": None}


def call_gemini(prompt, timeout=60):
    """Wspólna funkcja wywołania Gemini - używana przez dywersyfikację i pytania o portfel."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
    except requests.exceptions.RequestException as e:
        logger.exception("Błąd sieci przy wywołaniu Gemini")
        raise HTTPException(status_code=502, detail=f"Nie udało się połączyć z Gemini API: {e}")

    try:
        data = resp.json()
    except ValueError:
        logger.error("Gemini zwrócił nie-JSON: %s", resp.text[:500])
        raise HTTPException(status_code=502, detail="Gemini API zwróciło nieprawidłową odpowiedź.")

    if resp.status_code != 200:
        err = data.get("error", {}).get("message", "Nieznany błąd API")
        logger.error("BŁĄD Z GOOGLE (status %s): %s", resp.status_code, err)
        raise HTTPException(status_code=502, detail=err)

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        logger.error("Nieoczekiwany kształt odpowiedzi Gemini: %s", data)
        raise HTTPException(status_code=502, detail="Gemini nie zwróciło treści odpowiedzi.")


def build_portfolio_context(portfolio_data):
    """Buduje tekstowy opis całego portfela - używany zarówno w dywersyfikacji, jak i w pytaniach."""
    positions = portfolio_data["positions"]
    summary = portfolio_data["summary"]

    lines = []
    for pos in positions:
        line = (
            f"- {pos.get('name') or pos['ticker']} ({pos['ticker']}): {pos['quantity']} szt., "
            f"kupione po {pos['buy_price']} {pos.get('currency', 'PLN')}, "
            f"aktualnie {pos['current_price']} {pos.get('quote_currency', pos.get('currency', 'PLN'))}, "
            f"wartość {pos['value']} PLN, zysk/strata {pos['profit']} PLN ({pos['profit_pct']}%)"
        )
        if pos.get("note"):
            line += f", notatka: {pos['note']}"
        lines.append(line)

    return (
        f"Łączny koszt portfela: {summary['total_cost']} PLN, "
        f"wartość: {summary['total_value']} PLN, "
        f"zysk/strata: {summary['total_profit']} PLN ({summary['total_profit_pct']}%).\n\n"
        "POZYCJE:\n" + "\n".join(lines)
    )


@app.get("/api/portfolio/diversification")
def portfolio_diversification():
    """
    Analiza dywersyfikacji całego portfela: koncentracja w sektorach/krajach/walutach,
    luki w portfelu, i konkretne sugestie rebalansowania.
    """
    portfolio_data = get_portfolio()
    positions = portfolio_data["positions"]
    summary = portfolio_data["summary"]

    if not positions:
        raise HTTPException(status_code=400, detail="Portfel jest pusty — dodaj przynajmniej jedną pozycję.")

    total_value = summary["total_value"] or 0
    sector_cache = {}
    blocks = []

    for pos in positions:
        ticker = pos["ticker"]
        if ticker not in sector_cache:
            sector_cache[ticker] = get_sector_info(ticker)
        info = sector_cache[ticker]

        weight_pct = (pos["value"] / total_value * 100) if pos["value"] and total_value else None

        blocks.append(
            f"- {pos.get('name') or ticker} ({ticker}): waga {safe_round(weight_pct)}% portfela, "
            f"wartość {pos['value']} PLN, waluta {pos.get('currency', 'PLN')}, "
            f"sektor: {info['sector'] or 'brak danych'}, branża: {info['industry'] or 'brak danych'}, "
            f"kraj: {info['country'] or 'brak danych'}"
        )

    prompt = (
        "Jesteś analitykiem zarządzania ryzykiem portfela inwestycyjnego. Poniżej masz skład portfela "
        "inwestora (waga każdej pozycji, sektor, branża, kraj, waluta). Oceń:\n"
        "1. KONCENTRACJA: czy portfel jest nadmiernie skoncentrowany w jednym sektorze, kraju, walucie "
        "lub pojedynczej spółce - podaj konkretne przybliżone %.\n"
        "2. LUKI: jakich ważnych klas aktywów/sektorów/regionów wyraźnie brakuje, biorąc pod uwagę to co już jest.\n"
        "3. REBALANSOWANIE: 2-3 konkretne, praktyczne sugestie co przegrupować lub jakiego typu instrument "
        "dokupić, żeby poprawić dywersyfikację (możesz wskazać typ instrumentu np. 'szerszy ETF na rynek "
        "europejski', nie musisz wskazywać konkretnego tickera).\n"
        "Zakończ jednym zdaniem zastrzeżenia, że to analiza edukacyjna, nie porada inwestycyjna.\n\n"
        f"Łączna wartość portfela: {summary['total_value']} PLN.\n\n"
        "SKŁAD PORTFELA:\n" + "\n".join(blocks)
    )

    report_text = call_gemini(prompt)
    return {"report": report_text}


class PortfolioQuestionRequest(BaseModel):
    question: str
    previous_analysis: str = ""  # kontekst z poprzednich pytań/odpowiedzi w tej samej rozmowie


@app.post("/api/portfolio/ask")
def portfolio_ask(request: PortfolioQuestionRequest):
    """
    Otwarte pytanie o cały portfel, np. 'w co zainwestować dodatkowe 5k na IKE -
    dokupić coś co już mam, czy szukać czegoś nowego?'. Odpowiedź opiera się na
    realnych, aktualnych danych portfela (wagi, waluty, zyski), z pamięcią
    poprzednich pytań w tej samej rozmowie.
    """
    portfolio_data = get_portfolio()
    if not portfolio_data["positions"]:
        raise HTTPException(status_code=400, detail="Portfel jest pusty — dodaj przynajmniej jedną pozycję.")

    portfolio_context = build_portfolio_context(portfolio_data)

    if request.previous_analysis:
        prompt = (
            "Jesteś doświadczonym doradcą inwestycyjnym prowadzącym dalszą rozmowę z inwestorem "
            "o jego portfelu.\n\n"
            f"AKTUALNY STAN PORTFELA:\n{portfolio_context}\n\n"
            f"Wcześniej w tej rozmowie napisałeś:\n\n{request.previous_analysis}\n\n"
            f"Inwestor pyta teraz: \"{request.question}\"\n\n"
            "Odpowiedz konkretnie, odwołując się do realnych danych portfela powyżej (wagi, waluty, zyski), "
            "nie powtarzaj całej wcześniejszej treści. Jeśli pytanie dotyczy nowych środków do zainwestowania, "
            "rozważ zarówno dokupienie istniejących pozycji jak i nowe kierunki, biorąc pod uwagę dywersyfikację. "
            "Zakończ jednym zdaniem zastrzeżenia, że to analiza edukacyjna, nie porada inwestycyjna."
        )
    else:
        prompt = (
            "Jesteś doświadczonym doradcą inwestycyjnym. Oto aktualny portfel inwestora:\n\n"
            f"{portfolio_context}\n\n"
            f"Inwestor pyta: \"{request.question}\"\n\n"
            "Odpowiedz konkretnie i praktycznie, odwołując się do realnych danych portfela powyżej "
            "(wagi pozycji, waluty, zyski/straty). Jeśli pytanie dotyczy nowych środków do zainwestowania, "
            "rozważ zarówno dokupienie istniejących pozycji jak i nowe kierunki, biorąc pod uwagę dywersyfikację "
            "portfela. Zakończ jednym zdaniem zastrzeżenia, że to analiza edukacyjna, nie porada inwestycyjna."
        )

    answer_text = call_gemini(prompt)
    return {"answer": answer_text}


class WatchlistAddRequest(BaseModel):
    ticker: str


@app.get("/api/watchlist")
def get_watchlist():
    """Zwraca aktualną watchlistę tickerów obserwowanych pod kątem katalizatorów."""
    return {"tickers": load_watchlist()}


@app.post("/api/watchlist")
def add_watchlist_ticker(request: WatchlistAddRequest):
    """Dodaje ticker do watchlisty (niezależnej od portfela)."""
    ticker = request.ticker.upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="Podaj ticker.")
    tickers = load_watchlist()
    if ticker not in tickers:
        tickers.append(ticker)
        save_watchlist(tickers)
    return {"tickers": tickers}


@app.delete("/api/watchlist/{ticker}")
def remove_watchlist_ticker(ticker: str):
    """Usuwa ticker z watchlisty."""
    ticker = ticker.upper().strip()
    tickers = load_watchlist()
    tickers = [t for t in tickers if t != ticker]
    save_watchlist(tickers)
    return {"tickers": tickers}


@app.get("/api/watchlist/catalysts")
def watchlist_catalysts():
    """
    Buduje 'radar katalizatorów' dla spółek z watchlisty (niezależnie od portfela):
    najbliższe raporty finansowe + świeże newsy, z oceną priorytetu uwagi pod kątem
    agresywnego, krótkoterminowego inwestowania pod nadchodzące wydarzenia.
    """
    tickers = load_watchlist()
    if not tickers:
        raise HTTPException(
            status_code=400,
            detail="Watchlista jest pusta — dodaj przynajmniej jeden ticker do obserwowania."
        )

    blocks = []
    for ticker in tickers:
        name = get_company_name(ticker) or ticker
        earnings = get_next_earnings_date(ticker) or "brak danych o terminie najbliższego raportu"
        headlines = get_news_headlines(name, max_items=4)

        block = f"### {name} ({ticker})\nNajbliższy raport finansowy: {earnings}\n"
        if headlines:
            block += "Świeże nagłówki:\n" + "\n".join(headlines)
        else:
            block += "Brak świeżych nagłówków w wyszukiwaniu."
        blocks.append(block)

    prompt = (
        "Jesteś analitykiem rynkowym budującym 'radar katalizatorów' dla agresywnego, "
        "krótkoterminowego inwestora, który chce wiedzieć o nadchodzących raportach finansowych "
        "i dużych wydarzeniach (konferencje, premiery produktów, prezentacje wyników) mogących "
        "wywołać gwałtowny ruch kursu w najbliższych tygodniach. Poniżej masz dane obserwowanych "
        "spółek - niezależnie od tego, czy inwestor je aktualnie posiada.\n\n"
        "Dla KAŻDEJ spółki oceń:\n"
        "1. Czy zbliża się raport finansowy lub inne wydarzenie w ciągu najbliższych ~30 dni - "
        "podaj konkretną datę, jeśli jest znana, albo napisz wprost że brak danych.\n"
        "2. Czy coś w nagłówkach sugeruje nadchodzącą konferencję, premierę produktu lub inny "
        "potencjalny katalizator.\n"
        "3. PRIORYTET UWAGI: WYSOKI / ŚREDNI / NISKI - im bliżej wydarzenia i im większy "
        "potencjalny wpływ na kurs, tym wyższy priorytet.\n"
        "Posortuj spółki od najwyższego priorytetu do najniższego. Jeśli dla danej spółki nic "
        "istotnego się nie dzieje, napisz to wprost - fałszywy alarm jest gorszy niż jego brak.\n"
        "Zakończ jednym zdaniem zastrzeżenia, że to analiza edukacyjna, nie porada inwestycyjna, "
        "oraz że granie pod eventy w krótkim terminie niesie wysokie ryzyko.\n\n"
        + "\n\n".join(blocks)
    )

    report_text = call_gemini(prompt, timeout=90)
    return {"report": report_text, "tickers_checked": tickers}


@app.post("/api/analyze")
def analyze_stock(request: AnalyzeRequest):
    # 1. Pobieranie danych giełdowych
    try:
        stock = yf.Ticker(request.ticker)
        hist = stock.history(period=f"{request.days}d")
    except Exception as e:
        logger.exception("Błąd yfinance")
        raise HTTPException(status_code=502, detail=f"Błąd pobierania danych z Yahoo Finance: {e}")

    if hist.empty:
        raise HTTPException(
            status_code=404,
            detail=f"Brak danych dla tickera '{request.ticker}'. Sprawdź czy sufiks .WA jest poprawny."
        )

    hist = hist.dropna()
    hist = hist[~hist.index.duplicated(keep="first")]

    chart_data = []
    data_lines = []

    for date, row in hist.iterrows():
        date_str = date.strftime("%Y-%m-%d")
        c = round(float(row["Close"]), 2)
        v = int(row["Volume"])
        chart_data.append({
            "time": date_str,
            "open": round(float(row["Open"]), 2),
            "high": round(float(row["High"]), 2),
            "low": round(float(row["Low"]), 2),
            "close": c,
            "value": v,
        })
        data_lines.append(f"{date_str}: Zamknięcie {c} PLN, Wolumen {v}")

    prompt = (
        f"Jesteś analitykiem giełdowym. Na podstawie danych spółki {request.ticker}:\n"
        + "\n".join(data_lines)
        + f"\n\nOdpowiedz krótko na pytanie: {request.question}"
    )

    # 2. Wywołanie Gemini REST API
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        resp = requests.post(url, json=payload, timeout=30)
    except requests.exceptions.RequestException as e:
        logger.exception("Błąd sieci przy wywołaniu Gemini")
        raise HTTPException(status_code=502, detail=f"Nie udało się połączyć z Gemini API: {e}")

    try:
        data = resp.json()
    except ValueError:
        logger.error("Gemini zwrócił nie-JSON: %s", resp.text[:500])
        raise HTTPException(status_code=502, detail="Gemini API zwróciło nieprawidłową odpowiedź.")

    if resp.status_code != 200:
        err = data.get("error", {}).get("message", "Nieznany błąd API")
        logger.error("BŁĄD Z GOOGLE (status %s): %s", resp.status_code, err)
        # Podpowiedź gdy model nie istnieje/nie jest dostępny dla klucza
        hint = ""
        if resp.status_code == 404:
            hint = f" Model '{MODEL_NAME}' może nie być dostępny dla Twojego klucza — sprawdź GET /api/models."
        raise HTTPException(status_code=502, detail=f"{err}{hint}")

    try:
        ai_text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        logger.error("Nieoczekiwany kształt odpowiedzi Gemini: %s", data)
        raise HTTPException(status_code=502, detail="Gemini nie zwróciło treści (możliwy filtr bezpieczeństwa).")

    return {
        "ticker": request.ticker,
        "ai_analysis": ai_text,
        "chart_data": chart_data,
    }