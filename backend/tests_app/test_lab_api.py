import json
import os
import sys
import time
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lab_api as L  # noqa: E402


def make_app(tmp_path, game=True, refresh=None):
    lab = tmp_path / "lab"
    lab.mkdir()
    if game:
        (lab / "paper_journal.json").write_text(json.dumps({"sets": [{"strategy": "A1"}]}), encoding="utf-8")
    calls = []

    def fake_refresh(trigger):
        calls.append(trigger)
        (lab / "paper_status.json").write_text(json.dumps({"report": {"started": "2026-11-03"},
                                                           "last_ok": "2026-11-09T18:31:00"}), encoding="utf-8")

    app = FastAPI()
    L.setup_lab(app, refresh_fn=refresh or fake_refresh, lab_dir=str(lab))
    return TestClient(app), app, lab, calls


def wait_done(c):
    for _ in range(100):
        if not c.get("/api/lab/paper").json()["running"]:
            return
        time.sleep(0.02)


def test_laptop_without_game_is_blocked(tmp_path):
    c, app, lab, calls = make_app(tmp_path, game=False)
    d = c.get("/api/lab/paper").json()
    assert d["game"] is False and d["report"] is None
    r = c.post("/api/lab/paper/run")
    assert r.status_code == 409 and "stacjonarnym" in r.json()["detail"]
    assert app.state.lab_run_once("automat") is False and calls == []
    assert not (lab / "paper_journal.json").exists()


def test_button_runs_in_background_and_report_is_read(tmp_path):
    c, _, _, calls = make_app(tmp_path)
    assert c.post("/api/lab/paper/run").json() == {"started": True}
    wait_done(c)
    d = c.get("/api/lab/paper").json()
    assert calls == ["przycisk"] and d["report"] == {"started": "2026-11-03"} and d["error"] is None


def test_refuses_while_other_program_holds_lock(tmp_path):
    c, _, lab, calls = make_app(tmp_path)
    (lab / "paper.lock").write_text("terminal", encoding="utf-8")
    assert c.get("/api/lab/paper").json()["running"] is True
    assert c.post("/api/lab/paper/run").status_code == 409 and calls == []


def test_error_is_reported(tmp_path):
    def boom(trigger):
        raise RuntimeError("brak internetu")
    c, app, _, _ = make_app(tmp_path, refresh=boom)
    assert app.state.lab_run_once("automat") is False
    assert "brak internetu" in c.get("/api/lab/paper").json()["error"]


def test_schedule_slots():
    mon_19 = datetime(2026, 11, 9, 19, 0)       # poniedziałek po 18:30
    mon_12 = datetime(2026, 11, 9, 12, 0)
    sat = datetime(2026, 11, 14, 12, 0)
    assert L.last_slot(mon_19) == datetime(2026, 11, 9, 18, 30)
    assert L.last_slot(mon_12) == datetime(2026, 11, 6, 18, 30)      # piątek
    assert L.next_slot(sat) == datetime(2026, 11, 16, 18, 30)        # poniedziałek
    assert L.needs_catch_up(None, mon_19)
    assert L.needs_catch_up("2026-11-06T18:40:00", mon_19)            # piątek był, poniedziałku nie
    assert not L.needs_catch_up("2026-11-09T18:45:00", mon_19)
    assert not L.needs_catch_up("2026-11-13T18:45:00", sat)            # weekend - nic nie przepadło
