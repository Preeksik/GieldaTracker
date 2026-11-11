"""Lusterko inwestora: łączenie częściowych zleceń, otwarte straty, wnioski z progów, pomysły wg podstawy."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mirror as M  # noqa: E402


def sale(t, buy, sell, profit, cost, acc="zwykle", q=1):
    return {"ticker": t, "name": t, "account": acc, "buy_date": buy, "sell_date": sell,
            "realized_profit_pln": profit, "cost_pln": cost, "quantity": q}


# zyski: szybko i małe; straty: długo i duże - klasyczny błąd
SALES = [sale("AAA.WA", "2026-01-01", "2026-01-11", 50, 1000) for _ in range(1)] + [
    sale("BBB.WA", "2026-02-01", "2026-02-11", 30, 300), sale("BBB.WA", "2026-02-01", "2026-02-11", 20, 200),  # 1 zlecenie w 2 częściach
    sale("CCC.WA", "2026-03-01", "2026-03-21", 40, 1000),
    sale("DDD", "2026-01-01", "2026-05-01", -150, 1000),
    sale("EEE.WA", "2026-01-01", "2026-04-01", -120, 1000, acc="ike"),
]
POSITIONS = [
    {"ticker": "FFF.WA", "name": "F", "account": "zwykle", "cost": 1000, "value": 800, "buy_date": "2026-01-10"},
    {"ticker": "FFF.WA", "name": "F", "account": "zwykle", "cost": 1000, "value": 900, "buy_date": "2026-09-10"},
    {"ticker": "GGG.WA", "name": "G", "account": "ike", "cost": 500, "value": 600, "buy_date": "2026-01-10"},
    {"ticker": "HHH", "name": "H", "account": "zwykle", "cost": 500, "value": None, "buy_date": "2026-01-10"},
]
TODAY = date(2026, 11, 11)


def test_partial_fills_are_one_decision():
    trades = M.closed_trades(SALES)
    assert len(trades) == 5
    bbb = next(t for t in trades if t["ticker"] == "BBB.WA")
    assert bbb["parts"] == 2 and bbb["profit_pln"] == 50 and bbb["return_pct"] == 10.0 and bbb["days"] == 10


def test_stats_and_habits():
    r = M.compute(SALES, POSITIONS, today=TODAY)
    s = r["summary"]
    assert s["count"] == 5 and s["wins"] == 3 and s["losses"] == 2 and s["win_rate"] == 60
    assert s["median_days_win"] == 10 and s["median_days_loss"] == 105
    assert s["avg_win_pct"] == round((5 + 10 + 4) / 3, 2) and s["avg_loss_pct"] == -13.5
    assert s["profit_factor"] == round(140 / 270, 2) and s["small_sample"]
    keys = {h["key"] for h in r["habits"]}
    assert {"hold_losers", "big_losses", "open_losers"} <= keys
    assert r["by_market"]["GPW"]["count"] == 4 and r["by_market"]["zagranica"]["count"] == 1
    assert [b["count"] for b in r["by_hold"]] == [3, 2, 0]
    assert r["biggest_losses"][0]["ticker"] == "DDD"


def test_open_losers_grouped_and_weighted():
    losers = M.open_losers(POSITIONS, TODAY.isoformat())
    assert [x["ticker"] for x in losers] == ["FFF.WA"]            # GGG na plusie, HHH bez wyceny
    f = losers[0]
    assert f["profit_pln"] == -300 and f["return_pct"] == -15.0
    assert f["days"] == round(((TODAY - date(2026, 1, 10)).days + (TODAY - date(2026, 9, 10)).days) / 2)


def test_good_habits_are_recognized():
    good = [sale("A.WA", "2026-01-01", "2026-06-01", 200, 1000), sale("B.WA", "2026-01-01", "2026-07-01", 150, 1000),
            sale("C.WA", "2026-01-01", "2026-01-08", -30, 1000)]
    keys = {h["key"] for h in M.compute(good, [], today=TODAY)["habits"]}
    assert keys == {"cut_losers", "small_losses"}


def test_ideas_by_basis_only_finished():
    entries = [
        {"source": "moje", "final": {"at": "x"}, "brief": {"basis": "raport"}, "r": (5.0, 2.0)},
        {"source": "moje", "final": {"at": "x"}, "brief": {"basis": "raport"}, "r": (-3.0, -4.0)},
        {"source": "moje", "final": {"at": "x"}, "brief": {"basis": "przeczucie"}, "r": (1.0, -1.0)},
        {"source": "moje", "brief": {"basis": "news"}, "r": (9.0, 9.0)},                 # jeszcze trwa
        {"source": "doradca", "final": {"at": "x"}, "r": (9.0, 9.0)},                    # porada AI
    ]
    ev = lambda e: {"avg_change_pct": e["r"][0], "vs_market_pct": e["r"][1]}
    out = {g["basis"]: g for g in M.ideas_by_basis(entries, ev)}
    assert set(out) == {"raport", "przeczucie"}
    assert out["raport"]["count"] == 2 and out["raport"]["hit"] == 1 and out["raport"]["beat_market"] == 1
    assert out["raport"]["avg_vs_market_pct"] == -1.0


def test_empty_history():
    r = M.compute([], [], today=TODAY)
    assert r["summary"]["count"] == 0 and r["habits"] == [] and r["open_losers"] == []
