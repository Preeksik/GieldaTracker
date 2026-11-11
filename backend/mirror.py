"""
HossaLab - "Lusterko inwestora": Twoje nawyki policzone z Twojej historii. Bez AI - sama matematyka.

ŹRÓDŁA:
  - zamknięte transakcje (sales.json) - częściowe zlecenia XTB (ten sam papier, konto, dzień kupna
    i sprzedaży) łączymy w jedną decyzję, inaczej jedna sprzedaż liczyłaby się jak trzy,
  - OTWARTE pozycje na minusie - bez nich statystyka byłaby za ładna: strata, którą "kisisz",
    z definicji nie ma jeszcze sprzedaży,
  - "Moje pomysły" po terminie (Dziennik porad) - z podziałem na to, na czym się opierały.

CZEGO SZUKAMY (klasyczne błędy):
  - małe zyski zamykane szybko, a duże straty trzymane długo ("tniesz zyski, trzymasz straty"),
  - średnia strata większa niż średni zysk przy niewielkiej przewadze trafień.

Wnioski liczy kod według stałych progów (THRESHOLDS) - to fakty z Twojej historii, nie rady.
Przy małej liczbie transakcji wynik to w dużej mierze przypadek - panel mówi to wprost.

Endpoint: GET /api/mirror
"""

import logging
import statistics as st
from datetime import date

logger = logging.getLogger("gpw-api")

MIN_SAMPLE = 10            # poniżej tylu transakcji w grupie - "za mało, żeby wyciągać wnioski"
HOLD_BUCKETS = [("do 1 miesiąca", 0, 30), ("1-6 miesięcy", 31, 182), ("ponad pół roku", 183, 10 ** 6)]
THRESHOLDS = {
    "hold_ratio": 1.3,     # straty trzymane o 30%+ dłużej niż zyski
    "size_ratio": 1.2,     # średnia strata (w %) o 20%+ większa niż średni zysk
}
BASIS = {"raport": "raport finansowy", "insider": "transakcje insiderów", "news": "news / wydarzenie",
         "wykres": "wykres / analiza techniczna", "przeczucie": "przeczucie", "inne": "inne"}


def _days(a, b):
    try:
        return max(0, (date.fromisoformat(str(b)[:10]) - date.fromisoformat(str(a)[:10])).days)
    except (TypeError, ValueError):
        return None


def _r(x, n=2):
    return None if x is None else round(x, n)


def market_of(ticker):
    return "GPW" if str(ticker or "").upper().endswith(".WA") else "zagranica"


def closed_trades(sales):
    """Zamknięte transakcje po złączeniu częściowych zleceń."""
    groups = {}
    for s in sales or []:
        k = (s.get("ticker"), (s.get("account") or "zwykle"), s.get("buy_date"), s.get("sell_date"))
        g = groups.setdefault(k, {"ticker": k[0], "name": s.get("name") or k[0], "account": k[1],
                                  "buy_date": k[2], "sell_date": k[3], "profit_pln": 0.0, "cost_pln": 0.0,
                                  "quantity": 0.0, "parts": 0})
        g["profit_pln"] += float(s.get("realized_profit_pln") or 0)
        g["cost_pln"] += float(s.get("cost_pln") or 0)
        g["quantity"] += float(s.get("quantity") or 0)
        g["parts"] += 1
    out = []
    for g in groups.values():
        g["days"] = _days(g["buy_date"], g["sell_date"])
        g["return_pct"] = _r(g["profit_pln"] / g["cost_pln"] * 100) if g["cost_pln"] else None
        g["market"] = market_of(g["ticker"])
        g["profit_pln"], g["cost_pln"] = _r(g["profit_pln"]), _r(g["cost_pln"])
        out.append(g)
    return sorted(out, key=lambda g: g["sell_date"] or "", reverse=True)


def stats(trades):
    """Podstawowe liczby dla grupy transakcji."""
    wins = [t for t in trades if (t["profit_pln"] or 0) > 0]
    losses = [t for t in trades if (t["profit_pln"] or 0) < 0]

    def avg(xs):
        xs = [x for x in xs if x is not None]
        return _r(st.mean(xs)) if xs else None

    def med(xs):
        xs = [x for x in xs if x is not None]
        return _r(st.median(xs), 0) if xs else None

    sum_w = sum(t["profit_pln"] for t in wins)
    sum_l = -sum(t["profit_pln"] for t in losses)
    return {
        "count": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": _r(len(wins) / len(trades) * 100, 0) if trades else None,
        "total_pln": _r(sum(t["profit_pln"] or 0 for t in trades)),
        "avg_win_pln": avg(t["profit_pln"] for t in wins), "avg_loss_pln": avg(t["profit_pln"] for t in losses),
        "avg_win_pct": avg(t["return_pct"] for t in wins), "avg_loss_pct": avg(t["return_pct"] for t in losses),
        "median_days_win": med(t["days"] for t in wins), "median_days_loss": med(t["days"] for t in losses),
        # ile zarabiasz na każdą złotówkę straty (powyżej 1 = zyski przeważają nad stratami)
        "profit_factor": _r(sum_w / sum_l) if sum_l > 0 else None,
        "small_sample": len(trades) < MIN_SAMPLE,
    }


def open_losers(positions, today):
    """Otwarte pozycje na minusie, zebrane po spółce i koncie (kilka transz = jedna pozycja)."""
    groups = {}
    for p in positions or []:
        k = (p.get("ticker"), p.get("account") or "zwykle")
        g = groups.setdefault(k, {"ticker": k[0], "name": p.get("name") or k[0], "account": k[1],
                                  "cost_pln": 0.0, "value_pln": 0.0, "first_buy": p.get("buy_date"), "_w": 0.0})
        cost, value = float(p.get("cost") or 0), p.get("value")
        if value is None:
            g["unknown"] = True
            continue
        g["cost_pln"] += cost
        g["value_pln"] += float(value)
        d = _days(p.get("buy_date"), today)
        if d is not None:
            g["_w"] += d * cost
        if (p.get("buy_date") or "9") < (g["first_buy"] or "9"):
            g["first_buy"] = p.get("buy_date")
    out = []
    for g in groups.values():
        if g.get("unknown") or g["cost_pln"] <= 0:
            continue
        profit = g["value_pln"] - g["cost_pln"]
        if profit >= 0:
            continue
        out.append({"ticker": g["ticker"], "name": g["name"], "account": g["account"], "market": market_of(g["ticker"]),
                    "cost_pln": _r(g["cost_pln"]), "value_pln": _r(g["value_pln"]), "profit_pln": _r(profit),
                    "return_pct": _r(profit / g["cost_pln"] * 100),
                    "days": int(round(g["_w"] / g["cost_pln"])),        # średnio ważony kosztem czas trzymania
                    "first_buy": g["first_buy"]})
    return sorted(out, key=lambda x: x["profit_pln"])


def habits(all_stats, losers, winners_median_days):
    """Wnioski liczone stałymi progami - fakty z historii, nie porady."""
    out = []
    s = all_stats
    if s["count"] == 0:
        return out
    if s["median_days_win"] is not None and s["median_days_loss"] is not None and s["median_days_win"] > 0:
        ratio = s["median_days_loss"] / s["median_days_win"]
        if ratio >= THRESHOLDS["hold_ratio"]:
            out.append({"kind": "warn", "key": "hold_losers",
                        "text": f"Straty trzymasz dłużej niż zyski: typowo {s['median_days_loss']:.0f} dni wobec "
                                f"{s['median_days_win']:.0f} dni dla zysków (≈{ratio:.1f}×)."})
        elif ratio <= 1 / THRESHOLDS["hold_ratio"]:
            out.append({"kind": "good", "key": "cut_losers",
                        "text": f"Straty zamykasz szybciej niż zyski ({s['median_days_loss']:.0f} vs "
                                f"{s['median_days_win']:.0f} dni) - to zdrowy nawyk."})
    if s["avg_win_pct"] and s["avg_loss_pct"]:
        size = abs(s["avg_loss_pct"]) / s["avg_win_pct"]
        if size >= THRESHOLDS["size_ratio"]:
            out.append({"kind": "warn", "key": "big_losses",
                        "text": f"Średnia strata ({s['avg_loss_pct']:.1f}%) jest większa niż średni zysk "
                                f"(+{s['avg_win_pct']:.1f}%). Wygrywasz częściej ({s['win_rate']:.0f}% transakcji), "
                                f"ale pojedyncza strata kosztuje więcej niż daje pojedynczy zysk."})
        elif size <= 1 / THRESHOLDS["size_ratio"]:
            out.append({"kind": "good", "key": "small_losses",
                        "text": f"Średnia strata ({s['avg_loss_pct']:.1f}%) jest mniejsza niż średni zysk "
                                f"(+{s['avg_win_pct']:.1f}%)."})
    if winners_median_days and losers:
        old = [x for x in losers if x["days"] > winners_median_days]
        if old:
            out.append({"kind": "warn", "key": "open_losers",
                        "text": f"Masz {len(old)} {'otwartą pozycję' if len(old) == 1 else 'otwarte pozycje'} na minusie "
                                f"trzymane dłużej niż typowy zysk ({winners_median_days:.0f} dni): "
                                + ", ".join(f"{x['ticker']} {x['return_pct']:+.1f}%" for x in old[:5])
                                + ("…" if len(old) > 5 else "") + "."})
    return out


def ideas_by_basis(entries, evaluate_fn):
    """'Moje pomysły' po terminie, pogrupowane według tego, na czym się opierały."""
    groups = {}
    for e in entries or []:
        if e.get("source") != "moje" or not e.get("final"):
            continue
        try:
            r = evaluate_fn(e)
        except Exception:
            continue
        basis = (e.get("brief") or {}).get("basis") or "brak"
        g = groups.setdefault(basis, {"basis": basis, "label": BASIS.get(basis, "bez oznaczenia"),
                                      "count": 0, "hit": 0, "beat_market": 0, "_vs": []})
        g["count"] += 1
        g["hit"] += int((r.get("avg_change_pct") or 0) > 0)
        if r.get("vs_market_pct") is not None:
            g["beat_market"] += int(r["vs_market_pct"] > 0)
            g["_vs"].append(r["vs_market_pct"])
    out = []
    for g in groups.values():
        vs = g.pop("_vs")
        g["avg_vs_market_pct"] = _r(st.mean(vs)) if vs else None
        g["small_sample"] = g["count"] < MIN_SAMPLE
        out.append(g)
    return sorted(out, key=lambda g: -g["count"])


def compute(sales, positions, journal_entries=None, evaluate_fn=None, today=None):
    today = (today or date.today()).isoformat()
    trades = closed_trades(sales)
    s_all = stats(trades)
    losers = open_losers(positions, today)
    by_market = {m: stats([t for t in trades if t["market"] == m]) for m in ("GPW", "zagranica")}
    by_hold = [{"label": lbl, **stats([t for t in trades if t["days"] is not None and lo <= t["days"] <= hi])}
               for lbl, lo, hi in HOLD_BUCKETS]
    by_account = {a: stats([t for t in trades if t["account"] == a])
                  for a in sorted({t["account"] for t in trades})}
    return {
        "as_of": today,
        "period": [min((t["sell_date"] for t in trades), default=None), max((t["sell_date"] for t in trades), default=None)],
        "raw_rows": len(sales or []),
        "summary": s_all,
        "habits": habits(s_all, losers, s_all["median_days_win"]),
        "by_market": by_market,
        "by_hold": by_hold,
        "by_account": by_account,
        "open_losers": losers,
        "open_losers_total_pln": _r(sum(x["profit_pln"] for x in losers)),
        "biggest_wins": sorted(trades, key=lambda t: -(t["profit_pln"] or 0))[:5],
        "biggest_losses": sorted(trades, key=lambda t: (t["profit_pln"] or 0))[:5],
        "ideas": ideas_by_basis(journal_entries, evaluate_fn) if evaluate_fn else [],
        "basis_labels": BASIS,
        "min_sample": MIN_SAMPLE,
    }


def setup_mirror(app, sales_fn, positions_fn, journal_entries_fn=None, evaluate_fn=None):
    """Funkcje przekazujemy z main.py jako lambdy (zawsze aktualne wersje)."""
    from fastapi import HTTPException

    @app.get("/api/mirror")
    def mirror():
        try:
            sales = sales_fn() or []
        except Exception:
            logger.exception("Lusterko: nie udało się wczytać sprzedaży")
            raise HTTPException(status_code=500, detail="Nie udało się wczytać historii sprzedaży.")
        try:
            positions = positions_fn() or []
        except Exception:
            logger.warning("Lusterko: brak wyceny otwartych pozycji - liczę tylko zamknięte transakcje")
            positions = []
        entries = []
        if journal_entries_fn:
            try:
                entries = journal_entries_fn() or []
            except Exception:
                entries = []
        return compute(sales, positions, entries, evaluate_fn)
