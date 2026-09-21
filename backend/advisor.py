"""
HossaLab - Doradca: otwarte pytanie "mam X zł, co z tym zrobić?".

DLACZEGO OSOBNY MODUŁ:
Każda dotychczasowa analiza w aplikacji była przypięta do czegoś, co już masz -
do tickera albo do pozycji w portfelu. Nie dało się zadać najprostszego pytania,
od którego zaczyna każdy inwestor: "mam 10k, myślę długoterminowo, w co to włożyć".
Przy pustym portfelu aplikacja kończyła ślepą uliczką.

CO ODRÓŻNIA TO OD ZAPYTANIA GEMINI WPROST:

1. STRUKTURALNY BRIEF zamiast ściany tekstu - kwota, horyzont, typ konta, apetyt
   na ryzyko i wykluczenia idą do modelu jako osobne pola, więc odpowiedź nie
   rozjeżdża się w ogólniki.

2. REALNY KONTEKST RYNKOWY - aktualne poziomy indeksów, kursy walut, Twój portfel
   (jeśli jest) i watchlista z bieżącymi cenami.

3. WERYFIKACJA KAŻDEGO WYMIENIONEGO SYMBOLU - to jest sedno. Model językowy potrafi
   podać ticker, który nie istnieje, albo cenę sprzed roku. Po otrzymaniu odpowiedzi
   sprawdzamy KAŻDY wymieniony symbol w Yahoo Finance i pokazujemy przy nim realną,
   dzisiejszą cenę - a przy zmyślonych stawiamy ostrzeżenie. Nigdy nie podajemy dalej
   liczby, której nie potwierdziliśmy.

Podpięcie na końcu main.py:
    from advisor import setup_advisor
    setup_advisor(app, ask_fn=call_gemini, persona=ANALYST_PERSONA, ...)
"""

import re
import logging
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

logger = logging.getLogger("gpw-api")

MAX_VERIFIED = 10          # ile symboli maksymalnie sprawdzamy w Yahoo
VERIFY_WORKERS = 4

HORIZONS = {
    "krotki": "krótki (do 12 miesięcy)",
    "sredni": "średni (1-3 lata)",
    "dlugi": "długi (3 lata i więcej)",
}
ACCOUNTS = {
    "zwykle": "zwykłe konto maklerskie - zyski objęte 19% podatkiem Belki",
    "ike": "IKE - zyski zwolnione z podatku Belki przy wypłacie po 60. roku życia",
    "ikze": "IKZE - wpłaty odliczasz od podstawy opodatkowania, przy wypłacie 10% ryczałtu",
}
RISKS = {
    "ostrozny": "ostrożny - priorytetem jest ochrona kapitału, akceptujesz niższy zwrot",
    "zrownowazony": "zrównoważony - godzisz się na wahania w zamian za wyższy zwrot długoterminowy",
    "agresywny": "agresywny - akceptujesz duże obsunięcia w pogoni za ponadprzeciętnym zwrotem",
}

# Skróty, które wyglądają jak ticker, a nim nie są - bez tego lista "wymienionych
# spółek" zapełniłaby się słowami PLN, ETF, IKE i podobnymi.
STOPWORDS = {
    "IKE", "IKZE", "PLN", "USD", "EUR", "GBP", "CHF", "ETF", "ETF-Y", "ETFY", "AI", "PKB",
    "GPW", "WIG", "WIG20", "MWIG40", "SWIG80", "SP", "USA", "UE", "EU", "VAT", "PIT",
    "ROE", "EPS", "PE", "PEG", "EBIT", "EBITDA", "TFI", "OFE", "PPK", "NBP", "FED", "ECB",
    "TAK", "NIE", "ALE", "LUB", "ORAZ", "TICKERY", "UWAGA", "RYZYKO", "PODSUMOWANIE",
}

TICKER_LINE_RE = re.compile(r"^\s*TICKERY\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
TICKER_TOKEN_RE = re.compile(r"\b([A-Z0-9]{1,6}(?:\.[A-Z]{1,3})?)\b")


class AdvisorRequest(BaseModel):
    amount: float | None = None
    currency: str = "PLN"
    horizon: str = "dlugi"
    account: str = "zwykle"
    risk: str = "zrownowazony"
    question: str = ""
    exclusions: str = ""
    use_portfolio: bool = True
    previous_analysis: str = ""


# ============================================================================
# WYCIĄGANIE SYMBOLI Z ODPOWIEDZI
# ============================================================================

def extract_tickers(text):
    """
    Zwraca (lista_symboli, tekst_bez_linii_TICKERY).

    Prosimy model, żeby na końcu odpowiedzi wypisał linię 'TICKERY: ...'. To dużo
    pewniejsze niż wyławianie symboli regexem z prozy, bo w tekście roi się od
    skrótów pisanych wielkimi literami. Regex zostaje jako zapasowy plan, gdy
    model o linii zapomni.
    """
    if not text:
        return [], ""

    found = []
    m = TICKER_LINE_RE.search(text)
    clean = text
    if m:
        for part in re.split(r"[,;]", m.group(1)):
            sym = part.strip().strip("`*_.").upper()
            if sym and sym not in STOPWORDS and len(sym) <= 12:
                found.append(sym)
        clean = TICKER_LINE_RE.sub("", text).rstrip()

    if not found:
        for token in TICKER_TOKEN_RE.findall(text):
            if token in STOPWORDS or len(token) < 2:
                continue
            # Bez kropki-sufiksu wymagamy, żeby symbol był otoczony kontekstem giełdowym
            # (nawias, backtick albo kropka), inaczej łapiemy zwykłe skróty z tekstu.
            if "." not in token and not re.search(rf"[`(]{re.escape(token)}[`)]", text):
                continue
            if token not in found:
                found.append(token)

    # zachowujemy kolejność, usuwamy duplikaty
    seen, ordered = set(), []
    for t in found:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered[:MAX_VERIFIED], clean


def verify_tickers(tickers, price_fn, name_fn, currency_fn):
    """Sprawdza każdy symbol w Yahoo. Równolegle, bo pojedyncze zapytanie to ~1 s."""
    if not tickers or not price_fn:
        return []

    def check(ticker):
        row = {"ticker": ticker, "ok": False, "name": None, "price": None, "currency": None}
        try:
            row["price"] = price_fn(ticker)
            if row["price"] is not None:
                row["ok"] = True
                row["name"] = name_fn(ticker) if name_fn else None
                row["currency"] = (currency_fn(ticker) if currency_fn else None) or "PLN"
        except Exception:
            logger.exception("Nie udało się zweryfikować symbolu %s", ticker)
        return row

    with ThreadPoolExecutor(max_workers=VERIFY_WORKERS) as pool:
        return list(pool.map(check, tickers))


# ============================================================================
# BUDOWANIE KONTEKSTU
# ============================================================================

def build_brief(req):
    lines = []
    if req.amount and req.amount > 0:
        lines.append(f"- Kwota do zainwestowania: {req.amount:,.0f} {req.currency}".replace(",", " "))
    else:
        lines.append("- Kwota: nie podana - zapytaj o nią, jeśli jest kluczowa dla odpowiedzi.")
    lines.append(f"- Horyzont inwestycyjny: {HORIZONS.get(req.horizon, req.horizon)}")
    lines.append(f"- Typ konta: {ACCOUNTS.get(req.account, req.account)}")
    lines.append(f"- Apetyt na ryzyko: {RISKS.get(req.risk, req.risk)}")
    if req.exclusions.strip():
        lines.append(f"- Czego inwestor NIE chce: {req.exclusions.strip()}")
    return "\n".join(lines)


def build_market_context(snapshot_fn, indexes, fx_fn):
    parts = []
    if snapshot_fn and indexes:
        rows = []
        for idx in indexes:
            try:
                price, change = snapshot_fn(idx["tickers"])
            except Exception:
                price, change = None, None
            if price is not None:
                chg = f"{change:+.2f}%" if change is not None else "brak zmiany"
                rows.append(f"  - {idx['label']} ({idx['region']}): {price} ({chg} d/d)")
        if rows:
            parts.append("AKTUALNY STAN RYNKÓW:\n" + "\n".join(rows))

    if fx_fn:
        rates = []
        for cur in ("USD", "EUR"):
            try:
                rate = fx_fn(cur)
            except Exception:
                rate = None
            if rate:
                rates.append(f"{cur}/PLN {rate:.4f}")
        if rates:
            parts.append("KURSY WALUT: " + ", ".join(rates))

    return "\n\n".join(parts)


def build_watchlist_context(watchlist_fn, price_fn, name_fn, limit=12):
    """Watchlista z bieżącymi cenami - konkretne spółki, które inwestor już obserwuje."""
    if not watchlist_fn or not price_fn:
        return ""
    try:
        raw = watchlist_fn() or []
    except Exception:
        return ""

    tickers = []
    for row in raw:
        t = row if isinstance(row, str) else (row or {}).get("ticker")
        if t:
            tickers.append(t)
    if not tickers:
        return ""

    def one(t):
        try:
            price = price_fn(t)
        except Exception:
            price = None
        if price is None:
            return None
        name = None
        try:
            name = name_fn(t) if name_fn else None
        except Exception:
            pass
        return f"  - {name or t} ({t}): {price}"

    with ThreadPoolExecutor(max_workers=VERIFY_WORKERS) as pool:
        rows = [r for r in pool.map(one, tickers[:limit]) if r]

    return "SPÓŁKI Z TWOJEJ WATCHLISTY (aktualne ceny):\n" + "\n".join(rows) if rows else ""


# ============================================================================
# ENDPOINTY
# ============================================================================

def setup_advisor(app, ask_fn, persona="", markdown_rules="", disclaimer="",
                  portfolio_fn=None, portfolio_context_fn=None, watchlist_fn=None,
                  price_fn=None, name_fn=None, currency_fn=None,
                  snapshot_fn=None, indexes=None, fx_fn=None):

    @app.post("/api/advisor/ask")
    def advisor_ask(req: AdvisorRequest):
        """
        Otwarte pytanie inwestycyjne. Działa RÓWNIEŻ przy pustym portfelu -
        to jest właśnie moment, w którym takie pytanie zadaje się najczęściej.
        """
        portfolio_block = "PORTFEL: inwestor nie ma jeszcze żadnych pozycji - zaczyna od zera."
        has_positions = False
        if req.use_portfolio and portfolio_fn and portfolio_context_fn:
            try:
                data = portfolio_fn()
                if data.get("positions"):
                    has_positions = True
                    portfolio_block = portfolio_context_fn(data)
            except Exception:
                logger.exception("Nie udało się zbudować kontekstu portfela dla doradcy")

        market = build_market_context(snapshot_fn, indexes, fx_fn)
        watchlist = build_watchlist_context(watchlist_fn, price_fn, name_fn)

        rules = (
            "\nZASADY ODPOWIEDZI:\n"
            "- Podaj KONKRETNY podział kwoty: ile pieniędzy w co, jaki to procent całości, "
            "ile mniej więcej sztuk za dzisiejszą cenę. Suma musi się zgadzać z podaną kwotą.\n"
            "- Każdą propozycję opisz razem z jej RYZYKIEM - co musi się stać, żeby ta pozycja "
            "straciła, i ile można na niej stracić w złym scenariuszu.\n"
            "- Napisz wprost, czego NIE robić przy tym profilu i dlaczego.\n"
            "- Uwzględnij typ konta: na IKE/IKZE najwięcej zyskują aktywa generujące dochód "
            "opodatkowany (dywidendy, częsty obrót), bo tam podatek Belki nie obowiązuje.\n"
            "- Jeśli podajesz konkretną spółkę lub ETF, ZAWSZE podaj jego symbol z Yahoo Finance "
            "w nawiasie, z sufiksem giełdy (GPW: .WA, Xetra: .DE, Londyn: .L, USA: bez sufiksu).\n"
            "- NIE podawaj cen z pamięci. Jeśli ceny nie ma w danych powyżej, napisz "
            "'cena do sprawdzenia' zamiast zgadywać - aplikacja i tak dopisze realną cenę.\n"
            "- Nie udawaj pewności tam, gdzie jej nie ma. Jeśli brakuje informacji o inwestorze, "
            "powiedz jakiej i jak zmieniłaby odpowiedź.\n"
            "\nNa samym końcu, w osobnej linii, wypisz wszystkie wymienione symbole w formacie:\n"
            "TICKERY: SYM1, SYM2, SYM3\n"
        )

        blocks = [
            persona,
            "\nInwestor prosi Cię o pomoc w ulokowaniu środków.\n",
            "BRIEF INWESTORA:\n" + build_brief(req),
            portfolio_block,
        ]
        if watchlist:
            blocks.append(watchlist)
        if market:
            blocks.append(market)

        if req.previous_analysis:
            blocks.append("POPRZEDNIA CZĘŚĆ ROZMOWY:\n" + req.previous_analysis[:6000])

        question = req.question.strip() or "W co ulokować te środki? Podaj konkretny plan."
        blocks.append("PYTANIE INWESTORA:\n" + question)
        blocks.append(rules + markdown_rules + disclaimer)

        raw = ask_fn("\n\n".join(b for b in blocks if b), timeout=90)
        tickers, answer = extract_tickers(raw)
        verified = verify_tickers(tickers, price_fn, name_fn, currency_fn)

        return {
            "answer": answer,
            "verified": verified,
            "unverified_count": sum(1 for v in verified if not v["ok"]),
            "context": {
                "portfolio_used": has_positions,
                "watchlist_used": bool(watchlist),
                "market_used": bool(market),
            },
        }

    logger.info("Doradca HossaLab gotowy (/api/advisor/ask).")
