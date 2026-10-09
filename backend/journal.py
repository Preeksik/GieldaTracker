"""
HossaLab - Dziennik porad AI.

CO ZAPISUJE:
Każdą poradę z datą i godziną, pytaniem, profilem (horyzont, ryzyko, kwota), modelem,
który odpowiedział, pełną treścią - oraz CENĄ każdej wymienionej spółki z dnia porady
i poziomem WIG20 i S&P 500 z tego samego momentu.

PO CO CENY:
Żeby po tygodniach dało się sprawdzić, jak porada wypadła - i to na tle rynku.
"+6% od porady" nic nie znaczy, jeśli WIG20 w tym czasie urósł o 9%.

DLACZEGO ZAPIS JEST AUTOMATYCZNY:
Gdyby zapisywać ręcznie, zostawałyby w dzienniku głównie porady, które się
sprawdziły - a te chybione szybko by znikały z pamięci. Taki bilans byłby
fałszywie optymistyczny. Dlatego zapisuje się wszystko, a usunąć można ręcznie.

Podpięcie na końcu main.py:
    from journal import setup_journal, journal_save, journal_append
    setup_journal(app, price_fn=get_current_price, snapshot_fn=get_index_snapshot, fx_fn=...)
"""

import os
import json
import time
import uuid
import logging
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

logger = logging.getLogger("gpw-api")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOURNAL_FILE = os.path.join(BASE_DIR, "advice_journal.json")

BENCHMARKS = [
    {"key": "wig20", "label": "WIG20", "tickers": ["WIG20.WA", "^WIG20"]},
    {"key": "sp500", "label": "S&P 500", "tickers": ["^GSPC"]},
]

# Po ilu dniach od porady da się ją sensownie ocenić. Wcześniej wynik to szum -
# porada krótkoterminowa po 3 dniach, a długoterminowa po miesiącu nic nie mówią.
HORIZON_DAYS = {"krotki": 60, "sredni": 365, "dlugi": 1095, None: 90}
HORIZON_LABEL = {"krotki": "krótki", "sredni": "średni", "dlugi": "długi"}

SOURCES = {"doradca": "Doradca", "spolka": "Analiza spółki", "portfel": "Przegląd portfela", "pozycja": "Analiza pozycji"}

PRICE_TTL = 600

# Kierunek zalecenia. Porada "SPRZEDAJ" jest trafna, gdy kurs SPADŁ - liczenie jej jak
# zakupu zamieniałoby trafne ostrzeżenia w "chybione" porady i odwrotnie.
#   kup / trzymaj   -> wynik = zmiana kursu
#   sprzedaj / unikaj -> wynik = zmiana kursu ze znakiem minus (strata, której uniknąłeś)
#   neutralnie      -> nie liczy się do bilansu (brak zalecenia)
# Brak zalecenia (stare wpisy) traktujemy jak "kup" - tak działały porady przed tą zmianą.
DIRECTION = {"kup": 1, "trzymaj": 1, None: 1, "sprzedaj": -1, "unikaj": -1, "neutralnie": 0}
STANCE_LABEL = {"kup": "kup", "trzymaj": "trzymaj", "sprzedaj": "sprzedaj", "unikaj": "unikaj",
                "neutralnie": "bez zalecenia"}

_lock = threading.RLock()
_entries = None
_price_cache = {}   # ticker -> (ts, cena)

# Wstrzykiwane przez setup_journal - potrzebne też funkcjom zapisu wołanym z innych modułów.
_deps = {"price_fn": None, "snapshot_fn": None, "model_fn": None, "fx_fn": None}


# ============================================================================
# PLIK
# ============================================================================

def _load():
    global _entries
    if _entries is None:
        try:
            with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            _entries = data if isinstance(data, list) else []
        except FileNotFoundError:
            _entries = []
        except json.JSONDecodeError:
            # Nie kasujemy po cichu - dziennik to dane, których nie da się odtworzyć.
            broken = JOURNAL_FILE + ".broken"
            try:
                os.replace(JOURNAL_FILE, broken)
            except OSError:
                pass
            logger.error("Uszkodzony %s - przeniesiony do %s", JOURNAL_FILE, broken)
            _entries = []
    return _entries


def _persist():
    tmp = JOURNAL_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_load(), f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, JOURNAL_FILE)


# ============================================================================
# CENY
# ============================================================================

def _price(ticker):
    fn = _deps["price_fn"]
    if not fn or not ticker:
        return None
    with _lock:
        hit = _price_cache.get(ticker)
    if hit and time.time() - hit[0] < PRICE_TTL:
        return hit[1]
    try:
        p = fn(ticker)
        p = float(p) if p is not None else None
    except Exception:
        p = None
    with _lock:
        _price_cache[ticker] = (time.time(), p)
    return p


def _benchmarks_now():
    fn = _deps["snapshot_fn"]
    out = {}
    if not fn:
        return out
    for b in BENCHMARKS:
        cached = _price_cache.get("__" + b["key"])
        if cached and time.time() - cached[0] < PRICE_TTL:
            out[b["key"]] = cached[1]
            continue
        try:
            level, _ = fn(b["tickers"])
        except Exception:
            level = None
        with _lock:
            _price_cache["__" + b["key"]] = (time.time(), level)
        out[b["key"]] = level
    return out


def _pct(then, now):
    try:
        if then and now:
            return round((float(now) / float(then) - 1) * 100, 2)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return None


# ============================================================================
# ZAPIS - wołane przez Doradcę i analizę spółki
# ============================================================================

def _amount_pln(t):
    """Kwota z porady przeliczona na PLN - wspólna miara do ważenia wyników."""
    if t.get("amount_pln"):
        return float(t["amount_pln"])
    amount = t.get("amount")
    if not amount:
        return None
    cur = (t.get("amount_currency") or "PLN").upper()
    if cur == "PLN":
        return round(float(amount), 2)
    fx = None
    if _deps["fx_fn"]:
        try:
            fx = _deps["fx_fn"](cur)
        except Exception:
            fx = None
    return round(float(amount) * fx, 2) if fx else None


def _ticker_row(t, added_at=None):
    row = {"ticker": t.get("ticker"), "name": t.get("name"), "price": t.get("price"),
           "currency": t.get("currency"), "stance": t.get("stance"), "amount_pln": _amount_pln(t)}
    if added_at:
        row["added_at"] = added_at
    return row


def journal_save(source, question, answer, tickers=None, brief=None):
    """
    tickers: [{"ticker", "name", "price", "currency", "stance"?, "amount"?, "amount_currency"?,
               "amount_pln"?}] - ceny Z CHWILI PORADY. stance: kup/trzymaj/sprzedaj/unikaj/neutralnie.
    Zwraca id wpisu albo None, gdy zapis się nie udał (porada i tak trafia do
    użytkownika - dziennik nie może zepsuć odpowiedzi).
    """
    try:
        entry = {
            "id": uuid.uuid4().hex[:12],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "question": (question or "").strip(),
            "answer": answer or "",
            "brief": brief or {},
            "model": None,
            "tickers": [_ticker_row(t) for t in (tickers or []) if t.get("ticker") and t.get("price")],
            "benchmarks": _benchmarks_now(),
            "followups": [],
            "note": "",
            "outcome": None,        # trafiona | chybiona | czesciowo | None
            "pinned": False,
        }
        if _deps["model_fn"]:
            try:
                entry["model"] = _deps["model_fn"]()
            except Exception:
                pass
        with _lock:
            items = _load()
            items.insert(0, entry)
            try:
                _persist()
            except Exception:
                # Wycofujemy wpis z pamięci - inaczej byłby widoczny do restartu,
                # a potem by zniknął, bo na dysk nigdy nie trafił.
                items.remove(entry)
                raise
        return entry["id"]
    except Exception:
        logger.exception("Nie udało się zapisać porady do dziennika")
        return None


def journal_append(entry_id, question, answer, tickers=None):
    """Dopytanie w tej samej rozmowie trafia do tego samego wpisu, a nie jako nowa porada."""
    try:
        with _lock:
            e = next((x for x in _load() if x["id"] == entry_id), None)
            if not e:
                return None
            fu = {"at": datetime.now(timezone.utc).isoformat(), "question": question, "answer": answer}
            before_fu, before_t = len(e["followups"]), len(e["tickers"])
            e["followups"].append(fu)
            known = {t["ticker"] for t in e["tickers"]}
            for t in tickers or []:
                if t.get("ticker") and t.get("price") and t["ticker"] not in known:
                    e["tickers"].append(_ticker_row(t, added_at=fu["at"]))
            try:
                _persist()
            except Exception:
                del e["followups"][before_fu:]
                del e["tickers"][before_t:]
                raise
        return entry_id
    except Exception:
        logger.exception("Nie udało się dopisać dopytania do dziennika")
        return None


# ============================================================================
# WYNIK OD PORADY
# ============================================================================

def _weighted(pairs):
    """
    Średnia ważona kwotami z porady. Pozycja bez kwoty (np. ostrzeżenie UNIKAJ - przed czymś,
    czego nie kupujesz, nie ma kwoty) dostaje wagę równą średniej kwocie pozostałych, żeby
    jedno ostrzeżenie nie wyłączało ważenia całej porady. Bez żadnych kwot: zwykła średnia.
    """
    pairs = [(v, w) for v, w in pairs if v is not None]
    if not pairs:
        return None, False
    known = [w for _, w in pairs if w and w > 0]
    if not known:
        return round(sum(v for v, _ in pairs) / len(pairs), 2), False
    fill = sum(known) / len(known)
    weights = [(v, w if (w and w > 0) else fill) for v, w in pairs]
    total = sum(w for _, w in weights)
    return round(sum(v * w for v, w in weights) / total, 2), True


def evaluate(entry):
    """
    Wynik porady od jej dnia: każda spółka osobno (z kierunkiem zalecenia), średnia
    ważona kwotami i porównanie z rynkiem. Rynek odniesienia dobieramy do spółki:
    WIG20 dla GPW, S&P 500 dla reszty.
    """
    created = datetime.fromisoformat(entry["created_at"])
    days = (datetime.now(timezone.utc) - created).days
    horizon = (entry.get("brief") or {}).get("horizon")
    needed = HORIZON_DAYS.get(horizon, HORIZON_DAYS[None])

    tickers = entry.get("tickers") or []
    with ThreadPoolExecutor(max_workers=4) as pool:
        now_prices = list(pool.map(lambda t: _price(t["ticker"]), tickers))

    bench_now = _benchmarks_now()
    bench = {}
    for b in BENCHMARKS:
        then = (entry.get("benchmarks") or {}).get(b["key"])
        bench[b["key"]] = {"label": b["label"], "then": then, "now": bench_now.get(b["key"]),
                           "change_pct": _pct(then, bench_now.get(b["key"]))}

    rows = []
    for t, now in zip(tickers, now_prices):
        change = _pct(t.get("price"), now)
        direction = DIRECTION.get(t.get("stance"), 1)
        ref_key = "wig20" if str(t["ticker"]).endswith(".WA") else "sp500"
        ref_change = bench.get(ref_key, {}).get("change_pct")
        effect = round(change * direction, 2) if (change is not None and direction) else None
        vs = (round((change - ref_change) * direction, 2)
              if (change is not None and ref_change is not None and direction) else None)
        rows.append({**t, "now": now, "change_pct": change, "effect_pct": effect, "vs_market_pct": vs,
                     "reference": ref_key, "stance_label": STANCE_LABEL.get(t.get("stance"))})

    avg, weighted = _weighted([(r["effect_pct"], r.get("amount_pln")) for r in rows])
    vs_market, _ = _weighted([(r["vs_market_pct"], r.get("amount_pln")) for r in rows])

    # Wynik w złotówkach - tylko dla pozycji z kwotą.
    money_rows = [r for r in rows if r.get("amount_pln") and r["effect_pct"] is not None]
    money = None
    if money_rows:
        money = {
            "invested_pln": round(sum(r["amount_pln"] for r in money_rows), 2),
            "result_pln": round(sum(r["amount_pln"] * r["effect_pct"] / 100 for r in money_rows), 2),
            "vs_market_pln": round(sum(r["amount_pln"] * r["vs_market_pct"] / 100
                                       for r in money_rows if r["vs_market_pct"] is not None), 2),
        }

    all_gpw = tickers and all(str(t["ticker"]).endswith(".WA") for t in tickers)
    return {
        "days": days,
        "horizon_days": needed,
        "mature": days >= needed * 0.25,   # od 1/4 horyzontu wynik zaczyna cokolwiek mówić
        "rows": rows,
        "avg_change_pct": avg,             # wynik porady (z kierunkiem zalecenia)
        "weighted": weighted,
        "benchmarks": bench,
        "reference": "wig20" if all_gpw else "sp500",
        "vs_market_pct": vs_market,
        "money": money,
    }


# ============================================================================
# ENDPOINTY
# ============================================================================

class EntryUpdate(BaseModel):
    note: str | None = None
    outcome: str | None = None
    pinned: bool | None = None


def setup_journal(app, price_fn=None, snapshot_fn=None, model_fn=None, fx_fn=None):
    from fastapi import HTTPException

    _deps.update(price_fn=price_fn, snapshot_fn=snapshot_fn, model_fn=model_fn, fx_fn=fx_fn)

    @app.get("/api/journal")
    def list_entries(q: str = "", ticker: str = "", source: str = "", with_results: bool = True):
        """Lista porad, najnowsze pierwsze, przypięte na górze. Z wynikiem od dnia porady."""
        with _lock:
            items = [dict(e) for e in _load()]

        ql, tk = q.strip().lower(), ticker.strip().upper()
        if ql:
            items = [e for e in items if ql in (e["question"] + " " + e["answer"]).lower()
                     or any(ql in (t.get("name") or "").lower() for t in e["tickers"])]
        if tk:
            items = [e for e in items if any(t["ticker"] == tk for t in e["tickers"])]
        if source:
            items = [e for e in items if e["source"] == source]

        # Najnowsze pierwsze, a przypięte zawsze na górze (sort stabilny zachowuje kolejność dat).
        items.sort(key=lambda e: e["created_at"], reverse=True)
        items.sort(key=lambda e: not e.get("pinned"))

        if with_results:
            for e in items:
                e["result"] = evaluate(e)

        # Bilans: tylko porady, które dojrzały - reszta to szum.
        mature = [e for e in items if with_results and e["result"]["mature"]
                  and e["result"]["vs_market_pct"] is not None]
        def stats(group):
            return {
                "mature": len(group),
                "beat_market": sum(1 for e in group if e["result"]["vs_market_pct"] > 0),
                "avg_vs_market_pct": round(sum(e["result"]["vs_market_pct"] for e in group) / len(group), 2)
                if group else None,
            }

        # Bilans w złotówkach: porady z kwotami (Doradca, Portfel AI, analizy pozycji).
        # "Gdybyś zrobił dokładnie to, co radziło AI" - i ile to dało ponad zwykły indeks.
        money_entries = [e for e in mature if e["result"].get("money")]
        invested = sum(e["result"]["money"]["invested_pln"] for e in money_entries)
        money = {
            "entries": len(money_entries),
            "invested_pln": round(invested, 2),
            "result_pln": round(sum(e["result"]["money"]["result_pln"] for e in money_entries), 2),
            "vs_market_pln": round(sum(e["result"]["money"]["vs_market_pln"] for e in money_entries), 2),
        } if money_entries else None
        if money and invested:
            money["vs_market_pct"] = round(money["vs_market_pln"] / invested * 100, 2)

        summary = {
            "total": len(items),
            **stats(mature),
            "money": money,
            "by_source": {k: stats([e for e in mature if e["source"] == k])
                          for k in SOURCES if any(e["source"] == k for e in mature)},
            "outcomes": {k: sum(1 for e in items if e.get("outcome") == k)
                         for k in ("trafiona", "czesciowo", "chybiona")},
        }
        return {"entries": items, "summary": summary, "sources": SOURCES}

    @app.patch("/api/journal/{entry_id}")
    def update_entry(entry_id: str, upd: EntryUpdate):
        with _lock:
            e = next((x for x in _load() if x["id"] == entry_id), None)
            if not e:
                raise HTTPException(status_code=404, detail="Nie ma takiego wpisu w dzienniku.")
            if upd.note is not None:
                e["note"] = upd.note[:4000]
            if upd.outcome is not None:
                if upd.outcome not in ("trafiona", "czesciowo", "chybiona", ""):
                    raise HTTPException(status_code=400, detail="Ocena: trafiona, czesciowo, chybiona albo pusta.")
                e["outcome"] = upd.outcome or None
            if upd.pinned is not None:
                e["pinned"] = upd.pinned
            _persist()
            return {"ok": True}

    @app.delete("/api/journal/{entry_id}")
    def delete_entry(entry_id: str):
        with _lock:
            items = _load()
            before = len(items)
            items[:] = [x for x in items if x["id"] != entry_id]
            if len(items) == before:
                raise HTTPException(status_code=404, detail="Nie ma takiego wpisu w dzienniku.")
            _persist()
        return {"ok": True}

    logger.info("Dziennik porad gotowy (%d wpisów).", len(_load()))