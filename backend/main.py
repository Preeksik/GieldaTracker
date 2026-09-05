from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yfinance as yf
import requests
import os
import json
import uuid
import math
import logging
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
    """Zwraca wszystkie pozycje portfela wraz z aktualną wyceną i zyskiem/stratą."""
    entries = load_portfolio()
    enriched = []
    total_cost = 0.0
    total_value = 0.0
    price_cache = {}

    for entry in entries:
        ticker = entry["ticker"]

        if ticker not in price_cache:
            price_cache[ticker] = get_current_price(ticker)

        current_price = price_cache[ticker]
        cost = entry["quantity"] * entry["buy_price"]
        value = entry["quantity"] * current_price if current_price is not None else None
        profit = (value - cost) if value is not None else None
        profit_pct = (profit / cost * 100) if profit is not None and cost > 0 else None

        total_cost += cost
        if value is not None:
            total_value += value

        enriched.append({
            **entry,
            "name": entry.get("name") or ticker,
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
    new_entry = {
        "id": str(uuid.uuid4()),
        "ticker": ticker,
        "name": get_company_name(ticker) or ticker,
        "quantity": entry.quantity,
        "buy_price": entry.buy_price,
        "buy_date": entry.buy_date,
        "note": entry.note,
    }
    entries.append(new_entry)
    save_portfolio(entries)
    return new_entry


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
    cost = entry["quantity"] * entry["buy_price"]
    value = entry["quantity"] * current_price if current_price is not None else None
    profit = (value - cost) if value is not None else None
    profit_pct = (profit / cost * 100) if profit is not None and cost > 0 else None

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
                f"(od {start_price:.2f} do {end_price:.2f} PLN), "
                f"zakres {min_price:.2f}-{max_price:.2f} PLN, "
                f"śr. wolumen dzienny {avg_volume:,.0f} "
                f"(ostatnio: {recent_volume:,.0f})."
            )
    except Exception:
        logger.exception("Nie udało się pobrać trendu (%s) dla %s", config["period"], ticker)

    earnings_info = get_next_earnings_date(ticker) or "brak dostępnych danych o terminie najbliższego raportu"

    # Wspólny blok danych o pozycji - używany zarówno w pełnej analizie, jak i w follow-upie
    position_facts = (
        f"Spółka: {entry.get('name') or ticker} ({ticker})\n"
        f"- Ilość: {entry['quantity']}, cena zakupu: {entry['buy_price']} PLN (data zakupu: {entry['buy_date']})\n"
        f"- Aktualna cena: {current_price if current_price is not None else 'brak danych'} PLN\n"
        f"- Aktualny zysk/strata: {safe_round(profit) if profit is not None else 'brak danych'} PLN "
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


@app.get("/api/portfolio/report")
def portfolio_report():
    """
    Generuje pełny raport AI dla całego portfela: dla każdej pozycji rekomendację
    (sprzedaj / trzymaj / dokup), szacowane prawdopodobieństwo dalszego ruchu
    oraz informację o nadchodzących wydarzeniach (raporty finansowe, konferencje).
    """
    portfolio_data = get_portfolio()
    positions = portfolio_data["positions"]

    if not positions:
        raise HTTPException(
            status_code=400,
            detail="Portfel jest pusty — dodaj przynajmniej jedną pozycję, żeby wygenerować raport."
        )

    position_blocks = []
    for pos in positions:
        ticker = pos["ticker"]

        # Krótkie podsumowanie trendu z ostatnich 3 miesięcy (żeby nie wysyłać setek wierszy do AI)
        trend_summary = "brak danych o trendzie"
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="3mo")
            if not hist.empty:
                hist = hist.dropna()
                start_price = float(hist["Close"].iloc[0])
                end_price = float(hist["Close"].iloc[-1])
                min_price = float(hist["Close"].min())
                max_price = float(hist["Close"].max())
                change_pct = ((end_price - start_price) / start_price * 100) if start_price else 0
                avg_volume = float(hist["Volume"].mean())
                trend_summary = (
                    f"Ostatnie 3 miesiące: zmiana {change_pct:+.1f}% "
                    f"(od {start_price:.2f} do {end_price:.2f} PLN), "
                    f"zakres {min_price:.2f}-{max_price:.2f} PLN, "
                    f"śr. wolumen dzienny {avg_volume:,.0f}."
                )
        except Exception:
            logger.exception("Nie udało się pobrać trendu 3mo dla %s", ticker)

        earnings_info = get_next_earnings_date(ticker) or "brak dostępnych danych o terminie najbliższego raportu"

        block = (
            f"### {ticker}\n"
            f"- Ilość: {pos['quantity']}, cena zakupu: {pos['buy_price']} PLN, data zakupu: {pos['buy_date']}\n"
            f"- Aktualna cena: {pos['current_price']} PLN\n"
            f"- Aktualny zysk/strata: {pos['profit']} PLN ({pos['profit_pct']}%)\n"
            f"- Trend: {trend_summary}\n"
            f"- Najbliższy raport finansowy / wydarzenie: {earnings_info}\n"
        )
        if pos.get("note"):
            block += f"- Notatka użytkownika: {pos['note']}\n"
        position_blocks.append(block)

    summary = portfolio_data["summary"]
    portfolio_summary_text = (
        f"Łączny koszt portfela: {summary['total_cost']} PLN, "
        f"łączna wartość: {summary['total_value']} PLN, "
        f"łączny zysk/strata: {summary['total_profit']} PLN ({summary['total_profit_pct']}%)."
    )

    prompt = (
        "Jesteś doświadczonym analitykiem giełdowym. Poniżej masz szczegóły portfela inwestora "
        "z Giełdy Papierów Wartościowych w Warszawie. Dla KAŻDEJ pozycji z osobna podaj:\n"
        "1. Rekomendację: SPRZEDAJ / TRZYMAJ / DOKUP.\n"
        "2. Szacowane prawdopodobieństwo dalszego wzrostu vs spadku w najbliższych tygodniach "
        "(np. \"~60% szans na wzrost\") wraz z krótkim uzasadnieniem opartym na trendzie i wolumenie.\n"
        "3. Czy zbliża się istotne wydarzenie (raport finansowy, konferencja, publikacja wyników) "
        "mogące wpłynąć na kurs, i jak inwestor mógłby się do niego ustawić. Jeśli nie masz danych "
        "o dacie, napisz to wprost zamiast zgadywać.\n"
        "Na końcu dodaj krótkie podsumowanie całego portfela (2-3 zdania) oraz jedno zdanie zastrzeżenia, "
        "że to nie jest porada inwestycyjna, tylko analiza edukacyjna.\n\n"
        f"PODSUMOWANIE PORTFELA:\n{portfolio_summary_text}\n\n"
        "POZYCJE:\n" + "\n".join(position_blocks)
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        resp = requests.post(url, json=payload, timeout=60)
    except requests.exceptions.RequestException as e:
        logger.exception("Błąd sieci przy wywołaniu Gemini (raport portfela)")
        raise HTTPException(status_code=502, detail=f"Nie udało się połączyć z Gemini API: {e}")

    try:
        data = resp.json()
    except ValueError:
        logger.error("Gemini zwrócił nie-JSON (raport portfela): %s", resp.text[:500])
        raise HTTPException(status_code=502, detail="Gemini API zwróciło nieprawidłową odpowiedź.")

    if resp.status_code != 200:
        err = data.get("error", {}).get("message", "Nieznany błąd API")
        logger.error("BŁĄD Z GOOGLE (raport portfela, status %s): %s", resp.status_code, err)
        raise HTTPException(status_code=502, detail=err)

    try:
        report_text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        logger.error("Nieoczekiwany kształt odpowiedzi Gemini (raport portfela): %s", data)
        raise HTTPException(status_code=502, detail="Gemini nie zwróciło treści raportu.")

    return {"report": report_text, "positions_analyzed": len(positions)}


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