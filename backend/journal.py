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
    setup_journal(app, price_fn=get_current_price, snapshot_fn=get_index_snapshot)
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

SOURCES = {"doradca": "Doradca", "spolka": "Analiza spółki", "portfel": "Portfel AI", "pozycja": "Analiza pozycji"}

PRICE_TTL = 600

_lock = threading.RLock()
_entries = None
_price_cache = {}   # ticker -> (ts, cena)

# Wstrzykiwane przez setup_journal - potrzebne też funkcjom zapisu wołanym z innych modułów.
_deps = {"price_fn": None, "snapshot_fn": None, "model_fn": None}


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

def journal_save(source, question, answer, tickers=None, brief=None):
    """
    tickers: [{"ticker", "name", "price", "currency"}] - ceny Z CHWILI PORADY.
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
            "tickers": [
                {"ticker": t.get("ticker"), "name": t.get("name"),
                 "price": t.get("price"), "currency": t.get("currency")}
                for t in (tickers or []) if t.get("ticker") and t.get("price")
            ],
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
                    e["tickers"].append({"ticker": t["ticker"], "name": t.get("name"),
                                         "price": t["price"], "currency": t.get("currency"),
                                         "added_at": fu["at"]})
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

def evaluate(entry):
    """Wynik każdej spółki od dnia porady, średnia i porównanie z rynkiem."""
    created = datetime.fromisoformat(entry["created_at"])
    days = (datetime.now(timezone.utc) - created).days
    horizon = (entry.get("brief") or {}).get("horizon")
    needed = HORIZON_DAYS.get(horizon, HORIZON_DAYS[None])

    tickers = entry.get("tickers") or []
    with ThreadPoolExecutor(max_workers=4) as pool:
        now_prices = list(pool.map(lambda t: _price(t["ticker"]), tickers))

    rows = []
    for t, now in zip(tickers, now_prices):
        rows.append({**t, "now": now, "change_pct": _pct(t.get("price"), now)})

    changes = [r["change_pct"] for r in rows if r["change_pct"] is not None]
    avg = round(sum(changes) / len(changes), 2) if changes else None

    bench_now = _benchmarks_now()
    bench = {}
    for b in BENCHMARKS:
        then = (entry.get("benchmarks") or {}).get(b["key"])
        bench[b["key"]] = {"label": b["label"], "then": then, "now": bench_now.get(b["key"]),
                           "change_pct": _pct(then, bench_now.get(b["key"]))}

    # Rynek odniesienia: WIG20 dla porad o samych polskich spółkach, S&P 500 w pozostałych.
    all_gpw = tickers and all(str(t["ticker"]).endswith(".WA") for t in tickers)
    ref_key = "wig20" if all_gpw else "sp500"
    ref = bench.get(ref_key, {})
    vs_market = round(avg - ref["change_pct"], 2) if (avg is not None and ref.get("change_pct") is not None) else None

    return {
        "days": days,
        "horizon_days": needed,
        "mature": days >= needed * 0.25,   # od 1/4 horyzontu wynik zaczyna cokolwiek mówić
        "rows": rows,
        "avg_change_pct": avg,
        "benchmarks": bench,
        "reference": ref_key,
        "vs_market_pct": vs_market,
    }


# ============================================================================
# ENDPOINTY
# ============================================================================

class EntryUpdate(BaseModel):
    note: str | None = None
    outcome: str | None = None
    pinned: bool | None = None


def setup_journal(app, price_fn=None, snapshot_fn=None, model_fn=None):
    from fastapi import HTTPException

    _deps.update(price_fn=price_fn, snapshot_fn=snapshot_fn, model_fn=model_fn)

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
        summary = {
            "total": len(items),
            "mature": len(mature),
            "beat_market": sum(1 for e in mature if e["result"]["vs_market_pct"] > 0),
            "avg_vs_market_pct": round(sum(e["result"]["vs_market_pct"] for e in mature) / len(mature), 2)
            if mature else None,
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
