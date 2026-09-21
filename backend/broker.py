"""
HossaLab - koszty maklerskie: tabele opłat, profil inwestora, kalkulator.

SKĄD TEN MODUŁ:
Analiza AI napisała, że dzielenie 10 000 zł na transze "nie ma sensu, bo prowizje
zjadłyby zysk". U tradycyjnego brokera to prawda - ale w XTB akcje i ETF-y do
100 000 EUR obrotu miesięcznie są bez prowizji, więc wniosek był zbudowany na
fałszywym założeniu. Model nie wiedział, u kogo inwestujesz.

CO ROBI:
1. Trzyma tabele opłat kilku brokerów - każda ze ŹRÓDŁEM i DATĄ sprawdzenia,
   bo cenniki się zmieniają, a stara stawka podana z pewnością siebie jest gorsza
   niż żadna.
2. Pamięta, u którego brokera masz które konto (zwykłe / IKE / IKZE).
3. Liczy koszty DETERMINISTYCZNIE, w kodzie - model językowy dostaje gotowe liczby
   zamiast samemu zgadywać prowizje.
4. Dokleja do każdego promptu analitycznego blok z Twoimi realnymi kosztami.

Podpięcie na końcu main.py:
    from broker import setup_broker, broker_prompt_block
    setup_broker(app, fx_fn=get_fx_rate)
"""

import os
import json
import time
import logging
import threading
from copy import deepcopy

from pydantic import BaseModel

logger = logging.getLogger("gpw-api")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_FILE = os.path.join(BASE_DIR, "broker_profile.json")

CHECKED = "2026-09-21"   # dzień, w którym sprawdzono tabele niżej

# ============================================================================
# TABELE OPŁAT
# ============================================================================
#
# Rynki uproszczone do dwóch kategorii, bo tylko te występują w Twoim portfelu:
#   gpw        - akcje i ETF-y notowane w Warszawie, w PLN
#   zagranica  - główne rynki: Xetra, Londyn, USA (u brokerów tradycyjnych mają
#                najniższe minimalne prowizje; Szwajcaria czy Kanada są droższe)
#
# fx_pct = None oznacza, że źródło nie podaje kosztu przewalutowania - wtedy go
# nie wliczamy i mówimy to wprost, zamiast wstawiać zmyśloną liczbę.

PRESETS = {
    "xtb": {
        "name": "XTB",
        "source": "https://xtb.scdn5.secure.raxcdn.com/file/0104/37/31a2b42e-c7fe-406c-a37f-6af477ccecf1/06-tabela-oplat-i-prowizji-pl-clean.pdf",
        "source_kind": "oficjalna tabela opłat z 5 stycznia 2026",
        "checked": CHECKED,
        "gpw": {"pct": 0.0, "min": 0, "min_currency": "PLN"},
        "zagranica": {"pct": 0.0, "min": 0, "min_currency": "EUR"},
        "free_monthly_eur": 100000,
        "above_free": {"pct": 0.2, "min": 10, "min_currency": "EUR"},
        "fx_pct": 0.5,
        "notes": [
            "0% prowizji do 100 000 EUR obrotu w miesiącu, powyżej 0,2% (min. 10 EUR).",
            "Przewalutowanie 0,5% przy zakupie instrumentu w walucie innej niż waluta rachunku - i ponownie przy sprzedaży.",
            "IKE i IKZE: bez opłaty za prowadzenie; tabela nie wyłącza ich z zasad prowizji.",
            "Rachunek walutowy (EUR/USD) pozwala uniknąć przewalutowania przy każdym zakupie.",
        ],
    },
    "mbank": {
        "name": "mBank eMakler",
        "source": "https://www.mdm.pl/bm/emakler-oplaty",
        "source_kind": "oficjalna strona opłat (bez daty obowiązywania)",
        "checked": CHECKED,
        "gpw": {"pct": 0.39, "min": 5, "min_currency": "PLN"},
        "zagranica": {"pct": 0.29, "min": 14, "min_currency": "PLN"},
        "free_monthly_eur": None,
        "above_free": None,
        "fx_pct": 0.2,
        "notes": [
            "GPW: 0,39% (min. 5 zł). Zagranica: 0,29% (min. 14 zł).",
            "Przewalutowanie: bank podaje średni spread ok. 0,2%.",
            "Rachunek bez opłat przy zgodzie na komunikację elektroniczną (inaczej 50 zł rocznie).",
        ],
    },
    "bossa": {
        "name": "bossa (DM BOŚ)",
        "source": "https://bossa.pl/oferta/oplaty-i-prowizje",
        "source_kind": "oficjalna strona opłat",
        "checked": CHECKED,
        "gpw": {"pct": 0.38, "min": 5, "min_currency": "PLN"},
        "zagranica": {"pct": 0.29, "min": 14, "min_currency": "PLN"},
        "free_monthly_eur": None,
        "above_free": None,
        "fx_pct": None,
        "notes": [
            "GPW: 0,38% (min. 5 zł). USA/Niemcy/UK: 0,29% (min. 14 zł / 4 EUR / 4 USD).",
            "Francja, Holandia, Belgia i Szwajcaria mają wyższe minima.",
            "Strona nie podaje kosztu przewalutowania - nie jest wliczony.",
            "IKE/IKZE: te same prowizje, prowadzenie bezpłatne.",
        ],
    },
    "trading212": {
        "name": "Trading 212",
        "source": "https://brokerchooser.com/broker-reviews/trading-212-review/trading-212-fees",
        "source_kind": "zestawienie BrokerChooser, aktualizacja 17.09.2026 (nie tabela brokera)",
        "checked": CHECKED,
        "gpw": {"pct": 0.0, "min": 0, "min_currency": "PLN"},
        "zagranica": {"pct": 0.0, "min": 0, "min_currency": "PLN"},
        "free_monthly_eur": None,
        "above_free": None,
        "fx_pct": 0.15,
        "notes": [
            "Akcje i ETF-y bez prowizji, bez podanego limitu miesięcznego.",
            "Przewalutowanie 0,15%.",
            "Źródło nie mówi nic o IKE/IKZE - sprawdź u brokera, zanim założysz takie konto.",
        ],
    },
    "ibkr": {
        "name": "Interactive Brokers",
        "source": "https://www.interactivebrokers.com/en/pricing/commissions-stocks-europe.php",
        "source_kind": "oficjalny cennik (plan Fixed)",
        "checked": CHECKED,
        "gpw": {"pct": 0.10, "min": 15, "min_currency": "PLN"},
        "zagranica": {"pct": 0.10, "min": 4, "min_currency": "EUR"},
        "free_monthly_eur": None,
        "above_free": None,
        "fx_pct": None,
        "notes": [
            "Plan Fixed: GPW 0,1% (min. 15 zł), Xetra 0,1% (min. 4 EUR).",
            "Plan Tiered na GPW: 0,05% (min. 5 zł, max. 125 zł) - tańszy przy większych kwotach.",
            "Kosztu przewalutowania nie sprawdzałem - nie jest wliczony.",
            "Brak polskiego IKE/IKZE.",
        ],
    },
}

CUSTOM_TEMPLATE = {
    "name": "Własne stawki",
    "source": None,
    "source_kind": "wpisane ręcznie",
    "checked": None,
    "gpw": {"pct": 0.0, "min": 0, "min_currency": "PLN"},
    "zagranica": {"pct": 0.0, "min": 0, "min_currency": "PLN"},
    "free_monthly_eur": None,
    "above_free": None,
    "fx_pct": None,
    "notes": ["Stawki wpisane ręcznie."],
}

ACCOUNT_LABEL = {"zwykle": "Konto zwykłe", "ike": "IKE", "ikze": "IKZE"}

_lock = threading.RLock()
_fx_cache = {}          # waluta -> (timestamp, kurs)
FX_TTL = 3600


# ============================================================================
# PROFIL
# ============================================================================

def _default_profile():
    return {"accounts": {"zwykle": "xtb", "ike": "xtb", "ikze": "xtb"}, "custom": deepcopy(CUSTOM_TEMPLATE)}


def load_profile():
    try:
        with open(PROFILE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        prof = _default_profile()
        prof["accounts"].update(data.get("accounts") or {})
        if isinstance(data.get("custom"), dict):
            prof["custom"].update(data["custom"])
        return prof
    except (FileNotFoundError, json.JSONDecodeError):
        return _default_profile()


def save_profile(prof):
    tmp = PROFILE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(prof, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROFILE_FILE)


def schedule(broker_id, profile=None):
    if broker_id == "wlasny":
        return (profile or load_profile())["custom"]
    return PRESETS.get(broker_id)


# ============================================================================
# OBLICZENIA
# ============================================================================

def _rate(currency, fx_fn):
    """Kurs waluty do PLN, z pamięcią na godzinę. PLN = 1."""
    cur = (currency or "PLN").upper()
    if cur == "PLN":
        return 1.0
    with _lock:
        hit = _fx_cache.get(cur)
        if hit and time.monotonic() - hit[0] < FX_TTL:
            return hit[1]
    rate = None
    if fx_fn:
        try:
            rate = fx_fn(cur)
        except Exception:
            rate = None
    # Awaryjne przybliżenie, gdy Yahoo nie odpowie - kurs jest tylko do przeliczenia
    # minimalnej prowizji (kilka euro), więc błąd rzędu groszy nie zmienia wniosku.
    if not rate:
        rate = {"EUR": 4.25, "USD": 3.90, "GBP": 4.95}.get(cur, 4.0)
    with _lock:
        _fx_cache[cur] = (time.monotonic(), rate)
    return rate


def _one_commission(part, amount, fx_fn):
    if not part or amount <= 0:
        return 0.0
    c = amount * (part.get("pct") or 0) / 100
    minimum = (part.get("min") or 0) * _rate(part.get("min_currency"), fx_fn)
    c = max(c, minimum)
    if part.get("max"):
        c = min(c, part["max"] * _rate(part.get("max_currency") or part.get("min_currency"), fx_fn))
    return c


def trade_cost(sched, amount, market, fx_fn=None, turnover_before_eur=0.0):
    """
    Koszt jednego zakupu w PLN. Zwraca słownik z prowizją, przewalutowaniem i sumą.
    turnover_before_eur - obrót już zrobiony w tym miesiącu (ma znaczenie dla progu XTB).
    """
    market = "zagranica" if market == "zagranica" else "gpw"
    part = sched.get(market) or {}
    commission = 0.0

    free = sched.get("free_monthly_eur")
    if free:
        eur = _rate("EUR", fx_fn)
        threshold_pln = max(0.0, free - turnover_before_eur) * eur
        base = min(amount, threshold_pln)
        excess = max(0.0, amount - threshold_pln)
        commission = _one_commission(part, base, fx_fn) if base > 0 and (part.get("pct") or part.get("min")) else 0.0
        if excess > 0:
            commission += _one_commission(sched.get("above_free"), excess, fx_fn)
    else:
        commission = _one_commission(part, amount, fx_fn)

    fx_pct = sched.get("fx_pct")
    fx = amount * fx_pct / 100 if (market == "zagranica" and fx_pct) else 0.0
    fx_unknown = market == "zagranica" and fx_pct is None

    return {
        "commission": round(commission, 2),
        "fx": round(fx, 2),
        "fx_unknown": fx_unknown,
        "total": round(commission + fx, 2),
    }


def entry_comparison(sched, amount, tranches, market, fx_fn=None):
    """
    Jednorazowy zakup vs ten sam kapitał w N równych transzach.
    Założenie: każda transza w innym miesiącu (klasyczne uśrednianie), więc próg
    darmowego obrotu w XTB odnawia się przy każdej.
    """
    tranches = max(1, int(tranches))
    lump = trade_cost(sched, amount, market, fx_fn)
    one = trade_cost(sched, amount / tranches, market, fx_fn)
    dca = {k: round(one[k] * tranches, 2) if isinstance(one[k], float) else one[k] for k in one}
    dca["per_tranche"] = one

    # Kwota, poniżej której minimalna prowizja zaczyna dominować (efektywny koszt > stawka %).
    part = sched.get("zagranica" if market == "zagranica" else "gpw") or {}
    trap = None
    if part.get("pct") and part.get("min"):
        trap = round(part["min"] * _rate(part.get("min_currency"), fx_fn) / (part["pct"] / 100), 0)

    return {
        "lump": {**lump, "pct_of_amount": round(lump["total"] / amount * 100, 3) if amount else 0},
        "dca": {**dca, "pct_of_amount": round(dca["total"] / amount * 100, 3) if amount else 0},
        "extra_cost_of_dca": round(dca["total"] - lump["total"], 2),
        "min_commission_trap_below": trap,
    }


# ============================================================================
# BLOK DO PROMPTÓW
# ============================================================================

def _describe(sched, fx_fn):
    lines = [f"{sched['name']} (źródło: {sched.get('source_kind') or 'brak'})"]
    for note in sched.get("notes", [])[:3]:
        lines.append(f"    - {note}")

    # Konkretny, przeliczony przykład - model dostaje gotowe liczby zamiast zgadywać.
    for market, label in (("gpw", "GPW"), ("zagranica", "ETF zagraniczny")):
        cmp = entry_comparison(sched, 10000, 10, market, fx_fn)
        fx_note = " (bez przewalutowania - brak danych)" if cmp["lump"]["fx_unknown"] else ""
        lines.append(
            f"    - Przykład, {label}: 10 000 zł jednorazowo = {cmp['lump']['total']:.2f} zł kosztów; "
            f"10 transz po 1 000 zł = {cmp['dca']['total']:.2f} zł{fx_note}. "
            f"Transze droższe o {cmp['extra_cost_of_dca']:.2f} zł."
        )
    return "\n".join(lines)


def broker_prompt_block(fx_fn=None):
    """Blok doklejany do promptów analitycznych: Twoi brokerzy i realne koszty."""
    prof = load_profile()
    by_broker = {}
    for acc in ("zwykle", "ike", "ikze"):
        by_broker.setdefault(prof["accounts"].get(acc, "xtb"), []).append(ACCOUNT_LABEL[acc])

    parts = []
    for broker_id, accounts in by_broker.items():
        sched = schedule(broker_id, prof)
        if not sched:
            continue
        parts.append(f"  • {', '.join(accounts)}: " + _describe(sched, fx_fn))

    if not parts:
        return ""

    return (
        "\nKOSZTY TRANSAKCYJNE INWESTORA (policzone przez aplikację z tabel opłat - traktuj jako fakt):\n"
        + "\n".join(parts)
        + "\nZASADY:\n"
        "- Nie zakładaj żadnych innych prowizji niż podane wyżej. Jeśli koszt przewalutowania jest "
        "oznaczony jako nieznany, powiedz to zamiast zgadywać.\n"
        "- Gdy doradzasz jednorazowy zakup albo transze, oprzyj argument kosztowy na liczbach z przykładu. "
        "Jeśli transze nie są droższe, uzasadnij wybór czym innym (ryzyko wejścia w szczyt, dyscyplina, "
        "dostępność kapitału), a nie prowizjami.\n"
        "- Przy zakupach w obcej walucie uwzględnij przewalutowanie przy kupnie i przy sprzedaży.\n"
    )


# ============================================================================
# ENDPOINTY
# ============================================================================

class ProfileUpdate(BaseModel):
    accounts: dict | None = None
    custom: dict | None = None


class CostRequest(BaseModel):
    amount: float
    tranches: int = 1
    market: str = "gpw"          # gpw | zagranica


def setup_broker(app, fx_fn=None):
    from fastapi import HTTPException

    @app.get("/api/broker/presets")
    def presets():
        prof = load_profile()
        return {"presets": {**PRESETS, "wlasny": prof["custom"]}, "checked": CHECKED}

    @app.get("/api/broker/profile")
    def get_profile():
        prof = load_profile()
        accounts = {acc: {"broker": b, "name": (schedule(b, prof) or {}).get("name", b)}
                    for acc, b in prof["accounts"].items()}
        return {"accounts": accounts, "custom": prof["custom"]}

    @app.post("/api/broker/profile")
    def set_profile(update: ProfileUpdate):
        prof = load_profile()
        if update.accounts:
            for acc, b in update.accounts.items():
                if acc not in ACCOUNT_LABEL:
                    raise HTTPException(status_code=400, detail=f"Nieznany typ konta: {acc}")
                if b not in PRESETS and b != "wlasny":
                    raise HTTPException(status_code=400, detail=f"Nieznany broker: {b}")
                prof["accounts"][acc] = b
        if update.custom:
            c = prof["custom"]
            for key in ("name", "fx_pct", "free_monthly_eur"):
                if key in update.custom:
                    c[key] = update.custom[key]
            for market in ("gpw", "zagranica"):
                if isinstance(update.custom.get(market), dict):
                    c[market].update({k: v for k, v in update.custom[market].items()
                                      if k in ("pct", "min", "min_currency")})
            c["notes"] = ["Stawki wpisane ręcznie."]
        save_profile(prof)
        return get_profile()

    @app.post("/api/broker/compare")
    def compare(req: CostRequest):
        """Ten sam scenariusz policzony u wszystkich brokerów - posortowane od najtańszego."""
        if req.amount <= 0:
            raise HTTPException(status_code=400, detail="Kwota musi być większa od zera.")
        prof = load_profile()
        mine = set(prof["accounts"].values())
        rows = []
        for bid, sched in {**PRESETS, "wlasny": prof["custom"]}.items():
            if bid == "wlasny" and "wlasny" not in mine:
                continue
            cmp = entry_comparison(sched, req.amount, req.tranches, req.market, fx_fn)
            rows.append({"broker": bid, "name": sched["name"], "mine": bid in mine,
                         "source_kind": sched.get("source_kind"), **cmp})
        # Brokerzy z nieznanym kosztem przewalutowania idą na koniec. Ich suma jest
        # NIEPEŁNA, więc postawienie ich wyżej jako "tańszych" byłoby wprowadzaniem w błąd.
        key = "dca" if req.tranches > 1 else "lump"
        rows.sort(key=lambda r: (r[key]["fx_unknown"], r[key]["total"]))
        return {"amount": req.amount, "tranches": req.tranches, "market": req.market, "rows": rows}

    @app.get("/api/broker/prompt-preview")
    def prompt_preview():
        """Podgląd tego, co model dostaje o Twoich kosztach - do sprawdzenia."""
        return {"block": broker_prompt_block(fx_fn)}

    logger.info("Koszty maklerskie gotowe (tabele sprawdzone %s).", CHECKED)
