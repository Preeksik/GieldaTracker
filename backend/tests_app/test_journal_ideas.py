"""'Moje pomysły' w Dzienniku porad: zapis z ceną z chwili dodania, wynik zamrożony w dniu terminu, osobny bilans."""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import journal as J  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "JOURNAL_FILE", str(tmp_path / "advice_journal.json"))
    monkeypatch.setattr(J, "_entries", None)
    monkeypatch.setattr(J, "_price_cache", {})
    prices = {"CDR.WA": 100.0, "PKN.WA": 50.0}
    bench = {"wig20": 2000.0, "sp500": 5000.0}

    def snapshot(tickers):
        return (bench["wig20"] if "WIG20.WA" in tickers else bench["sp500"]), None

    app = FastAPI()
    J.setup_journal(app, price_fn=lambda t: prices.get(t), snapshot_fn=snapshot,
                    model_fn=lambda: "gemini-test", name_fn=lambda t: {"CDR.WA": "CD Projekt"}.get(t),
                    currency_fn=lambda t: "PLN")
    return TestClient(app), prices, bench


def age(entry_id, days):
    """Przesuwa datę wpisu w przeszłość (symulacja upływu czasu)."""
    e = next(x for x in J._load() if x["id"] == entry_id)
    e["created_at"] = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    J._price_cache.clear()


def test_idea_is_saved_with_price_from_now(env):
    c, _, _ = env
    r = c.post("/api/journal/idea", json={"ticker": "cdr.wa", "direction": "wzrost", "horizon": "1m",
                                          "reason": "premiera gry", "amount": 1000}).json()
    assert r["price"] == 100.0 and r["name"] == "CD Projekt"
    e = J._load()[0]
    assert e["source"] == "moje" and e["model"] is None                 # to Twój pomysł, nie AI
    assert e["tickers"][0]["stance"] == "kup" and e["tickers"][0]["amount_pln"] == 1000
    assert e["answer"] == "premiera gry" and e["brief"]["horizon"] == "1m"
    assert "wzrośnie" in e["question"]


def test_validation(env):
    c, _, _ = env
    assert c.post("/api/journal/idea", json={"ticker": "XXX", "direction": "wzrost"}).status_code == 400
    assert c.post("/api/journal/idea", json={"ticker": "CDR.WA", "direction": "bokiem"}).status_code == 400
    assert c.post("/api/journal/idea", json={"ticker": "CDR.WA", "direction": "wzrost", "horizon": "5l"}).status_code == 400
    assert J._load() == []


def test_result_freezes_at_deadline_and_counts_separately(env):
    c, prices, bench = env
    up = c.post("/api/journal/idea", json={"ticker": "CDR.WA", "direction": "wzrost", "horizon": "1m"}).json()["id"]
    down = c.post("/api/journal/idea", json={"ticker": "PKN.WA", "direction": "spadek", "horizon": "3m"}).json()["id"]
    J.journal_save("doradca", "pytanie do AI", "odpowiedź", tickers=[{"ticker": "PKN.WA", "price": 50.0}])

    # CDR po terminie: +10%, rynek +5% -> wynik zamrożony
    prices["CDR.WA"], bench["wig20"] = 110.0, 2100.0
    age(up, 31)
    data = c.get("/api/journal").json()
    idea = next(e for e in data["entries"] if e["id"] == up)
    assert idea["result"]["due"] and idea["result"]["final_at"]
    assert idea["result"]["avg_change_pct"] == 10.0 and idea["result"]["vs_market_pct"] == 5.0

    # później kurs spada - wynik pomysłu się NIE zmienia
    prices["CDR.WA"] = 80.0
    J._price_cache.clear()
    data = c.get("/api/journal").json()
    idea = next(e for e in data["entries"] if e["id"] == up)
    assert idea["result"]["avg_change_pct"] == 10.0

    s = data["summary"]
    assert s["total"] == 1                                        # tylko porada AI
    assert s["ideas"]["total"] == 2 and s["ideas"]["open"] == 1   # PKN jeszcze trwa
    assert s["ideas"]["mature"] == 1 and s["ideas"]["beat_market"] == 1 and s["ideas"]["hit"] == 1
    assert "moje" not in s["by_source"]
    pkn = next(e for e in data["entries"] if e["id"] == down)
    assert not pkn["result"]["due"] and pkn["result"]["final_at"] is None


def test_falling_idea_is_right_when_price_falls(env):
    c, prices, _ = env
    i = c.post("/api/journal/idea", json={"ticker": "PKN.WA", "direction": "spadek", "horizon": "1m"}).json()["id"]
    prices["PKN.WA"] = 45.0
    age(i, 30)
    e = next(x for x in c.get("/api/journal").json()["entries"] if x["id"] == i)
    assert e["result"]["avg_change_pct"] == 10.0                  # kurs -10% = pomysł "spadnie" trafiony


def test_no_freeze_without_price(env):
    c, prices, _ = env
    i = c.post("/api/journal/idea", json={"ticker": "CDR.WA", "direction": "wzrost", "horizon": "1m"}).json()["id"]
    age(i, 40)
    prices.pop("CDR.WA")                                          # Yahoo nie odpowiada
    assert J.finalize_due_ideas() == 0
    prices["CDR.WA"] = 120.0
    J._price_cache.clear()
    assert J.finalize_due_ideas() == 1
