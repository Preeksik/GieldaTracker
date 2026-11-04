import os
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from portfolio_clear import setup_portfolio_clear  # noqa: E402


def make_app(fail_snapshot=False):
    db = {"portfolio": [{"id": "1"}, {"id": "2"}], "sales": [{"id": "s"}], "history": [1, 2, 3], "snaps": []}

    def snap():
        if fail_snapshot:
            raise OSError("dysk pełny")
        db["snaps"].append({"portfolio": list(db["portfolio"]), "sales": list(db["sales"])})
        return "auto-przed-wyczyszczeniem-1.json"

    app = FastAPI()
    setup_portfolio_clear(app,
                          load_portfolio=lambda: db["portfolio"], save_portfolio=lambda e: db.update(portfolio=e),
                          load_sales=lambda: db["sales"], save_sales=lambda e: db.update(sales=e),
                          load_history=lambda: db["history"], save_history=lambda e: db.update(history=e),
                          snapshot_fn=snap)
    return TestClient(app), db


def test_preview():
    c, _ = make_app()
    assert c.get("/api/portfolio/clear/preview").json() == {"positions": 2, "sales": 1, "history_points": 3,
                                                           "confirm_word": "USUŃ"}


def test_requires_exact_word():
    c, db = make_app()
    for body in ({}, {"confirm": "tak"}, {"confirm": "usun"}):
        assert c.post("/api/portfolio/clear", json=body).status_code == 400
    assert len(db["portfolio"]) == 2 and not db["snaps"]


def test_clears_everything_after_backup():
    c, db = make_app()
    r = c.post("/api/portfolio/clear", json={"confirm": "USUŃ"}).json()
    assert db["portfolio"] == [] and db["sales"] == [] and db["history"] == []
    assert r["removed"] == {"positions": 2, "sales": 1, "history_points": 3}
    assert db["snaps"][0]["portfolio"] == [{"id": "1"}, {"id": "2"}]      # kopia zawiera stan sprzed usunięcia
    assert r["safety_snapshot"].startswith("auto-")


def test_positions_only():
    c, db = make_app()
    c.post("/api/portfolio/clear", json={"confirm": "usuń", "include_sales": False, "include_history": False})
    assert db["portfolio"] == [] and db["sales"] == [{"id": "s"}] and db["history"] == [1, 2, 3]


def test_nothing_deleted_when_backup_fails():
    c, db = make_app(fail_snapshot=True)
    assert c.post("/api/portfolio/clear", json={"confirm": "USUŃ"}).status_code == 500
    assert len(db["portfolio"]) == 2
