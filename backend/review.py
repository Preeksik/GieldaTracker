"""
HossaLab - Przegląd portfela: "co jest nie tak z tym, co już mam".

Rozdział ról:
  - Doradca odpowiada na pytanie "co zrobić z nowymi pieniędzmi",
  - Przegląd pokazuje stan portfela: podział, waluty, koncentrację, koszty i podatki.

Wszystkie liczby liczy KOD - wagi, ekspozycje, koszty, podatek. Model AI dostaje gotowe
fakty i tylko je komentuje. Wcześniejsza "analiza dywersyfikacji" kazała modelowi samemu
liczyć procenty z listy pozycji, a to przepis na pomyłki w rachunkach.

Podpięcie w main.py:
    setup_review(app, portfolio_fn=get_portfolio, sales_fn=load_sales, profile_fn=get_instrument_profile,
                 fx_pct_fn=..., ask_fn=call_gemini, journal_fn=..., persona=..., markdown_rules=..., disclaimer=...)
"""

import os
import json
import time
import logging
import threading
from datetime import datetime, date

logger = logging.getLogger("gpw-api")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_CACHE_FILE = os.path.join(BASE_DIR, "instrument_profiles.json")
PROFILE_TTL = 14 * 86400
BELKA = 0.19

COUNTRY_PL = {
    "United States": "USA", "Poland": "Polska", "Germany": "Niemcy", "United Kingdom": "Wielka Brytania",
    "France": "Francja", "Netherlands": "Holandia", "Switzerland": "Szwajcaria", "Ireland": "Irlandia",
    "Canada": "Kanada", "China": "Chiny", "Japan": "Japonia", "Taiwan": "Tajwan", "Cyprus": "Cypr",
    "Italy": "Włochy", "Spain": "Hiszpania", "Sweden": "Szwecja", "Denmark": "Dania", "Norway": "Norwegia",
    "Czech Republic": "Czechy", "Luxembourg": "Luksemburg", "Israel": "Izrael",
}
SECTOR_PL = {
    "Technology": "Technologia", "Financial Services": "Finanse", "Industrials": "Przemysł",
    "Consumer Cyclical": "Konsumpcja (cykliczna)", "Consumer Defensive": "Konsumpcja (podstawowa)",
    "Healthcare": "Ochrona zdrowia", "Basic Materials": "Surowce", "Energy": "Energia",
    "Utilities": "Energetyka i media", "Real Estate": "Nieruchomości", "Communication Services": "Media i telekomunikacja",
}
# Kraj z giełdy notowania - gdy Yahoo nie oddał profilu spółki
SUFFIX_COUNTRY = {"": "USA", "DE": "Niemcy", "L": "Wielka Brytania", "PA": "Francja", "AS": "Holandia",
                  "MI": "Włochy", "MC": "Hiszpania", "SW": "Szwajcaria", "PR": "Czechy", "VI": "Austria",
                  "ST": "Szwecja", "CO": "Dania", "OL": "Norwegia", "HE": "Finlandia", "BR": "Belgia", "LS": "Portugalia"}
ACCOUNT_LABEL = {"zwykle": "Zwykłe", "ike": "IKE", "ikze": "IKZE"}

# Region ETF-u po nazwie indeksu. Kolejność ma znaczenie: "MSCI World ex USA" przed "USA".
ETF_REGIONS = [
    (("all-world", "all world", "acwi", "msci world", "ftse world", "global"), "Świat"),
    (("emerging", "msci em", " em ", "rynki wschodz"), "Rynki wschodzące"),
    (("poland", "polska", "wig", "mwig", "swig"), "Polska"),
    (("nasdaq", "s&p 500", "s&p500", "sp500", "sp 500", "usa", "u.s.", "dow jones", "russell"), "USA"),
    (("stoxx", "europe", "euro ", "eurozone", "msci emu"), "Europa"),
    (("dax", "germany"), "Niemcy"),
    (("japan", "nikkei", "topix"), "Japonia"),
    (("china", "csi"), "Chiny"),
    (("india",), "Indie"),
    (("gold", "złoto", "silver"), "Surowce / metale"),
]


def etf_region(name, ticker=""):
    text = f" {(name or '').lower()} {ticker.lower()} "
    for keys, region in ETF_REGIONS:
        if any(k in text for k in keys):
            return region
    return "ETF – inny"


# ============================================================================
# PROFILE INSTRUMENTÓW (kraj, sektor, typ, dywidenda) - cache na dysku
# ============================================================================

_lock = threading.Lock()
_profiles = None


def _load_profiles():
    global _profiles
    if _profiles is None:
        try:
            with open(PROFILE_CACHE_FILE, "r", encoding="utf-8") as f:
                _profiles = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _profiles = {}
    return _profiles


def cached_profile(ticker, profile_fn):
    with _lock:
        hit = _load_profiles().get(ticker)
    if hit and time.time() - hit.get("ts", 0) < PROFILE_TTL:
        return hit
    try:
        prof = profile_fn(ticker) or {}
    except Exception:
        logger.exception("Nie udało się pobrać profilu %s", ticker)
        prof = {}
    if not prof and hit:
        return hit   # brak sieci - zostaje stary profil
    prof["ts"] = time.time()
    with _lock:
        _load_profiles()[ticker] = prof
        tmp = PROFILE_CACHE_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_profiles, f, ensure_ascii=False)
            os.replace(tmp, PROFILE_CACHE_FILE)
        except OSError:
            logger.exception("Nie udało się zapisać cache profili instrumentów")
    return prof


def classify(position, prof):
    """Typ, region, sektor dla jednej spółki/ETF-u."""
    t = position["ticker"]
    qt = (prof.get("quote_type") or "").upper()
    name = position.get("name") or prof.get("name") or t
    is_etf = qt in ("ETF", "MUTUALFUND") or "ETF" in name.upper() or "UCITS" in name.upper()
    # Yahoo nie zawsze oddaje profil (zdarza się przy ETF-ach z Xetry). Wtedy rozpoznajemy ETF
    # po nazwie indeksu: "FTSE All-World", "NASDAQ 100", "MSCI Poland", "S&P 500".
    if not is_etf and not qt and not t.endswith(".WA") and etf_region(name, t) != "ETF – inny":
        is_etf = True
    if is_etf:
        return {"type": "ETF", "region": etf_region(name + " " + (prof.get("name") or ""), t), "sector": None}
    if t.endswith(".WA"):
        country = "Polska"
    else:
        suffix = t.rsplit(".", 1)[1] if "." in t else ""
        country = COUNTRY_PL.get(prof.get("country"), prof.get("country")) or SUFFIX_COUNTRY.get(suffix, "Nieznany kraj")
    sector = SECTOR_PL.get(prof.get("sector"), prof.get("sector") or "Nieznany sektor")
    return {"type": "Akcje", "region": country, "sector": sector}


# ============================================================================
# OBLICZENIA
# ============================================================================

def _breakdown(items, total):
    """[(etykieta, wartość)] -> posortowana lista z udziałem %."""
    agg = {}
    for label, value in items:
        agg[label] = agg.get(label, 0.0) + (value or 0.0)
    out = [{"label": k, "value": round(v, 2), "pct": round(v / total * 100, 1) if total else 0.0}
           for k, v in agg.items() if v > 0]
    out.sort(key=lambda x: x["value"], reverse=True)
    return out


def compute_review(portfolio, sales, profiles, fx_pct_by_account, today=None):
    """
    Czysta funkcja: dane portfela -> przegląd. portfolio = wynik get_portfolio(),
    profiles = {ticker: profil}, fx_pct_by_account = {konto: % opłaty za przewalutowanie albo None}.
    """
    today = today or date.today()
    lots = [p for p in portfolio.get("positions", []) if p.get("value")]
    total = sum(p["value"] for p in lots)

    # Pozycje zsumowane po spółce (ta sama spółka na kilku kontach = jedno ryzyko)
    by_ticker = {}
    for p in lots:
        t = p["ticker"]
        row = by_ticker.setdefault(t, {"ticker": t, "name": p.get("name") or t, "value": 0.0, "cost": 0.0,
                                       "currency": p.get("quote_currency") or p.get("currency") or "PLN",
                                       "accounts": set()})
        row["value"] += p["value"] or 0
        row["cost"] += p.get("cost") or 0
        row["accounts"].add(p.get("account") or "zwykle")
    positions = []
    for t, row in by_ticker.items():
        cls = classify(row, profiles.get(t, {}))
        positions.append({**row, **cls, "accounts": sorted(row["accounts"]),
                          "weight": round(row["value"] / total * 100, 1) if total else 0.0,
                          "profit": round(row["value"] - row["cost"], 2),
                          "profit_pct": round((row["value"] / row["cost"] - 1) * 100, 1) if row["cost"] else None,
                          "dividend_yield": profiles.get(t, {}).get("dividend_yield")})
    positions.sort(key=lambda x: x["value"], reverse=True)

    weights = [p["value"] / total for p in positions] if total else []
    effective_n = round(1 / sum(w * w for w in weights), 1) if weights else 0

    regions = _breakdown([(p["region"], p["value"]) for p in positions], total)
    currencies = _breakdown([(p["currency"], p["value"]) for p in positions], total)
    types = _breakdown([(p["type"], p["value"]) for p in positions], total)
    accounts = _breakdown([(ACCOUNT_LABEL.get(l.get("account") or "zwykle", l.get("account")), l["value"]) for l in lots], total)
    stocks_total = sum(p["value"] for p in positions if p["type"] == "Akcje")
    sectors = _breakdown([(p["sector"], p["value"]) for p in positions if p["type"] == "Akcje"], stocks_total)

    # Wrażliwość walutowa: ile złotych zmienia się wartość przy ruchu kursu o 10%
    fx_sensitivity = [{"currency": c["label"], "value": c["value"], "pct": c["pct"],
                       "move_10pct": round(c["value"] * 0.10, 2)}
                      for c in currencies if c["label"] != "PLN"]
    foreign_pct = round(sum(c["pct"] for c in currencies if c["label"] != "PLN"), 1)

    # Koszty przewalutowania w ostatnich 12 miesiącach (szacunek: zakupy + sprzedaże w obcej walucie)
    def within_year(d):
        try:
            return (today - datetime.strptime(str(d)[:10], "%Y-%m-%d").date()).days <= 365
        except ValueError:
            return False
    fx_turnover, fx_cost, fx_unknown = 0.0, 0.0, False
    for l in lots:
        if (l.get("currency") or "PLN") != "PLN" and within_year(l.get("buy_date")):
            pct = fx_pct_by_account.get(l.get("account") or "zwykle")
            fx_turnover += l.get("cost") or 0
            fx_cost += (l.get("cost") or 0) * (pct or 0) / 100
            fx_unknown |= pct is None
    for s in sales:
        acc = s.get("account") or "zwykle"
        pct = fx_pct_by_account.get(acc)
        if (s.get("buy_currency") or "PLN") != "PLN":
            if within_year(s.get("buy_date")):
                fx_turnover += s.get("cost_pln") or 0
                fx_cost += (s.get("cost_pln") or 0) * (pct or 0) / 100
            if within_year(s.get("sell_date")):
                fx_turnover += s.get("proceeds_pln") or 0
                fx_cost += (s.get("proceeds_pln") or 0) * (pct or 0) / 100
            fx_unknown |= pct is None

    # Podatki na zwykłym koncie
    year = today.year
    realized = sum(s.get("realized_profit_pln") or 0 for s in sales
                   if (s.get("account") or "zwykle") == "zwykle" and str(s.get("sell_date", ""))[:4] == str(year))
    regular_lots = [l for l in lots if (l.get("account") or "zwykle") == "zwykle"]
    losers = [{"ticker": l["ticker"], "name": l.get("name"), "loss": round(l["profit"], 2), "buy_date": l.get("buy_date")}
              for l in regular_lots if (l.get("profit") or 0) < 0]
    losers.sort(key=lambda x: x["loss"])
    unrealized_loss = round(sum(x["loss"] for x in losers), 2)
    unrealized_gain = round(sum(l.get("profit") or 0 for l in regular_lots if (l.get("profit") or 0) > 0), 2)
    harvest_saving = round(min(-unrealized_loss, max(realized, 0)) * BELKA, 2) if realized > 0 and losers else 0.0
    div_on_regular = [{"ticker": p["ticker"], "name": p["name"], "yield": p["dividend_yield"],
                       "value": round(sum(l["value"] for l in regular_lots if l["ticker"] == p["ticker"]), 2)}
                      for p in positions if (p.get("dividend_yield") or 0) >= 2 and "zwykle" in p["accounts"]]

    tax = {
        "year": year,
        "realized_regular": round(realized, 2),
        "belka_due": round(max(realized, 0) * BELKA, 2),
        "unrealized_gain_regular": unrealized_gain,
        "belka_if_sold": round(unrealized_gain * BELKA, 2),
        "losers": losers[:8],
        "unrealized_loss_regular": unrealized_loss,
        "harvest_saving": harvest_saving,
        "dividends_on_regular": div_on_regular,
    }

    # Ostrzeżenia - reguły w kodzie, z liczbami
    warnings = []

    def zl(x):
        return f"{x:,.0f}".replace(",", " ")   # 1 234 - separator tysięcy tylko w liczbie

    def warn(level, title, text):
        warnings.append({"level": level, "title": title, "text": text})

    if positions:
        top = positions[0]
        if top["weight"] >= 35:
            warn("serious", f"{top['name']} to {top['weight']:.0f}% portfela",
                 "Jedna pozycja decyduje o wyniku całości. Przy spadku o 30% portfel traci "
                 f"ok. {zl(top['value'] * 0.3)} zł.")
        elif top["weight"] >= 20 and top["type"] == "Akcje":
            warn("warning", f"{top['name']} to {top['weight']:.0f}% portfela",
                 "Pojedyncza spółka powyżej 20% to wysoka koncentracja.")
    if regions and regions[0]["pct"] >= 70 and len(positions) > 1:
        warn("warning", f"{regions[0]['pct']:.0f}% w jednym regionie: {regions[0]['label']}",
             "Wynik portfela zależy głównie od jednej gospodarki.")
    if fx_sensitivity:
        biggest = max(fx_sensitivity, key=lambda x: x["value"])
        if foreign_pct >= 40:
            warn("info", f"{foreign_pct:.0f}% portfela w walutach obcych",
                 f"Ruch kursu {biggest['currency']}/PLN o 10% zmienia wartość o ok. {zl(biggest['move_10pct'])} zł "
                 "- niezależnie od tego, co robią same spółki.")
    if fx_cost >= 20:
        warn("info", f"Przewalutowania kosztowały ok. {zl(fx_cost)} zł w 12 miesięcy".replace(",", " "),
             "Każdy zakup i sprzedaż instrumentu w EUR/USD to opłata za wymianę waluty. Częste wyrównywanie "
             "proporcji w planach inwestycyjnych płaci ją dwa razy.")
    if harvest_saving >= 10:
        warn("good", f"Możesz obniżyć podatek o ok. {zl(harvest_saving)} zł".replace(",", " "),
             f"Masz {zl(realized)} zł zrealizowanego zysku w {year} r. na zwykłym koncie i niezrealizowane "
             "straty, które przy sprzedaży przed końcem roku pomniejszą podatek.")
    if div_on_regular:
        names = ", ".join(d["name"] for d in div_on_regular[:3])
        warn("info", "Spółki dywidendowe na zwykłym koncie",
             f"{names}: od dywidendy pobierane jest 19%. Na IKE/IKZE tego podatku nie ma.")
    if effective_n and effective_n < 4 and len(positions) >= 3:
        warn("warning", f"Efektywnie tylko {effective_n:g} pozycji",
             f"Masz {len(positions)} instrumentów, ale wagi są tak nierówne, że portfel zachowuje się jak "
             f"{effective_n:g} równych pozycji.")

    order = {"serious": 0, "warning": 1, "good": 2, "info": 3}
    warnings.sort(key=lambda w: order.get(w["level"], 9))

    return {
        "generated_at": datetime.now().isoformat(timespec="minutes"),
        "total_value": round(total, 2),
        "total_cost": round(sum(p["cost"] for p in positions), 2),
        "positions_count": len(positions),
        "effective_n": effective_n,
        "top_weight": positions[0]["weight"] if positions else 0,
        "top3_weight": round(sum(p["weight"] for p in positions[:3]), 1),
        "positions": [{k: v for k, v in p.items() if k != "cost"} for p in positions],
        "regions": regions, "currencies": currencies, "types": types, "accounts": accounts, "sectors": sectors,
        "fx_sensitivity": fx_sensitivity, "foreign_pct": foreign_pct,
        "costs": {"fx_turnover_12m": round(fx_turnover, 2), "fx_cost_12m": round(fx_cost, 2),
                  "fx_pct": fx_pct_by_account, "fx_unknown": fx_unknown},
        "tax": tax,
        "warnings": warnings,
    }


def facts_for_ai(r):
    """Przegląd -> zwięzły tekst dla modelu. Wszystkie liczby już policzone."""
    pct = lambda items: ", ".join(f"{x['label']} {x['pct']:.1f}%" for x in items)
    lines = [
        f"Wartość portfela: {r['total_value']:.0f} zł, {r['positions_count']} instrumentów "
        f"(efektywnie {r['effective_n']:g} równych pozycji), największa pozycja {r['top_weight']:.1f}%, "
        f"trzy największe {r['top3_weight']:.1f}%.",
        "POZYCJE: " + "; ".join(f"{p['name']} ({p['ticker']}, {p['type']}, {p['region']}) {p['weight']:.1f}%, "
                                f"wynik {p['profit_pct'] if p['profit_pct'] is not None else '?'}%"
                                for p in r["positions"]),
        "REGIONY: " + pct(r["regions"]),
        "WALUTY NOTOWANIA: " + pct(r["currencies"]),
        "TYPY: " + pct(r["types"]),
        "KONTA: " + pct(r["accounts"]),
    ]
    if r["sectors"]:
        lines.append("SEKTORY (tylko akcje): " + pct(r["sectors"]))
    for f in r["fx_sensitivity"]:
        lines.append(f"Ruch {f['currency']}/PLN o 10% = {f['move_10pct']:.0f} zł zmiany wartości.")
    c, t = r["costs"], r["tax"]
    lines.append(f"Przewalutowania w 12 mies.: obrót {c['fx_turnover_12m']:.0f} zł, koszt ok. {c['fx_cost_12m']:.0f} zł.")
    lines.append(f"Podatek {t['year']} (zwykłe konto): zrealizowany zysk {t['realized_regular']:.0f} zł "
                 f"(Belka ok. {t['belka_due']:.0f} zł), niezrealizowany zysk {t['unrealized_gain_regular']:.0f} zł, "
                 f"niezrealizowane straty {t['unrealized_loss_regular']:.0f} zł.")
    if r["warnings"]:
        lines.append("WYKRYTE PRZEZ APLIKACJĘ: " + " | ".join(w["title"] for w in r["warnings"]))
    return "\n".join(lines)


def setup_review(app, portfolio_fn, sales_fn, profile_fn, fx_pct_fn, ask_fn=None, journal_fn=None,
                 persona="", markdown_rules="", disclaimer="", ticker_rule=""):
    from fastapi import HTTPException

    def build():
        portfolio = portfolio_fn()
        if not portfolio.get("positions"):
            raise HTTPException(status_code=400, detail="Portfel jest pusty — dodaj pozycje albo zaimportuj historię z XTB.")
        tickers = sorted({p["ticker"] for p in portfolio["positions"]})
        profiles = {t: cached_profile(t, profile_fn) for t in tickers}
        return compute_review(portfolio, sales_fn(), profiles, fx_pct_fn())

    @app.get("/api/review")
    def review():
        """Przegląd portfela - liczby policzone przez kod (bez AI)."""
        return build()

    @app.post("/api/review/ai")
    def review_ai():
        """Komentarz AI do policzonych liczb + zapis w Dzienniku porad."""
        if not ask_fn:
            raise HTTPException(status_code=500, detail="Brak połączenia z modelem.")
        r = build()
        prompt = (
            persona
            + "\nOceniasz STAN portfela inwestora (nie odpowiadasz na pytanie, w co wpłacić nowe pieniądze - "
            "od tego jest osobny Doradca). Poniżej liczby policzone przez aplikację - są poprawne, NIE przeliczaj "
            "ich od nowa i nie wymyślaj innych.\n\n"
            + facts_for_ai(r)
            + "\n\nNapisz krótko, w tej strukturze:\n"
            "## Ocena\nJedno zdanie werdyktu i ocena portfela w skali **1-10** z uzasadnieniem.\n"
            "## Największe ryzyko\nJedno, konkretne, z liczbą z danych.\n"
            "## Co poprawić\n2-3 konkretne ruchy w ramach TEGO, co inwestor już ma (redukcja, przeniesienie na IKE, "
            "zmiana proporcji w planie), każdy z kwotą w złotych.\n"
            "## Koszty i podatki\nCo z kosztów przewalutowania i podatku da się realnie poprawić.\n"
            + markdown_rules + disclaimer + ticker_rule
        )
        raw = ask_fn(prompt, timeout=90)
        text, jid = journal_fn("Przegląd portfela", raw) if journal_fn else (raw, None)
        return {"commentary": text, "journal_id": jid, "review": r}

    logger.info("Przegląd portfela gotowy (/api/review).")
