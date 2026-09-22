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
# ODCZYTANIE INTENCJI Z TEKSTU PYTANIA
# ============================================================================
#
# Formularz ma domyślnie "3 lata+" i "zrównoważony". Jeśli inwestor napisał
# "krótkoterminowo, agresywnie" tylko w polu pytania, model dostawał SPRZECZNY brief:
# pola mówiły "długo i spokojnie", tekst "krótko i agresywnie". Pod pierwszy wariant
# spokojny deweloper faktycznie pasuje - stąd ATAL. Słowa inwestora są najświeższą
# intencją, więc mają pierwszeństwo przed domyślnymi przełącznikami.

HORIZON_WORDS = [
    ("krotki", r"kr[oó]tkoterm|kr[oó]tki(m|ego)?\s+(termin|horyzont)|na\s+szybko|szybki\w*\s+zysk|"
               r"kilka\s+(dni|tygodni)|swing|na\s+(tydzie[nń]|miesi[aą]c)|do\s+ko[nń]ca\s+roku"),
    ("dlugi", r"d[lł]ugoterm|d[lł]ugi(m|ego)?\s+(termin|horyzont)|na\s+lata|emerytur|kilka\s+lat|\b\d{2}\s+lat"),
    ("sredni", r"[sś]rednioterm|[sś]redni(m|ego)?\s+(termin|horyzont)|rok\s+lub\s+dwa|1\s*-\s*3\s+lat"),
]
RISK_WORDS = [
    ("agresywny", r"agresywn|ryzykown|wysokie\w*\s+ryzyk|du[zż]e\w*\s+ryzyk|spekul|moonshot|high\s*risk|"
                  r"wysok\w+\s+zmienn"),
    ("ostrozny", r"ostro[zż]n|bezpieczn|bez\s+ryzyka|niskie\w*\s+ryzyk|konserwatyw"),
    ("zrownowazony", r"zr[oó]wnowa[zż]|umiarkowan"),
]
# Łapiemy frazę po słowie przeczącym, ale tylko do najbliższego "i"/"oraz"/przecinka -
# inaczej "brak krypto i brak CFD" dawało jedno wykluczenie "krypto i brak CFD".
NEGATION_RE = re.compile(
    r"(?:\bbez\b|\bbrak\w*|\bani\b|nie\s+chc\w*|[zż]adn\w*|wyklucz\w*|omijaj\w*)\s+"
    r"([^.;,\n]{2,40}?)(?=\s+(?:i|oraz|ani)\s|[,.;\n]|$)",
    re.IGNORECASE,
)


def interpret(req):
    """Zwraca (horyzont, ryzyko, wykluczenia, co_odczytano_z_tekstu)."""
    text = (req.question or "").lower()
    horizon, risk = req.horizon, req.risk
    detected = {}

    for key, pat in HORIZON_WORDS:
        if re.search(pat, text):
            detected["horizon"] = key
            horizon = key
            break
    for key, pat in RISK_WORDS:
        if re.search(pat, text):
            detected["risk"] = key
            risk = key
            break

    negated = [m.group(1).strip(" ,") for m in NEGATION_RE.finditer(req.question or "")]
    exclusions = ", ".join(x for x in [req.exclusions.strip(), *negated] if x)
    if negated:
        detected["exclusions"] = negated

    return horizon, risk, exclusions, detected


def _collect_user_tickers(portfolio_fn, watchlist_fn, index_fn):
    """Symbole użytkownika dokładane do uniwersum skanera: portfel, watchlista, wyszukiwane."""
    out = []
    try:
        for p in (portfolio_fn() or {}).get("positions", []) if portfolio_fn else []:
            out.append((p.get("ticker"), p.get("name")))
    except Exception:
        pass
    try:
        for w in (watchlist_fn() or []) if watchlist_fn else []:
            t = w if isinstance(w, str) else (w or {}).get("ticker")
            out.append((t, None if isinstance(w, str) else w.get("name")))
    except Exception:
        pass
    try:
        for t, v in ((index_fn() or {}).items() if index_fn else []):
            out.append((t, (v or {}).get("name")))
    except Exception:
        pass
    return [(t, n) for t, n in out if t]


# ============================================================================
# ENDPOINTY
# ============================================================================

def setup_advisor(app, ask_fn, persona="", markdown_rules="", disclaimer="",
                  portfolio_fn=None, portfolio_context_fn=None, watchlist_fn=None,
                  price_fn=None, name_fn=None, currency_fn=None,
                  snapshot_fn=None, indexes=None, fx_fn=None,
                  earnings_fn=None, news_fn=None, search_index_fn=None, download_fn=None):
    import screener

    @app.post("/api/advisor/ask")
    def advisor_ask(req: AdvisorRequest):
        """
        Otwarte pytanie inwestycyjne. Działa RÓWNIEŻ przy pustym portfelu -
        to jest właśnie moment, w którym takie pytanie zadaje się najczęściej.
        """
        # 1. Co inwestor naprawdę chce - pola formularza skorygowane o treść pytania.
        horizon, risk, exclusions, detected = interpret(req)
        req = req.model_copy(update={"horizon": horizon, "risk": risk, "exclusions": exclusions})

        portfolio_block = "PORTFEL: inwestor nie ma jeszcze żadnych pozycji - zaczyna od zera."
        has_positions = False
        owned = []
        if req.use_portfolio and portfolio_fn and portfolio_context_fn:
            try:
                data = portfolio_fn()
                if data.get("positions"):
                    has_positions = True
                    owned = [p.get("ticker") for p in data["positions"] if p.get("ticker")]
                    portfolio_block = (
                        "OBECNY PORTFEL INWESTORA - wyłącznie jako KONTEKST (koncentracja, "
                        "dywersyfikacja, żeby nie dublować ekspozycji). To NIE jest lista kandydatów "
                        "do zakupu.\n" + portfolio_context_fn(data)
                    )
            except Exception:
                logger.exception("Nie udało się zbudować kontekstu portfela dla doradcy")

        # 2. Skaner rynku pod profil - model dostaje gotowych kandydatów z liczbami,
        #    zamiast wybierać z jedynych spółek, jakie widzi (czyli z portfela).
        scan = None
        try:
            scan = screener.screen(
                horizon, risk, req.amount, exclusions,
                extra_tickers=_collect_user_tickers(portfolio_fn, watchlist_fn, search_index_fn),
                download_fn=download_fn, earnings_fn=earnings_fn, news_fn=news_fn, owned=owned,
            )
        except Exception:
            logger.exception("Skaner rynku nie zadziałał - Doradca odpowie bez listy kandydatów")

        market = build_market_context(snapshot_fn, indexes, fx_fn)
        watchlist = build_watchlist_context(watchlist_fn, price_fn, name_fn)

        profile = screener.get_profile(horizon, risk)
        short = horizon == "krotki"
        rules = (
            "\nZASADY ODPOWIEDZI:\n"
            "- Wybieraj PRZEDE WSZYSTKIM z listy KANDYDATÓW ZE SKANERA - zostali dobrani do profilu "
            "strategii na podstawie realnych danych. Jeśli proponujesz coś spoza listy, oznacz to "
            "wprost jako 'spoza skanera' i wyjaśnij, dlaczego pasuje do profilu.\n"
            "- Każda propozycja MUSI pasować do profilu strategii. Nie proponuj spółek z portfela "
            "inwestora tylko dlatego, że je ma.\n"
            "- Nigdy nie proponuj CFD, dźwigni, opcji ani kryptowalut, chyba że inwestor wprost o to prosi.\n"
            + ("- Profil krótkoterminowy: każda propozycja MUSI mieć konkretny katalizator z datą lub "
               "przybliżonym terminem w horyzoncie, poziom wejścia, cel i poziom stop-loss w procentach. "
               "Jeśli dla kandydata nie znasz katalizatora, powiedz to zamiast go wymyślać.\n" if short else "")
            + "- Podaj KONKRETNY podział kwoty: ile pieniędzy w co, jaki to procent całości, "
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
        ]
        if scan and scan["candidates"]:
            blocks.append(screener.candidates_block(scan))
        else:
            blocks.append(f"PROFIL STRATEGII: {profile['label']}. {profile['brief']}\n"
                          "(Skaner rynku nie zwrócił kandydatów - zaznacz, że propozycje nie są "
                          "poparte bieżącymi danymi o zmienności.)")
        blocks.append(portfolio_block)
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

        # 3. Kod sprawdza model: czy każda propozycja pasuje do profilu.
        try:
            ok_tickers = [v["ticker"] for v in verified if v["ok"]]
            metrics = screener.fetch_metrics(ok_tickers, download_fn=download_fn) if ok_tickers else {}
        except Exception:
            logger.exception("Nie udało się pobrać metryk do sprawdzenia dopasowania")
            metrics = {}
        candidate_set = {c["ticker"] for c in (scan or {}).get("candidates", [])}
        for v in verified:
            m = metrics.get(v["ticker"])
            fit, reason = screener.fit_check(m, profile) if v["ok"] else (None, None)
            v["metrics"] = m
            v["fit"] = fit
            v["fit_reason"] = reason
            v["from_scanner"] = v["ticker"] in candidate_set

        return {
            "answer": answer,
            "verified": verified,
            "unverified_count": sum(1 for v in verified if not v["ok"]),
            "misfit_count": sum(1 for v in verified if v.get("fit") is False),
            "interpreted": {
                "horizon": horizon, "risk": risk, "exclusions": exclusions,
                "from_text": detected, "profile": profile["label"],
            },
            "scan": {
                "candidates": (scan or {}).get("candidates", []),
                "scanned": (scan or {}).get("scanned", 0),
                "relaxed": (scan or {}).get("relaxed", False),
                "rejected_owned": (scan or {}).get("rejected_owned", []),
            },
            "context": {
                "portfolio_used": has_positions,
                "watchlist_used": bool(watchlist),
                "market_used": bool(market),
            },
        }

    @app.post("/api/advisor/warmup")
    def advisor_warmup():
        """Pobiera metryki całego uniwersum do cache - kolejne pytania do Doradcy idą szybko."""
        uni = screener.build_universe(_collect_user_tickers(portfolio_fn, watchlist_fn, search_index_fn))
        m = screener.fetch_metrics(list(uni), download_fn=download_fn)
        return {"universe": len(uni), "with_data": len(m)}

    # Rozgrzewamy cache w tle przy starcie - pierwsze pytanie nie czeka ~30 s na
    # pobranie danych ~70 spółek.
    def _warm():
        try:
            advisor_warmup()
        except Exception:
            logger.exception("Rozgrzewanie skanera nie powiodło się")
    import threading
    threading.Thread(target=_warm, daemon=True).start()

    logger.info("Doradca HossaLab gotowy (/api/advisor/ask).")