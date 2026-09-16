from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
import yfinance as yf
import requests
import pandas as pd
import os
import json
import uuid
import math
import csv
import io
import re
import logging
import smtplib
from email.mime.text import MIMEText
import xml.etree.ElementTree as ET
from urllib.parse import quote
from datetime import datetime
from typing import Optional
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

# Plik z historią ZREALIZOWANYCH sprzedaży (do liczenia realnego zysku i PIT-38)
SALES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sales.json")

# Plik z watchlistą - spółki obserwowane pod kątem nadchodzących wydarzeń/raportów,
# NIEZALEŻNIE od tego, czy są w portfelu (np. Nvidia, o której inwestor myśli, ale jej nie ma)
WATCHLIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist.json")

# Plik z alertami cenowymi
ALERTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alerts.json")

# Plik z historią wartości portfela w czasie (snapshoty co ~30 min, gdy backend działa)
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio_history.json")

# Konfiguracja e-mail (opcjonalna) - jeśli nie ustawisz tych zmiennych w .env,
# alerty nadal będą działać, ale tylko w apce, bez wysyłki maila.
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_TO = os.environ.get("SMTP_TO", "")  # adres, na który mają przychodzić powiadomienia
EMAIL_ENABLED = bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD and SMTP_TO)

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
    horizon: str = "sredni"  # "krotki" | "sredni" | "dlugi"


class PortfolioEntryCreate(BaseModel):
    ticker: str
    quantity: float
    buy_price: float
    buy_date: str  # format "YYYY-MM-DD"
    note: str = ""
    currency: str = ""  # "PLN"/"USD"/"EUR"/"GBP"... puste = autodetekcja po tickerze
    account: str = "zwykle"  # "zwykle" | "ike" | "ikze" - decyduje o podatku Belki


class AccountUpdateRequest(BaseModel):
    account: str


class SellRequest(BaseModel):
    quantity: float
    sell_price: float
    sell_date: str  # format "YYYY-MM-DD"
    sell_currency: str = ""  # puste = ta sama waluta co przy zakupie


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


def _normalize_date_index(series):
    """Sprowadza indeks pandas Series (daty z yfinance, czasem tz-aware) do samych dat bez strefy czasowej."""
    if series is None or series.empty:
        return series
    if series.index.tz is not None:
        series = series.copy()
        series.index = series.index.tz_localize(None)
    series.index = series.index.normalize()
    return series


def _safe_start_date(start_date):
    """
    Sprowadza datę startową do formatu, który akceptuje yfinance ('YYYY-MM-DD').
    Chroni przed wywaleniem całej rekonstrukcji, gdy w danych siedzi data w innym
    formacie (np. '20251024' z importu XTB).
    """
    s = str(start_date).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    logger.warning("Nierozpoznany format daty '%s' - używam domyślnego zakresu 5 lat wstecz", s)
    return (datetime.now() - pd.Timedelta(days=365 * 5)).strftime("%Y-%m-%d")


def get_historical_price_series(ticker, start_date):
    """Zwraca pandas Series (indeks: data, wartość: cena zamknięcia) od start_date do dziś."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(start=_safe_start_date(start_date))
        if hist.empty:
            return None
        return _normalize_date_index(hist["Close"])
    except Exception:
        logger.exception("Nie udało się pobrać historycznych cen dla %s (rekonstrukcja wykresu)", ticker)
        return None


def get_historical_fx_series(currency, start_date):
    """Zwraca pandas Series historycznego kursu danej waluty do PLN od start_date do dziś."""
    if currency == "PLN":
        return None  # nie potrzebujemy - traktujemy jako stałe 1.0
    try:
        fx_ticker = yf.Ticker(f"{currency}PLN=X")
        hist = fx_ticker.history(start=_safe_start_date(start_date))
        if hist.empty:
            return None
        return _normalize_date_index(hist["Close"])
    except Exception:
        logger.exception("Nie udało się pobrać historycznego kursu %sPLN (rekonstrukcja wykresu)", currency)
        return None


def get_fx_rate_on_date(currency, date_str):
    """Zwraca kurs danej waluty do PLN na konkretny dzień historyczny (do liczenia zrealizowanego zysku)."""
    currency = (currency or "PLN").upper()
    if currency == "PLN":
        return 1.0
    series = get_historical_fx_series(currency, date_str)
    if series is None or series.empty:
        return get_fx_rate(currency)  # fallback: dzisiejszy kurs, gdy brak historii
    target = pd.Timestamp(date_str)
    s_upto = series[series.index <= target]
    if s_upto.empty:
        return get_fx_rate(currency)
    return float(s_upto.iloc[-1])


_reconstructed_history_cache = {"data": None, "computed_at": None}


def reconstruct_portfolio_history():
    """
    Odtwarza wartość CAŁEGO portfela dzień po dniu, od daty NAJWCZEŚNIEJSZEGO zakupu
    do dziś. Kluczowe: uwzględnia też ZREALIZOWANE SPRZEDAŻE (sales.json) - inaczej
    historyczne dni SPRZED sprzedaży byłyby liczone na podstawie dzisiejszej,
    już zmniejszonej ilości akcji, co fałszuje cały wykres wstecz.

    Dla każdej "transzy" (nadal otwartej albo już sprzedanej) liczymy okres, w którym
    faktycznie była w portfelu: od daty zakupu do dziś (jeśli nadal otwarta) albo do
    daty sprzedaży (jeśli zamknięta). 'total_value' i 'total_cost' liczą się tylko
    z transz aktywnych w danym dniu - dzięki temu obie linie spadają razem w dniu
    sprzedaży, co jasno pokazuje że to realizacja zysku, nie spadek wartości rynkowej.

    Wynik jest cache'owany na 15 minut w pamięci procesu.
    """
    now = datetime.now()
    if (
        _reconstructed_history_cache["data"] is not None
        and _reconstructed_history_cache["computed_at"] is not None
        and (now - _reconstructed_history_cache["computed_at"]).total_seconds() < 15 * 60
    ):
        return _reconstructed_history_cache["data"]

    entries = load_portfolio()
    sales = load_sales()
    today_str = now.strftime("%Y-%m-%d")

    # Budujemy listę "transz" - każda ma: ticker, walutę, datę startu, datę końca
    # (None = nadal otwarta), ilość i cenę zakupu.
    tranches = []
    for e in entries:
        tranches.append({
            "ticker": e["ticker"],
            "currency": e.get("currency") or "PLN",
            "buy_date": e["buy_date"],
            "end_date": None,
            "quantity": e["quantity"],
            "buy_price": e["buy_price"],
        })
    for s in sales:
        tranches.append({
            "ticker": s["ticker"],
            "currency": s.get("buy_currency") or "PLN",
            "buy_date": s["buy_date"],
            "end_date": s["sell_date"],
            "quantity": s["quantity"],
            "buy_price": s["buy_price"],
        })

    if not tranches:
        empty = {"history": [], "events": []}
        _reconstructed_history_cache.update(data=empty, computed_at=now)
        return empty

    earliest_date = min(t["buy_date"] for t in tranches)
    tickers = set(t["ticker"] for t in tranches)

    price_series = {}
    currency_by_ticker = {}
    for ticker in tickers:
        series = get_historical_price_series(ticker, earliest_date)
        if series is not None:
            price_series[ticker] = _filter_price_outliers(series)
        matching = next((t for t in tranches if t["ticker"] == ticker), None)
        currency_by_ticker[ticker] = get_currency(ticker) or (matching["currency"] if matching else "PLN")

    if not price_series:
        empty = {"history": [], "events": []}
        _reconstructed_history_cache.update(data=empty, computed_at=now)
        return empty

    fx_series = {}
    for currency in set(currency_by_ticker.values()):
        if currency == "PLN":
            continue
        series = get_historical_fx_series(currency, earliest_date)
        if series is not None:
            fx_series[currency] = series

    all_dates = sorted(set().union(*(s.index for s in price_series.values())))

    def price_on(ticker, date):
        s = price_series.get(ticker)
        if s is None:
            return None
        s_upto = s[s.index <= date]
        return float(s_upto.iloc[-1]) if not s_upto.empty else None

    def fx_on(currency, date):
        if currency == "PLN":
            return 1.0
        s = fx_series.get(currency)
        if s is None:
            return None
        s_upto = s[s.index <= date]
        return float(s_upto.iloc[-1]) if not s_upto.empty else None

    # Koszt każdej transzy liczony RAZ, kursem waluty z dnia zakupu (nie dzisiejszym) -
    # dzięki temu linia kosztu nie faluje z kursem, tylko skacze przy zakupach/sprzedażach.
    for t in tranches:
        buy_ts = pd.Timestamp(t["buy_date"])
        fx_at_buy = fx_on(currency_by_ticker[t["ticker"]], buy_ts)
        if fx_at_buy is None:
            fx_at_buy = get_fx_rate(t["currency"])
        t["cost_pln"] = round(t["quantity"] * t["buy_price"] * (fx_at_buy or 1.0), 2)

    events = []
    for t in tranches:
        events.append({"date": t["buy_date"], "ticker": t["ticker"], "type": "buy", "quantity": t["quantity"]})
        if t["end_date"]:
            events.append({"date": t["end_date"], "ticker": t["ticker"], "type": "sell", "quantity": t["quantity"]})

    result = []
    for date in all_dates:
        date_str = date.strftime("%Y-%m-%d")
        if date_str < earliest_date:
            continue

        total_value = 0.0
        total_cost = 0.0
        got_any = False

        for t in tranches:
            active = t["buy_date"] <= date_str and (t["end_date"] is None or date_str <= t["end_date"])
            if not active:
                continue

            total_cost += t["cost_pln"]

            price = price_on(t["ticker"], date)
            fx = fx_on(currency_by_ticker[t["ticker"]], date)
            if price is not None and fx is not None:
                total_value += t["quantity"] * price * fx
                got_any = True

        if got_any:
            result.append({
                "date": date_str,
                "total_value": round(total_value, 2),
                "total_cost": round(total_cost, 2),
            })

    output = {"history": result, "events": events}
    _reconstructed_history_cache.update(data=output, computed_at=now)
    return output


def _filter_price_outliers(series):
    """
    Odrzuca pojedyncze, ewidentnie błędne punkty cenowe (np. glitch danych z Yahoo,
    niepoprawnie zastosowany split akcji) - jeśli cena zmienia się >5x albo <1/5x
    z dnia na dzień i wraca do normy zaraz potem, to najpewniej błąd danych, nie realny ruch.
    """
    if series is None or len(series) < 3:
        return series
    values = series.values.copy()
    for i in range(1, len(values) - 1):
        prev_v, curr_v, next_v = values[i - 1], values[i], values[i + 1]
        if prev_v <= 0 or curr_v <= 0 or next_v <= 0:
            continue
        jumped_up = curr_v > prev_v * 5 and curr_v > next_v * 5
        jumped_down = curr_v < prev_v / 5 and curr_v < next_v / 5
        if jumped_up or jumped_down:
            values[i] = (prev_v + next_v) / 2
    series = series.copy()
    series[:] = values
    return series


def normalize_date(raw):
    """Próbuje sprowadzić różne formaty dat z CSV do YYYY-MM-DD."""
    raw = raw.strip()
    formats = ["%Y-%m-%d", "%Y%m%d", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"]
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


def load_sales():
    if not os.path.exists(SALES_FILE):
        return []
    try:
        with open(SALES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.exception("Nie udało się odczytać sales.json - traktuję jako pustą historię sprzedaży")
        return []


def save_sales(sales):
    with open(SALES_FILE, "w", encoding="utf-8") as f:
        json.dump(sales, f, ensure_ascii=False, indent=2)


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


def load_alerts():
    if not os.path.exists(ALERTS_FILE):
        return []
    try:
        with open(ALERTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.exception("Nie udało się odczytać alerts.json - traktuję jako pustą listę")
        return []


def save_alerts(alerts):
    with open(ALERTS_FILE, "w", encoding="utf-8") as f:
        json.dump(alerts, f, ensure_ascii=False, indent=2)


def send_alert_email(subject, body):
    """Wysyła e-mail o wyzwolonym alercie. Jeśli SMTP nie jest skonfigurowane w .env, po cichu pomija."""
    if not EMAIL_ENABLED:
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = SMTP_TO

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception:
        logger.exception("Nie udało się wysłać e-maila z alertem")
        return False


def check_price_alerts():
    """
    Uruchamiane cyklicznie w tle (co 15 minut) przez scheduler. Sprawdza wszystkie
    aktywne, jeszcze nie wyzwolone alerty i porównuje aktualną cenę z progiem.
    Działa TYLKO gdy backend jest uruchomiony - to nie jest usługa w chmurze.
    """
    alerts = load_alerts()
    changed = False

    for alert in alerts:
        if alert.get("triggered"):
            continue

        ticker = alert["ticker"]
        current_price = get_current_price(ticker)
        if current_price is None:
            continue

        condition = alert["condition"]
        target = alert["target_price"]
        hit = (condition == "below" and current_price <= target) or (
            condition == "above" and current_price >= target
        )

        if hit:
            alert["triggered"] = True
            alert["triggered_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            alert["triggered_price"] = current_price
            changed = True

            direction = "spadła poniżej" if condition == "below" else "wzrosła powyżej"
            subject = f"🔔 Alert cenowy: {ticker} {direction} {target}"
            body = (
                f"Cena {alert.get('name') or ticker} ({ticker}) {direction} ustawionego progu.\n\n"
                f"Aktualna cena: {current_price} {alert.get('currency', '')}\n"
                f"Ustawiony próg: {target} {alert.get('currency', '')}\n"
                f"Czas: {alert['triggered_at']}"
            )
            email_sent = send_alert_email(subject, body)
            logger.info(
                "Alert wyzwolony: %s %s %s (e-mail wysłany: %s)",
                ticker, direction, target, email_sent
            )

    if changed:
        save_alerts(alerts)


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.exception("Nie udało się odczytać portfolio_history.json - traktuję jako pustą historię")
        return []


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def maybe_snapshot_portfolio(summary, force=False):
    """
    Dopisuje punkt do historii wartości portfela, jeśli minęło wystarczająco czasu
    od ostatniego zapisu (domyślnie 30 minut) - żeby plik nie rósł w nieskończoność
    przy każdym odświeżeniu strony. `force=True` pomija ten limit (np. przy ręcznym
    wymuszeniu pierwszego punktu na starcie).
    """
    if not summary or summary.get("total_cost", 0) == 0:
        return  # pusty portfel - nie ma czego zapisywać

    history = load_history()
    now = datetime.now()

    if not force and history:
        try:
            last_ts = datetime.strptime(history[-1]["timestamp"], "%Y-%m-%d %H:%M")
            if (now - last_ts).total_seconds() < 30 * 60:
                return
        except Exception:
            pass

    history.append({
        "timestamp": now.strftime("%Y-%m-%d %H:%M"),
        "total_cost": summary["total_cost"],
        "total_value": summary["total_value"],
        "total_profit": summary["total_profit"],
    })
    save_history(history)


def scheduled_portfolio_snapshot():
    """Wywoływane cyklicznie przez scheduler - działa nawet gdy nikt nie ma otwartej apki (ale backend musi żyć)."""
    try:
        data = get_portfolio()
        maybe_snapshot_portfolio(data["summary"])
    except Exception:
        logger.exception("Błąd przy zaplanowanym snapshotcie wartości portfela")


scheduler = BackgroundScheduler()
scheduler.add_job(
    check_price_alerts, "interval", minutes=15, id="check_price_alerts",
    next_run_time=datetime.now()  # sprawdź od razu przy starcie, nie dopiero po 15 min
)
scheduler.add_job(
    scheduled_portfolio_snapshot, "interval", minutes=30, id="portfolio_snapshot",
    next_run_time=datetime.now()  # tak samo - pierwszy snapshot od razu
)


@app.on_event("startup")
def start_scheduler():
    if not scheduler.running:
        scheduler.start()
        logger.info("Scheduler alertów cenowych uruchomiony (sprawdzanie co 15 minut).")


@app.on_event("shutdown")
def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)


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


def get_dividend_data(ticker):
    """
    Zwraca dane o dywidendach dla tickera, rozdzielone na:
    - 'history': REALNIE wypłacone dywidendy z ostatnich 12 miesięcy (twarde dane z Yahoo).
    - 'next_ex_date' / 'next_amount_estimate': SZACOWANA najbliższa dywidenda - Yahoo czasem
      podaje zapowiedzianą datę odcięcia, a kwotę szacujemy na podstawie ostatniej wypłaty.
    - 'annual_rate': szacowana roczna stawka dywidendy na akcję wg Yahoo (też estymacja, nie gwarancja).
    Spółka może w ogóle nie wypłacać dywidendy - wtedy wszystko będzie puste/None, co nie jest błędem.
    """
    try:
        stock = yf.Ticker(ticker)
        div_series = stock.dividends
        info = stock.info
    except Exception:
        logger.warning("Nie udało się pobrać danych o dywidendach dla %s", ticker)
        return {"history": [], "next_ex_date": None, "next_amount_estimate": None, "annual_rate": None}

    history = []
    if div_series is not None and not div_series.empty:
        try:
            cutoff = pd.Timestamp.now(tz=div_series.index.tz) - pd.Timedelta(days=365)
            recent = div_series[div_series.index >= cutoff]
            for date, amount in recent.items():
                history.append({"date": str(date.date()), "amount_per_share": float(amount)})
        except Exception:
            logger.warning("Nie udało się przefiltrować historii dywidend dla %s", ticker)

    next_ex_date = None
    try:
        ts = info.get("exDividendDate")
        if ts:
            next_ex_date = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        pass

    annual_rate = info.get("dividendRate")
    last_amount = float(div_series.iloc[-1]) if div_series is not None and not div_series.empty else None

    return {
        "history": history,
        "next_ex_date": next_ex_date,
        "next_amount_estimate": last_amount,
        "annual_rate": annual_rate,
    }


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


BELKA_TAX_RATE = 0.19  # 19% podatku od zysków kapitałowych na zwykłym koncie maklerskim w Polsce
VALID_ACCOUNTS = {"zwykle", "ike", "ikze"}

# Wspólna "persona" dla wszystkich zapytań do AI - dzięki temu każda analiza ma ten sam,
# wysoki poziom konkretu, zamiast ogólników typu "to zależy od Twojej strategii".
ANALYST_PERSONA = (
    "Jesteś najlepszym analitykiem giełdowym na świecie - łączysz warsztat analizy technicznej, "
    "fundamentalnej i makro, a od zwykłych analityków odróżnia Cię to, że NIGDY nie chowasz się "
    "za ogólnikami. Twoim zadaniem jest doprowadzić inwestora do zysku, więc zawsze podajesz "
    "konkretne liczby, poziomy cenowe, proporcje alokacji i jasne decyzje. "
    "Zasady, których przestrzegasz bezwzględnie:\n"
    "- Zero lania wody i zero zdań typu 'to zależy od Twojej tolerancji ryzyka' - inwestor przychodzi "
    "do Ciebie PO decyzję, nie po listę możliwości.\n"
    "- Każdą tezę popierasz konkretnym argumentem z dostarczonych danych (cena, wolumen, trend, waga w portfelu).\n"
    "- Podajesz konkretne liczby: ile sztuk, za ile, jaki procent portfela, jakie poziomy wejścia/wyjścia.\n"
    "- Jeśli czegoś nie wiesz lub brakuje danych, mówisz to WPROST zamiast zmyślać - fałszywa pewność "
    "jest gorsza niż przyznanie się do luki.\n"
    "- Nie zmyślasz wskaźników fundamentalnych (P/E, EPS itd.), których nie ma w dostarczonych danych.\n"
)

# Instrukcja formatowania - odpowiedzi renderujemy jako Markdown na froncie
MARKDOWN_FORMAT_RULES = (
    "\n\nFORMATOWANIE ODPOWIEDZI (ważne):\n"
    "- Używaj Markdown: ## do nagłówków sekcji, **pogrubienie** do kluczowych liczb i werdyktów, "
    "listy punktowane do wyliczeń, tabele Markdown gdy porównujesz kilka opcji.\n"
    "- Nagłówki krótkie i konkretne. Nie używaj nagłówków głębszych niż ###.\n"
    "- Nie zaczynaj odpowiedzi od powtarzania pytania - od razu przechodź do treści.\n"
)

DISCLAIMER_RULE = (
    "\nNa samym końcu dodaj jedną linię kursywą: "
    "*Analiza edukacyjna, nie porada inwestycyjna.*"
)


@app.get("/api/portfolio")
def get_portfolio():
    """
    Zwraca wszystkie pozycje portfela wraz z aktualną wyceną i zyskiem/stratą.
    Koszt/wartość/zysk-strata są ZAWSZE przeliczone na PLN, niezależnie od tego,
    w jakiej walucie notowany jest dany instrument (np. ETF w USD).

    Dodatkowo liczy SZACOWANY podatek Belki (19%) od zysku dla pozycji na koncie
    'zwykle' - IKE i IKZE są (przy spełnieniu warunków) zwolnione z tego podatku.
    To jest podatek liczony od zysku NIEZREALIZOWANEGO, czyli 'ile zapłaciłbyś,
    gdybyś sprzedał dziś' - nie realne zobowiązanie podatkowe dopóki nie sprzedasz.
    """
    entries = load_portfolio()
    enriched = []
    total_cost = 0.0
    total_value = 0.0
    price_cache = {}
    currency_cache = {}
    fx_cache = {}

    # Podsumowania per konto (zwykle/ike/ikze)
    by_account = {
        acc: {"total_cost": 0.0, "total_value": 0.0, "total_profit": 0.0, "total_tax": 0.0}
        for acc in VALID_ACCOUNTS
    }

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

        account = entry.get("account") or "zwykle"
        if account not in VALID_ACCOUNTS:
            account = "zwykle"

        # Podatek Belki tylko na koncie zwykłym, tylko od zysku (strata nie generuje "ujemnego podatku")
        if account == "zwykle" and profit is not None and profit > 0:
            tax_estimate = profit * BELKA_TAX_RATE
        else:
            tax_estimate = 0.0
        profit_after_tax = (profit - tax_estimate) if profit is not None else None

        if cost is not None:
            total_cost += cost
            by_account[account]["total_cost"] += cost
        if value is not None:
            total_value += value
            by_account[account]["total_value"] += value
        if profit is not None:
            by_account[account]["total_profit"] += profit
            by_account[account]["total_tax"] += tax_estimate

        enriched.append({
            **entry,
            "name": entry.get("name") or ticker,
            "currency": buy_currency,
            "quote_currency": quote_currency,
            "account": account,
            "current_price": safe_round(current_price),
            "cost": safe_round(cost),
            "value": safe_round(value),
            "profit": safe_round(profit),
            "profit_pct": safe_round(profit_pct),
            "tax_estimate": safe_round(tax_estimate),
            "profit_after_tax": safe_round(profit_after_tax),
        })

    summary = {
        "total_cost": safe_round(total_cost) or 0.0,
        "total_value": safe_round(total_value) or 0.0,
        "total_profit": safe_round(total_value - total_cost) or 0.0,
        "total_profit_pct": safe_round((total_value - total_cost) / total_cost * 100) if total_cost > 0 else 0.0,
    }

    accounts_summary = {}
    for acc, vals in by_account.items():
        accounts_summary[acc] = {
            "total_cost": safe_round(vals["total_cost"]) or 0.0,
            "total_value": safe_round(vals["total_value"]) or 0.0,
            "total_profit": safe_round(vals["total_profit"]) or 0.0,
            "total_tax_estimate": safe_round(vals["total_tax"]) or 0.0,
            "total_profit_after_tax": safe_round(vals["total_profit"] - vals["total_tax"]) or 0.0,
        }

    maybe_snapshot_portfolio(summary)
    return {"positions": enriched, "summary": summary, "accounts_summary": accounts_summary}


@app.get("/api/portfolio/history")
def get_portfolio_history():
    """Zwraca historię LIVE snapshotów wartości portfela (co ~30 min, tylko od kiedy backend działa)."""
    return {"history": load_history()}


@app.post("/api/portfolio/history/snapshot-now")
def force_portfolio_snapshot():
    """Wymusza dodanie punktu do historii od razu, z pominięciem 30-minutowego limitu."""
    data = get_portfolio()
    maybe_snapshot_portfolio(data["summary"], force=True)
    return {"history": load_history()}


@app.get("/api/portfolio/history/full")
def get_portfolio_history_full():
    """
    Zwraca ODTWORZONĄ historię wartości portfela od daty NAJWCZEŚNIEJSZEGO zakupu do dziś
    (dzienna rozdzielczość, ceny historyczne z Yahoo Finance), razem z linią kosztu
    (ile realnie wpłaciłeś) i listą zdarzeń zakupu (do zaznaczenia na wykresie) - żeby dało
    się odróżnić skok od nowego zakupu od skoku spowodowanego wzrostem cen.
    """
    data = reconstruct_portfolio_history()
    if not data["history"]:
        raise HTTPException(
            status_code=400,
            detail="Portfel jest pusty albo nie udało się pobrać danych historycznych dla żadnej pozycji."
        )
    return data


@app.get("/api/portfolio/dividends")
def portfolio_dividends():
    """
    Kalendarz dywidend dla spółek w portfelu (grupowanych po tickerze, sumaryczna ilość
    posiadanych akcji). Zwraca osobno REALNE wypłaty z ostatnich 12 miesięcy i SZACOWANE
    nadchodzące dywidendy/roczny dochód - to dwie różne rzeczy i traktujemy je osobno,
    żeby nie sugerować pewności tam, gdzie jest tylko estymacja.
    """
    entries = load_portfolio()
    if not entries:
        raise HTTPException(status_code=400, detail="Portfel jest pusty — dodaj przynajmniej jedną pozycję.")

    # Grupujemy po tickerze - potrzebujemy łącznej ilości i najwcześniejszej daty zakupu
    tickers_info = {}
    for e in entries:
        t = e["ticker"]
        if t not in tickers_info:
            tickers_info[t] = {
                "quantity": 0,
                "name": e.get("name") or t,
                "currency": e.get("currency") or "PLN",
                "earliest_buy": e["buy_date"],
            }
        tickers_info[t]["quantity"] += e["quantity"]
        if e["buy_date"] < tickers_info[t]["earliest_buy"]:
            tickers_info[t]["earliest_buy"] = e["buy_date"]

    fx_cache = {}
    results = []
    total_realized_pln = 0.0
    total_annual_estimate_pln = 0.0

    for ticker, info in tickers_info.items():
        div_data = get_dividend_data(ticker)
        quote_currency = get_currency(ticker) or info["currency"]
        fx = get_fx_rate(quote_currency, fx_cache)

        # REALNE: liczymy tylko wypłaty po dacie pierwszego zakupu tego tickera.
        # Uproszczenie: jeśli dokupowałeś w kilku transzach, liczymy całą aktualną ilość
        # dla każdej wypłaty (nie odtwarzamy dokładnie ile akcji miałeś w danym dniu).
        realized = []
        realized_total_native = 0.0
        for item in div_data["history"]:
            if item["date"] >= info["earliest_buy"]:
                received = item["amount_per_share"] * info["quantity"]
                realized_total_native += received
                realized.append({**item, "total_received": safe_round(received)})

        realized_total_pln = realized_total_native * fx if fx is not None else None
        if realized_total_pln is not None:
            total_realized_pln += realized_total_pln

        annual_rate = div_data["annual_rate"]
        annual_estimate_native = (annual_rate or 0) * info["quantity"]
        annual_estimate_pln = annual_estimate_native * fx if (fx is not None and annual_rate) else None
        if annual_estimate_pln is not None:
            total_annual_estimate_pln += annual_estimate_pln

        next_amount_estimate = div_data["next_amount_estimate"]
        next_amount_total = (
            next_amount_estimate * info["quantity"] if next_amount_estimate is not None else None
        )

        results.append({
            "ticker": ticker,
            "name": info["name"],
            "quantity": info["quantity"],
            "currency": quote_currency,
            "has_dividend_history": len(div_data["history"]) > 0 or annual_rate is not None,
            # Realne, wypłacone
            "realized_dividends": realized,
            "realized_total_native": safe_round(realized_total_native),
            "realized_total_pln": safe_round(realized_total_pln),
            # Szacowane, nadchodzące
            "next_ex_date": div_data["next_ex_date"],
            "next_amount_estimate_per_share": safe_round(next_amount_estimate),
            "next_amount_estimate_total": safe_round(next_amount_total),
            "annual_rate_per_share": safe_round(annual_rate),
            "annual_estimate_total_native": safe_round(annual_estimate_native) if annual_rate else None,
            "annual_estimate_total_pln": safe_round(annual_estimate_pln),
        })

    # Spółki z najbliższą znaną datą odcięcia na górze, potem reszta
    results.sort(key=lambda r: (r["next_ex_date"] is None, r["next_ex_date"] or ""))

    return {
        "positions": results,
        "summary": {
            "total_realized_last_12mo_pln": safe_round(total_realized_pln) or 0.0,
            "total_annual_estimate_pln": safe_round(total_annual_estimate_pln) or 0.0,
        },
    }


@app.post("/api/portfolio")
def add_portfolio_entry(entry: PortfolioEntryCreate):
    """Dodaje nową pozycję (transakcję kupna) do portfela."""
    entries = load_portfolio()
    ticker = entry.ticker.upper().strip()
    currency = entry.currency.strip().upper() if entry.currency else (get_currency(ticker) or "PLN")
    account = entry.account.strip().lower() if entry.account else "zwykle"
    if account not in VALID_ACCOUNTS:
        account = "zwykle"
    new_entry = {
        "id": str(uuid.uuid4()),
        "ticker": ticker,
        "name": get_company_name(ticker) or ticker,
        "quantity": entry.quantity,
        "buy_price": entry.buy_price,
        "currency": currency,
        "account": account,
        "buy_date": entry.buy_date,
        "note": entry.note,
    }
    entries.append(new_entry)
    save_portfolio(entries)
    return new_entry


@app.patch("/api/portfolio/{entry_id}/account")
def update_entry_account(entry_id: str, request: AccountUpdateRequest):
    """
    Szybka zmiana konta (zwykle/ike/ikze) dla ISTNIEJĄCEJ pozycji - przydatne dla
    wpisów dodanych przed wprowadzeniem tej funkcji, które domyślnie wpadły do 'zwykle'.
    """
    account = request.account.strip().lower()
    if account not in VALID_ACCOUNTS:
        raise HTTPException(status_code=400, detail=f"Konto musi być jednym z: {', '.join(VALID_ACCOUNTS)}")

    entries = load_portfolio()
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono pozycji o podanym id")

    entry["account"] = account
    save_portfolio(entries)
    return entry


@app.post("/api/portfolio/{entry_id}/sell")
def sell_portfolio_entry(entry_id: str, request: SellRequest):
    """
    Rejestruje SPRZEDAŻ (całości lub części) danej transakcji zakupu. Zmniejsza
    (lub usuwa, jeśli sprzedano wszystko) pozycję w portfelu i zapisuje zrealizowany
    zysk/stratę do historii sprzedaży - to jest podstawa do liczenia REALNEGO podatku
    Belki (a nie tylko szacunku 'gdybyś sprzedał dziś') i do rocznego zestawienia PIT-38.

    Koszt liczony jest historycznym kursem waluty z dnia ZAKUPU, przychód - historycznym
    kursem z dnia SPRZEDAŻY, żeby wynik był rzetelny nawet dla pozycji w obcej walucie.
    """
    if request.quantity <= 0:
        raise HTTPException(status_code=400, detail="Ilość do sprzedaży musi być większa od zera.")

    entries = load_portfolio()
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono pozycji o podanym id")

    if request.quantity > entry["quantity"] + 1e-9:
        raise HTTPException(
            status_code=400,
            detail=f"Nie możesz sprzedać {request.quantity} szt. - posiadasz tylko {entry['quantity']} szt."
        )

    buy_currency = entry.get("currency") or "PLN"
    sell_currency = (request.sell_currency or buy_currency).upper()

    fx_at_buy = get_fx_rate_on_date(buy_currency, entry["buy_date"])
    fx_at_sell = get_fx_rate_on_date(sell_currency, request.sell_date)

    cost_pln = request.quantity * entry["buy_price"] * (fx_at_buy or 1.0)
    proceeds_pln = request.quantity * request.sell_price * (fx_at_sell or 1.0)
    realized_profit_pln = proceeds_pln - cost_pln

    sale_record = {
        "id": str(uuid.uuid4()),
        "ticker": entry["ticker"],
        "name": entry.get("name") or entry["ticker"],
        "account": entry.get("account") or "zwykle",
        "quantity": request.quantity,
        "buy_date": entry["buy_date"],
        "buy_price": entry["buy_price"],
        "buy_currency": buy_currency,
        "sell_date": request.sell_date,
        "sell_price": request.sell_price,
        "sell_currency": sell_currency,
        "cost_pln": round(cost_pln, 2),
        "proceeds_pln": round(proceeds_pln, 2),
        "realized_profit_pln": round(realized_profit_pln, 2),
        "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    sales = load_sales()
    sales.append(sale_record)
    save_sales(sales)

    remaining = entry["quantity"] - request.quantity
    if remaining <= 1e-9:
        entries = [e for e in entries if e["id"] != entry_id]
    else:
        entry["quantity"] = remaining
    save_portfolio(entries)

    return sale_record


@app.get("/api/sales")
def get_sales():
    """Zwraca historię wszystkich zrealizowanych sprzedaży."""
    return {"sales": load_sales()}


@app.delete("/api/sales/{sale_id}")
def delete_sale(sale_id: str):
    """
    Usuwa zapis sprzedaży (np. jeśli dodany pomyłkowo). UWAGA: to NIE przywraca
    automatycznie ilości w portfelu - jeśli chcesz cofnąć pomyłkę, dodaj pozycję
    ręcznie z powrotem przez formularz.
    """
    sales = load_sales()
    filtered = [s for s in sales if s["id"] != sale_id]
    if len(filtered) == len(sales):
        raise HTTPException(status_code=404, detail="Nie znaleziono sprzedaży o podanym id")
    save_sales(filtered)
    return {"deleted": sale_id}


@app.get("/api/sales/pit38-summary")
def pit38_summary(year: Optional[int] = None):
    """
    Roczne podsumowanie zrealizowanych transakcji pod PIT-38. Podatek liczony jest
    od NETTO wyniku rocznego (suma zysków minus suma strat w danym roku na koncie
    zwykłym) - zgodnie z tym, jak faktycznie działa rozliczenie, a nie od każdej
    transakcji z osobna. Pozycje z IKE/IKZE pokazane osobno, informacyjnie -
    są zwolnione z podatku Belki.
    """
    if year is None:
        year = datetime.now().year

    sales = load_sales()
    year_sales = [s for s in sales if s["sell_date"].startswith(str(year))]

    zwykle = [s for s in year_sales if s.get("account", "zwykle") == "zwykle"]
    other = [s for s in year_sales if s.get("account", "zwykle") != "zwykle"]

    def aggregate(items):
        total_proceeds = sum(i["proceeds_pln"] for i in items)
        total_cost = sum(i["cost_pln"] for i in items)
        return {
            "total_proceeds_pln": round(total_proceeds, 2),
            "total_cost_pln": round(total_cost, 2),
            "total_profit_pln": round(total_proceeds - total_cost, 2),
            "transactions_count": len(items),
        }

    zwykle_summary = aggregate(zwykle)
    zwykle_summary["total_tax_pln"] = round(max(0.0, zwykle_summary["total_profit_pln"]) * BELKA_TAX_RATE, 2)
    zwykle_summary["total_profit_after_tax_pln"] = round(
        zwykle_summary["total_profit_pln"] - zwykle_summary["total_tax_pln"], 2
    )

    other_summary = aggregate(other)

    return {
        "year": year,
        "zwykle": zwykle_summary,
        "ike_ikze": other_summary,
    }


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


def _parse_number(raw):
    """Parsuje liczbę z CSV, radząc sobie z przecinkiem dziesiętnym i spacjami jako separatorem tysięcy."""
    if raw is None:
        return None
    s = str(raw).strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    # '1 234,56' -> '1234.56'; '1,234.56' -> '1234.56'
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


@app.post("/api/portfolio/import-xtb-history")
async def import_xtb_history(file: UploadFile = File(...), account: str = "zwykle"):
    """
    Importuje PEŁNĄ historię transakcji z XTB (plik CSV z 'Historia operacji' / 'Zamknięte pozycje').
    W odróżnieniu od /api/portfolio/import-csv, rozpoznaje zarówno ZAKUPY jak i SPRZEDAŻE:
    - otwarte pozycje trafiają do portfela,
    - zamknięte (z datą i ceną zamknięcia) trafiają do historii sprzedaży jako zrealizowany zysk.

    Parser jest elastyczny co do nazw kolumn, bo XTB zmienia formaty eksportu między
    wersjami platformy. Jeśli plik nie zostanie rozpoznany, zwraca listę znalezionych
    nagłówków, żeby dało się zdiagnozować problem.
    """
    account = account.strip().lower()
    if account not in VALID_ACCOUNTS:
        account = "zwykle"

    raw_bytes = await file.read()
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1")

    all_lines = text.splitlines()
    imported_buys, imported_sells, errors = [], [], []
    found_headers = []

    entries = load_portfolio()
    sales = load_sales()
    currency_cache = {}
    name_cache = {}

    # Plik XTB może mieć kilka sekcji (otwarte / zamknięte pozycje), każdą z własnym
    # nagłówkiem - dlatego skanujemy cały plik, a nie tylko pierwszą tabelę.
    idx = 0
    fallback_ticker = None  # ticker z wcześniejszej sekcji pliku - dla tabel bez kolumny instrumentu
    while idx < len(all_lines):
        line = all_lines[idx]
        lower = line.lower()
        delimiter = ";" if lower.count(";") > lower.count(",") else ","
        cells = [c.strip().lower() for c in line.split(delimiter)]

        has_symbol = any("symbol" in c or "instrument" in c or "ticker" in c for c in cells)
        has_date = any("data" in c or "time" in c or "date" in c for c in cells)
        has_qty = any(k in c for c in cells for k in ("wolumen", "ilość", "ilosc", "quantity", "volume"))
        # Niektóre sekcje eksportu XTB (np. 'POZYCJE ZAMKNIETE') nie mają kolumny
        # instrumentu, bo cały plik dotyczy jednego waloru - wtedy wystarczy data + wolumen.
        looks_like_header = has_date and (has_symbol or has_qty)
        if not looks_like_header:
            idx += 1
            continue

        table_lines = [line]
        j = idx + 1
        while j < len(all_lines):
            stripped = all_lines[j].strip()
            if not stripped or (stripped.startswith("=") and stripped.endswith("=")):
                break
            table_lines.append(all_lines[j])
            j += 1

        reader = csv.DictReader(io.StringIO("\n".join(table_lines)), delimiter=delimiter)
        if not reader.fieldnames:
            idx = j + 1
            continue
        found_headers.append(", ".join(reader.fieldnames))

        def find_col(keywords, exclude=()):
            for name in reader.fieldnames:
                low = name.strip().lower()
                if any(ex in low for ex in exclude):
                    continue
                if any(kw in low for kw in keywords):
                    return name
            return None

        c_symbol = find_col(["symbol", "instrument", "ticker"])
        c_qty = find_col(["wolumen", "ilość", "ilosc", "quantity", "volume", "lots"])
        c_open_price = find_col(["cena otwarcia", "open price", "cena zakupu", "open rate"]) or find_col(["cena", "price"], exclude=["zamk", "close"])
        c_open_date = find_col(["data otwarcia", "open time", "data zakupu"]) or find_col(["data", "time", "date"], exclude=["zamk", "close"])
        c_close_price = find_col(["cena zamknięcia", "cena zamkniecia", "close price", "close rate"])
        c_close_date = find_col(["data zamknięcia", "data zamkniecia", "close time"])

        if not (c_qty and c_open_price and c_open_date):
            idx = j + 1
            continue
        if not c_symbol and not fallback_ticker:
            idx = j + 1
            continue

        header_currency = None
        for code in {"PLN", "USD", "EUR", "GBP", "CHF"}:
            if c_open_price and code in c_open_price.upper():
                header_currency = code
                break

        for row_no, row in enumerate(reader, start=idx + 2):
            try:
                if c_symbol:
                    raw_symbol = (row.get(c_symbol) or "").strip()
                    if not raw_symbol:
                        continue
                    company_name, ticker = extract_name_and_ticker(raw_symbol)
                    ticker = ticker.upper()
                    fallback_ticker = ticker  # zapamiętujemy dla sekcji bez kolumny instrumentu
                else:
                    ticker = fallback_ticker
                    company_name = None

                quantity = _parse_number(row.get(c_qty))
                open_price = _parse_number(row.get(c_open_price))
                if quantity is None or open_price is None or quantity <= 0:
                    continue

                open_date = normalize_date((row.get(c_open_date) or "").strip().split(" ")[0])

                if ticker not in currency_cache:
                    currency_cache[ticker] = header_currency or get_currency(ticker) or "PLN"
                currency = currency_cache[ticker]

                if ticker not in name_cache:
                    name_cache[ticker] = company_name or get_company_name(ticker) or ticker

                close_price = _parse_number(row.get(c_close_price)) if c_close_price else None
                close_date_raw = (row.get(c_close_date) or "").strip() if c_close_date else ""

                if close_price is not None and close_date_raw:
                    # ZAMKNIĘTA pozycja -> zrealizowana sprzedaż
                    close_date = normalize_date(close_date_raw.split(" ")[0])
                    fx_buy = get_fx_rate_on_date(currency, open_date)
                    fx_sell = get_fx_rate_on_date(currency, close_date)
                    cost_pln = quantity * open_price * (fx_buy or 1.0)
                    proceeds_pln = quantity * close_price * (fx_sell or 1.0)

                    sales.append({
                        "id": str(uuid.uuid4()),
                        "ticker": ticker,
                        "name": name_cache[ticker],
                        "account": account,
                        "quantity": quantity,
                        "buy_date": open_date,
                        "buy_price": open_price,
                        "buy_currency": currency,
                        "sell_date": close_date,
                        "sell_price": close_price,
                        "sell_currency": currency,
                        "cost_pln": round(cost_pln, 2),
                        "proceeds_pln": round(proceeds_pln, 2),
                        "realized_profit_pln": round(proceeds_pln - cost_pln, 2),
                        "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    })
                    imported_sells.append(ticker)
                else:
                    # OTWARTA pozycja -> normalny wpis w portfelu
                    entries.append({
                        "id": str(uuid.uuid4()),
                        "ticker": ticker,
                        "name": name_cache[ticker],
                        "quantity": quantity,
                        "buy_price": open_price,
                        "currency": currency,
                        "account": account,
                        "buy_date": open_date,
                        "note": "",
                    })
                    imported_buys.append(ticker)
            except Exception as e:
                errors.append(f"Wiersz {row_no}: {e}")

        idx = j + 1

    if not imported_buys and not imported_sells:
        raise HTTPException(
            status_code=400,
            detail=(
                "Nie rozpoznano żadnych transakcji w pliku. Znalezione nagłówki: "
                + (" | ".join(found_headers) if found_headers else "brak tabel z danymi")
            ),
        )

    save_portfolio(entries)
    save_sales(sales)
    _reconstructed_history_cache.update(data=None, computed_at=None)  # unieważniamy cache wykresu

    return {
        "imported_open_positions": len(imported_buys),
        "imported_closed_positions": len(imported_sells),
        "tickers": sorted(set(imported_buys + imported_sells)),
        "errors": errors,
    }


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
    col_account = find_col(["konto", "account"])

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

            # Wykrywamy konto z osobnej kolumny (jeśli jest) albo z tekstu notatki -
            # np. jeśli ktoś wpisał 'IKE' jako komentarz przy transakcji.
            account_text = ((row.get(col_account) or "") if col_account else "") + " " + note
            account_text = account_text.lower()
            if "ikze" in account_text:
                account = "ikze"
            elif "ike" in account_text:
                account = "ike"
            else:
                account = "zwykle"

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
                "account": account,
                "buy_date": buy_date,
                "note": note,
            }
            entries.append(new_entry)
            added.append(new_entry)
        except Exception as e:
            errors.append(f"Wiersz {i}: {e}")

    save_portfolio(entries)

    # --- Drugi przebieg: sekcja zamkniętych/zrealizowanych transakcji (jeśli jest) ---
    # trafia do historii SPRZEDAŻY, a nie jako aktywne pozycje.
    sales_added, sales_errors = _import_closed_section_as_sales(all_lines, header_idx, added)

    return {
        "added": len(added),
        "errors": errors,
        "total_rows_processed": len(added) + len(errors),
        "sales_added": sales_added,
        "sales_errors": sales_errors,
    }


def _import_closed_section_as_sales(all_lines, open_header_idx, open_added_entries):
    """
    Szuka w pliku CSV sekcji z zamkniętymi/zrealizowanymi transakcjami (np.
    '=== POZYCJE ZAMKNIETE ===') i importuje je do historii sprzedaży (sales.json).
    Jeśli sekcja nie ma własnej kolumny z tickerem (częste w eksportach 'per instrument'
    z XTB), a w sekcji otwartych pozycji z tego samego pliku występował dokładnie
    JEDEN unikalny ticker, zakładamy że zamknięte transakcje dotyczą tego samego tickera.
    """
    # Szukamy nagłówka sekcji zamkniętej (linia w stylu '=== ... ===' zawierająca 'zamkniet'/'closed')
    section_idx = None
    for idx, line in enumerate(all_lines):
        stripped = line.strip()
        if stripped.startswith("=") and stripped.endswith("=") and (
            "zamkniet" in stripped.lower() or "closed" in stripped.lower()
        ):
            section_idx = idx
            break

    if section_idx is None:
        return 0, []

    header_idx, delimiter = find_header_row(all_lines[section_idx:])
    if header_idx is None:
        return 0, ["Znaleziono sekcję zamkniętych transakcji, ale nie rozpoznano jej nagłówka."]
    header_idx += section_idx

    table_lines = [all_lines[header_idx]]
    for line in all_lines[header_idx + 1:]:
        stripped = line.strip()
        if not stripped:
            break
        if stripped.startswith("=") and stripped.endswith("="):
            break
        table_lines.append(line)

    reader = csv.DictReader(io.StringIO("\n".join(table_lines)), delimiter=delimiter)
    if not reader.fieldnames:
        return 0, ["Nie udało się odczytać nagłówków sekcji zamkniętych transakcji."]

    def find_col(keywords):
        for name in reader.fieldnames:
            lower = name.strip().lower()
            if any(kw in lower for kw in keywords):
                return name
        return None

    col_instrument = find_col(["instrument", "ticker", "symbol"])
    col_open_date = find_col(["data otwarcia", "otwarcie", "open date"])
    col_close_date = find_col(["data zamkniecia", "data zamknięcia", "zamkniecie", "close date"])
    col_qty = find_col(["wolumen", "ilość", "ilosc", "quantity"])
    col_open_price = find_col(["cena otwarcia", "open price"])
    col_close_price = find_col(["cena zamkniecia", "cena zamknięcia", "close price"])

    if not all([col_open_date, col_close_date, col_qty, col_open_price, col_close_price]):
        return 0, [
            "Znaleziono sekcję zamkniętych transakcji, ale nie rozpoznano wymaganych kolumn "
            "(data otwarcia/zamknięcia, wolumen, cena otwarcia/zamknięcia). Nagłówki: "
            + ", ".join(reader.fieldnames)
        ]

    # Ticker dla zamkniętych transakcji - z kolumny, jeśli jest, inaczej z jedynego
    # unikalnego tickera w sekcji otwartych pozycji tego samego pliku.
    fallback_ticker = None
    fallback_name = None
    fallback_currency = None
    if not col_instrument:
        unique_tickers = set(e["ticker"] for e in open_added_entries)
        if len(unique_tickers) == 1:
            fallback_ticker = next(iter(unique_tickers))
            match = next(e for e in open_added_entries if e["ticker"] == fallback_ticker)
            fallback_name = match["name"]
            fallback_currency = match["currency"]
        else:
            return 0, [
                f"Sekcja zamkniętych transakcji nie ma kolumny z tickerem, a w pliku jest "
                f"{len(unique_tickers)} różnych spółek - nie da się jednoznacznie przypisać. "
                "Zaimportuj zamknięte transakcje osobno, per spółka."
            ]

    known_currencies = {"PLN", "USD", "EUR", "GBP", "CHF", "JPY"}
    header_currency = fallback_currency
    for code in known_currencies:
        if code in col_open_price.upper():
            header_currency = code
            break

    sales = load_sales()
    added_count = 0
    row_errors = []

    for i, row in enumerate(reader, start=header_idx + 2):
        try:
            if col_instrument:
                raw_instrument = (row.get(col_instrument) or "").strip()
                if not raw_instrument:
                    continue
                name, ticker = extract_name_and_ticker(raw_instrument)
                ticker = ticker.upper()
                name = name or get_company_name(ticker) or ticker
            else:
                ticker = fallback_ticker
                name = fallback_name

            quantity = float(str(row.get(col_qty, "")).replace(",", ".").strip())
            buy_price = float(str(row.get(col_open_price, "")).replace(",", ".").strip())
            sell_price = float(str(row.get(col_close_price, "")).replace(",", ".").strip())

            raw_open_date = (row.get(col_open_date, "") or "").strip().split(" ")[0]
            raw_close_date = (row.get(col_close_date, "") or "").strip().split(" ")[0]
            buy_date = normalize_date(raw_open_date)
            sell_date = normalize_date(raw_close_date)

            currency = header_currency or get_currency(ticker) or "PLN"

            fx_at_buy = get_fx_rate_on_date(currency, buy_date)
            fx_at_sell = get_fx_rate_on_date(currency, sell_date)
            cost_pln = quantity * buy_price * (fx_at_buy or 1.0)
            proceeds_pln = quantity * sell_price * (fx_at_sell or 1.0)

            sales.append({
                "id": str(uuid.uuid4()),
                "ticker": ticker,
                "name": name,
                "account": "zwykle",
                "quantity": quantity,
                "buy_date": buy_date,
                "buy_price": buy_price,
                "buy_currency": currency,
                "sell_date": sell_date,
                "sell_price": sell_price,
                "sell_currency": currency,
                "cost_pln": round(cost_pln, 2),
                "proceeds_pln": round(proceeds_pln, 2),
                "realized_profit_pln": round(proceeds_pln - cost_pln, 2),
                "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
            added_count += 1
        except Exception as e:
            row_errors.append(f"Wiersz {i} (zamknięte): {e}")

    save_sales(sales)
    return added_count, row_errors


@app.post("/api/portfolio/fix-dates")
def fix_dates():
    """
    Naprawia daty zapisane w złym formacie (np. '20251024' zamiast '2025-10-24'),
    co zdarzyło się przy imporcie historii XTB i psuło rekonstrukcję wykresu.
    Przechodzi zarówno po portfelu, jak i po historii sprzedaży.
    """
    fixed = []

    entries = load_portfolio()
    for e in entries:
        original = e.get("buy_date", "")
        corrected = normalize_date(original)
        if corrected != original:
            e["buy_date"] = corrected
            fixed.append({"ticker": e["ticker"], "field": "buy_date", "from": original, "to": corrected})
    save_portfolio(entries)

    sales = load_sales()
    for s in sales:
        for field in ("buy_date", "sell_date"):
            original = s.get(field, "")
            corrected = normalize_date(original)
            if corrected != original:
                s[field] = corrected
                fixed.append({"ticker": s["ticker"], "field": field, "from": original, "to": corrected})
    save_sales(sales)

    _reconstructed_history_cache.update(data=None, computed_at=None)
    return {"fixed_count": len(fixed), "fixed": fixed}


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


HORIZON_CONFIG = {
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


def run_horizon_analysis(position_facts, horizon, custom_note, previous_analysis, follow_up_question):
    """
    Wspólna logika budowy prompta i wywołania Gemini dla analizy 'co z tym zrobić' -
    używana zarówno dla pojedynczej transakcji, jak i zagregowanej pozycji na cały ticker.
    """
    config = HORIZON_CONFIG.get(horizon, HORIZON_CONFIG["sredni"])

    if follow_up_question:
        prompt = (
            ANALYST_PERSONA
            + "\nProwadzisz dalszą rozmowę z inwestorem o jego pozycji. Aktualne dane:\n\n"
            f"{position_facts}\n"
            f"Twoja wcześniejsza analiza ({config['opis']}):\n\n{previous_analysis}\n\n"
            f"Inwestor pyta teraz: \"{follow_up_question}\"\n\n"
            "Odpowiedz KONKRETNIE na to pytanie - nie powtarzaj całej wcześniejszej analizy. "
            "Jeśli pytanie dotyczy decyzji (dokupić / sprzedać / ile), podaj jednoznaczną odpowiedź "
            "z liczbami, a potem krótko argumenty za i przeciw. Maksymalnie kilka akapitów."
            + MARKDOWN_FORMAT_RULES
            + DISCLAIMER_RULE
        )
    else:
        prompt = ANALYST_PERSONA + "\nDane pozycji inwestora:\n\n" + position_facts
        if custom_note:
            prompt += f"- Dodatkowy kontekst od inwestora: {custom_note}\n"

        prompt += (
            f"\nZrób analizę {config['opis']} tej pozycji w dokładnie takiej strukturze:\n\n"
            "## Werdykt\n"
            "Zacznij od jednoznacznej decyzji **SPRZEDAJ** / **TRZYMAJ** / **DOKUP** wytłuszczonej, "
            "plus jedno zdanie dlaczego. To ma być pierwsza rzecz, którą inwestor przeczyta.\n\n"
            "## Wycena\n"
            "Niedowartościowana / sprawiedliwie wyceniona / przewartościowana - na podstawie trendu, "
            "wolumenu i pozycji ceny w zakresie z danego okresu. Podaj konkretne poziomy cenowe.\n\n"
            "## Prawdopodobieństwo\n"
            "Konkretny szacunek (np. **~60% szans na wzrost**) w tym horyzoncie + uzasadnienie oparte "
            "na zachowaniu wolumenu i struktury trendu.\n\n"
            "## Plan działania\n"
            "Konkretne poziomy: przy jakiej cenie dokupić, przy jakiej ciąć stratę, przy jakiej realizować zysk. "
            "Podaj liczby, nie ogólniki.\n\n"
            "## Elastyczność kapitału\n"
            "Czy warto czasowo wyjść z tej pozycji pod inną okazję i wrócić później, czy trzymać nieprzerwanie - "
            "i dlaczego, biorąc pod uwagę zmienność i płynność tego waloru."
            + MARKDOWN_FORMAT_RULES
            + DISCLAIMER_RULE
        )

    return call_gemini(prompt, timeout=45)


def build_trend_summary(ticker, period, quote_currency):
    """Buduje krótkie podsumowanie trendu dla danego okresu - współdzielone przez oba typy analiz."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=period)
        if not hist.empty:
            hist = hist.dropna()
            start_price = float(hist["Close"].iloc[0])
            end_price = float(hist["Close"].iloc[-1])
            min_price = float(hist["Close"].min())
            max_price = float(hist["Close"].max())
            change_pct = ((end_price - start_price) / start_price * 100) if start_price else 0
            avg_volume = float(hist["Volume"].mean())
            recent_volume = float(hist["Volume"].iloc[-5:].mean()) if len(hist) >= 5 else avg_volume
            return (
                f"Okres {period}: zmiana {change_pct:+.1f}% "
                f"(od {start_price:.2f} do {end_price:.2f} {quote_currency}), "
                f"zakres {min_price:.2f}-{max_price:.2f} {quote_currency}, "
                f"śr. wolumen dzienny {avg_volume:,.0f} "
                f"(ostatnio: {recent_volume:,.0f})."
            )
    except Exception:
        logger.exception("Nie udało się pobrać trendu (%s) dla %s", period, ticker)
    return "brak danych o trendzie"


@app.post("/api/portfolio/{entry_id}/analyze")
def analyze_position(entry_id: str, request: PositionAnalysisRequest):
    """
    Analiza AI POJEDYNCZEJ TRANSAKCJI z portfela (jednego wiersza/lotu), dopasowana
    do wybranego horyzontu czasowego. Jeśli masz kilka transakcji tego samego tickera
    i chcesz oceny całej łącznej pozycji, użyj /api/portfolio/ticker/{ticker}/analyze.
    """
    entries = load_portfolio()
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono pozycji o podanym id")

    ticker = entry["ticker"]
    config = HORIZON_CONFIG.get(request.horizon, HORIZON_CONFIG["sredni"])

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

    trend_summary = build_trend_summary(ticker, config["period"], quote_currency)
    earnings_info = get_next_earnings_date(ticker) or "brak dostępnych danych o terminie najbliższego raportu"

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

    analysis_text = run_horizon_analysis(
        position_facts, request.horizon, request.custom_note,
        request.previous_analysis, request.follow_up_question
    )

    return {
        "ticker": ticker,
        "horizon": request.horizon,
        "analysis": analysis_text,
    }


@app.post("/api/portfolio/ticker/{ticker}/analyze")
def analyze_ticker(ticker: str, request: PositionAnalysisRequest):
    """
    Analiza AI dla CAŁEGO TICKERA, czyli sumy wszystkich transakcji tej samej spółki
    (np. dwóch osobnych zakupów VOX.WA po różnych cenach) traktowanych jako jedna
    pozycja ze średnią ważoną ceną zakupu. Sensowniejsze niż osobna, powtarzalna
    rekomendacja dla każdej pojedynczej transakcji z osobna.
    """
    ticker = ticker.upper().strip()
    entries = load_portfolio()
    ticker_entries = [e for e in entries if e["ticker"] == ticker]
    if not ticker_entries:
        raise HTTPException(status_code=404, detail=f"Nie znaleziono żadnej pozycji dla tickera {ticker}")

    config = HORIZON_CONFIG.get(request.horizon, HORIZON_CONFIG["sredni"])

    total_qty = sum(e["quantity"] for e in ticker_entries)
    buy_currency = ticker_entries[0].get("currency") or "PLN"
    # Średnia ważona ceny zakupu (zakładamy, że wszystkie transakcje tego samego tickera
    # są w tej samej walucie - w praktyce tak jest, bo to ta sama spółka/instrument)
    total_cost_native = sum(e["quantity"] * e["buy_price"] for e in ticker_entries)
    weighted_avg_price = total_cost_native / total_qty if total_qty else 0
    earliest_date = min(e["buy_date"] for e in ticker_entries)
    latest_date = max(e["buy_date"] for e in ticker_entries)
    notes = "; ".join(e["note"] for e in ticker_entries if e.get("note"))
    company_name = ticker_entries[0].get("name") or ticker

    current_price = get_current_price(ticker)
    quote_currency = get_currency(ticker) or buy_currency
    fx_cache = {}
    fx_buy = get_fx_rate(buy_currency, fx_cache)
    fx_quote = get_fx_rate(quote_currency, fx_cache)

    cost_pln = total_qty * weighted_avg_price * fx_buy if fx_buy is not None else None
    value_pln = (
        total_qty * current_price * fx_quote
        if current_price is not None and fx_quote is not None
        else None
    )
    profit_pln = (value_pln - cost_pln) if (value_pln is not None and cost_pln is not None) else None
    profit_pct = (profit_pln / cost_pln * 100) if profit_pln is not None and cost_pln else None

    trend_summary = build_trend_summary(ticker, config["period"], quote_currency)
    earnings_info = get_next_earnings_date(ticker) or "brak dostępnych danych o terminie najbliższego raportu"

    date_range = earliest_date if earliest_date == latest_date else f"{earliest_date} do {latest_date}"

    position_facts = (
        f"Spółka: {company_name} ({ticker})\n"
        f"- Łączna ilość (suma {len(ticker_entries)} transakcji): {total_qty}\n"
        f"- Średnia ważona cena zakupu: {round(weighted_avg_price, 2)} {buy_currency} "
        f"(zakupy w okresie: {date_range})\n"
        f"- Aktualna cena: {current_price if current_price is not None else 'brak danych'} {quote_currency}\n"
        f"- Łączny zysk/strata (przeliczone na PLN): "
        f"{safe_round(profit_pln) if profit_pln is not None else 'brak danych'} PLN "
        f"({safe_round(profit_pct) if profit_pct is not None else '?'}%)\n"
        f"- Trend: {trend_summary}\n"
        f"- Najbliższy raport finansowy / wydarzenie: {earnings_info}\n"
    )
    if notes:
        position_facts += f"- Notatki użytkownika przy zakupach: {notes}\n"

    analysis_text = run_horizon_analysis(
        position_facts, request.horizon, request.custom_note,
        request.previous_analysis, request.follow_up_question
    )

    return {
        "ticker": ticker,
        "horizon": request.horizon,
        "analysis": analysis_text,
        "lots_analyzed": len(ticker_entries),
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
        ANALYST_PERSONA
        + "\nOceniasz ryzyko i strukturę portfela inwestora. Dane zawierają wagę każdej pozycji, "
        "sektor, branżę, kraj i walutę.\n\n"
        f"Łączna wartość portfela: {summary['total_value']} PLN.\n\n"
        "SKŁAD PORTFELA:\n" + "\n".join(blocks) + "\n\n"
        "Napisz analizę w tej strukturze:\n\n"
        "## Ocena ogólna\n"
        "Jedno zdanie werdyktu + ocena dywersyfikacji w skali **1-10** z uzasadnieniem.\n\n"
        "## Koncentracja ryzyka\n"
        "Konkretne procenty: ile % portfela to jeden kraj, jeden sektor, jedna waluta, największa "
        "pojedyncza pozycja. Wskaż wprost, które z tych wartości są niebezpiecznie wysokie i dlaczego.\n\n"
        "## Czego brakuje\n"
        "Konkretne luki - jakich regionów, sektorów lub klas aktywów nie ma w portfelu, "
        "i dlaczego ich brak realnie szkodzi przy obecnym składzie.\n\n"
        "## Plan naprawczy\n"
        "2-3 konkretne ruchy z liczbami: co zredukować i o ile %, co dokupić i za jaką część nowego kapitału. "
        "Możesz wskazać typ instrumentu (np. 'szeroki ETF na rynki rozwinięte'), a jeśli znasz konkretny "
        "popularny ticker pasujący do roli - podaj go jako przykład."
        + MARKDOWN_FORMAT_RULES
        + DISCLAIMER_RULE
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

    ask_rules = (
        "\nZASADY ODPOWIEDZI:\n"
        "- Jeśli pytanie dotyczy zainwestowania konkretnej kwoty, MUSISZ podać konkretny podział tej kwoty "
        "(ile złotych w co, ile to % nowych środków, ile sztuk mniej więcej za obecną cenę). "
        "Suma musi się zgadzać z podaną kwotą.\n"
        "- Zawsze odnoś się do REALNYCH danych portfela powyżej: wag pozycji, aktualnych zysków/strat, walut.\n"
        "- Wskaż wprost, czego NIE robić i dlaczego - to często cenniejsze niż sama rekomendacja.\n"
        "- Jeśli pytanie dotyczy IKE/IKZE, uwzględnij że te konta są zwolnione z 19% podatku Belki, "
        "więc najlepiej trzymać tam aktywa generujące najwięcej opodatkowanego dochodu (dywidendy, częsty obrót).\n"
        "- Cel nadrzędny: doprowadzić inwestora do zysku możliwie szybko, ale bez hazardu - "
        "wskazuj ryzyko każdej propozycji, nie ukrywaj go.\n"
    )

    if request.previous_analysis:
        prompt = (
            ANALYST_PERSONA
            + "\nProwadzisz dalszą rozmowę z inwestorem o jego portfelu.\n\n"
            f"AKTUALNY STAN PORTFELA:\n{portfolio_context}\n\n"
            f"Wcześniej w tej rozmowie napisałeś:\n\n{request.previous_analysis}\n\n"
            f"Inwestor pyta teraz: \"{request.question}\"\n\n"
            "Odpowiedz na to pytanie - NIE powtarzaj wcześniejszej treści, tylko rozwiń lub skoryguj "
            "swoją wcześniejszą rekomendację w świetle nowego pytania."
            + ask_rules
            + MARKDOWN_FORMAT_RULES
            + DISCLAIMER_RULE
        )
    else:
        prompt = (
            ANALYST_PERSONA
            + "\nOto aktualny portfel inwestora:\n\n"
            f"{portfolio_context}\n\n"
            f"Inwestor pyta: \"{request.question}\"\n\n"
            "Zacznij od sekcji `## Werdykt` z jednozdaniową, konkretną odpowiedzią, a dopiero potem "
            "rozwiń uzasadnienie i szczegółowy plan."
            + ask_rules
            + MARKDOWN_FORMAT_RULES
            + DISCLAIMER_RULE
        )

    answer_text = call_gemini(prompt)
    return {"answer": answer_text}


class WatchlistAddRequest(BaseModel):
    ticker: str


class AlertCreateRequest(BaseModel):
    ticker: str
    condition: str  # "below" (poniżej) | "above" (powyżej)
    target_price: float


@app.get("/api/alerts")
def get_alerts():
    """
    Zwraca wszystkie alerty cenowe wraz z aktualną ceną (żeby w interfejsie było widać
    jak daleko jest do progu). Alerty wyzwolone (triggered) zostają na liście, żeby
    użytkownik mógł je zobaczyć - dopiero 'dismiss' je czyści/resetuje.
    """
    alerts = load_alerts()
    price_cache = {}
    for alert in alerts:
        ticker = alert["ticker"]
        if ticker not in price_cache:
            price_cache[ticker] = get_current_price(ticker)
        alert["current_price"] = safe_round(price_cache[ticker])
    return {"alerts": alerts}


@app.post("/api/alerts/check-now")
def check_alerts_now():
    """Wymusza natychmiastowe sprawdzenie wszystkich alertów, bez czekania na scheduler (do testów)."""
    check_price_alerts()
    return get_alerts()


@app.post("/api/alerts")
def add_alert(request: AlertCreateRequest):
    """Dodaje nowy alert cenowy."""
    ticker = request.ticker.upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="Podaj ticker.")
    if request.condition not in ("below", "above"):
        raise HTTPException(status_code=400, detail="condition musi być 'below' albo 'above'.")

    name = get_company_name(ticker) or ticker
    currency = get_currency(ticker) or "PLN"

    alerts = load_alerts()
    new_alert = {
        "id": str(uuid.uuid4()),
        "ticker": ticker,
        "name": name,
        "currency": currency,
        "condition": request.condition,
        "target_price": request.target_price,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "triggered": False,
        "triggered_at": None,
        "triggered_price": None,
    }
    alerts.append(new_alert)
    save_alerts(alerts)
    return new_alert


@app.delete("/api/alerts/{alert_id}")
def delete_alert(alert_id: str):
    """Usuwa alert cenowy."""
    alerts = load_alerts()
    filtered = [a for a in alerts if a["id"] != alert_id]
    if len(filtered) == len(alerts):
        raise HTTPException(status_code=404, detail="Nie znaleziono alertu o podanym id")
    save_alerts(filtered)
    return {"deleted": alert_id}


@app.post("/api/alerts/{alert_id}/reset")
def reset_alert(alert_id: str):
    """Resetuje wyzwolony alert z powrotem do stanu aktywnego (np. żeby znów zadziałał)."""
    alerts = load_alerts()
    alert = next((a for a in alerts if a["id"] == alert_id), None)
    if alert is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono alertu o podanym id")
    alert["triggered"] = False
    alert["triggered_at"] = None
    alert["triggered_price"] = None
    save_alerts(alerts)
    return alert


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
        ANALYST_PERSONA
        + "\nBudujesz 'radar katalizatorów' dla agresywnego, krótkoterminowego inwestora, który "
        "poluje na gwałtowne ruchy kursu wokół raportów finansowych, konferencji i premier produktów. "
        "Poniżej dane obserwowanych spółek.\n\n"
        + "\n\n".join(blocks)
        + "\n\nZbuduj raport w tej strukturze:\n\n"
        "## Priorytet WYSOKI\n"
        "Spółki z wydarzeniem w ciągu ~14 dni lub wyraźnym sygnałem w newsach. Dla każdej: data wydarzenia "
        "(albo wprost 'brak danych o dacie'), czego dotyczy, i **jak inwestor mógłby się ustawić** - "
        "wejście przed czy reakcja po, i dlaczego.\n\n"
        "## Priorytet ŚREDNI\n"
        "Wydarzenie w perspektywie ~30 dni albo słabszy sygnał. Krótko, po 1-2 zdania.\n\n"
        "## Priorytet NISKI / cisza\n"
        "Jedna linia zbiorczo - wymień tickery, przy których nic się nie dzieje. Nie rozpisuj się.\n\n"
        "KRYTYCZNE: nie naciągaj newsów na sensację. Jeśli nic się nie dzieje, napisz to wprost - "
        "fałszywy alarm kosztuje inwestora realne pieniądze. Nie zmyślaj dat wydarzeń."
        + MARKDOWN_FORMAT_RULES
        + "\nNa końcu dodaj linię kursywą: *Analiza edukacyjna, nie porada inwestycyjna. "
        "Granie pod eventy w krótkim terminie niesie bardzo wysokie ryzyko.*"
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

    config = HORIZON_CONFIG.get(request.horizon, HORIZON_CONFIG["sredni"])
    company_name = get_company_name(request.ticker) or request.ticker
    quote_currency = get_currency(request.ticker) or "PLN"
    trend_summary = build_trend_summary(request.ticker, config["period"], quote_currency)
    earnings_info = get_next_earnings_date(request.ticker) or "brak danych o terminie najbliższego raportu"

    prompt = (
        ANALYST_PERSONA
        + f"\nAnalizujesz spółkę {company_name} ({request.ticker}), waluta notowań: {quote_currency}.\n\n"
        f"Szerszy kontekst trendu: {trend_summary}\n"
        f"Najbliższy raport finansowy: {earnings_info}\n\n"
        f"Dane dzienne z ostatnich {request.days} sesji:\n"
        + "\n".join(data_lines)
        + f"\n\nHoryzont analizy: {config['opis']}.\n\n"
        f"Pytanie inwestora: \"{request.question}\"\n\n"
        "Odpowiedz w tej strukturze:\n\n"
        "## Odpowiedź\n"
        "Bezpośrednia, konkretna odpowiedź na zadane pytanie - z **wytłuszczonym werdyktem**, jeśli pytanie "
        "dotyczy decyzji. To ma być pierwsza rzecz, którą inwestor przeczyta.\n\n"
        "## Co mówią dane\n"
        "Konkretne obserwacje z powyższych notowań: kluczowe poziomy cenowe, zachowanie wolumenu, "
        "struktura trendu. Podawaj liczby, nie ogólniki.\n\n"
        "## Poziomy do obserwacji\n"
        "Konkretne ceny: wsparcie, opór, poziom unieważniający tezę."
        + MARKDOWN_FORMAT_RULES
        + DISCLAIMER_RULE
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