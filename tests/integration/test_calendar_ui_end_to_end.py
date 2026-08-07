"""
§16.4: End-to-end test — UI HTTP route writes → overlay reads.

Writes via Flask test client HTTP POST, then reads the same SQLite DB
with the real MacroLockoutOverlay.check(). No mocks. Proves the UI's
writes are visible to the overlay at runtime.
"""
import sqlite3
import pytest
import jwt as pyjwt
import datetime as dt
from datetime import datetime, timezone, timedelta

from flask import Flask
from bot.calendar_ui.routes import calendar_bp, init_calendar_routes
from bot.calendar_ui.db import ensure_tables, list_events, get_audit_log
from bot.overlays.macro_lockout import MacroLockoutOverlay

JWT_SECRET = "e2e-test-secret"


def _today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _token(username="e2euser"):
    return pyjwt.encode(
        {"sub": username, "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)},
        JWT_SECRET, algorithm="HS256",
    )


def _auth():
    return {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}


@pytest.fixture
def setup(tmp_path):
    db_path = str(tmp_path / "regime.db")
    app = Flask(__name__)
    app.register_blueprint(calendar_bp)
    init_calendar_routes(db_path, JWT_SECRET)
    app.config["TESTING"] = True
    client = app.test_client()
    return client, db_path


def _read_overlay_from_db(db_path: str, now: datetime) -> list:
    """Read macro_events from the same DB and build ctx for overlay check."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM macro_events WHERE event_date >= ? ORDER BY event_date",
        (_today_str(),),
    ).fetchall()
    conn.close()

    macro_events = []
    for row in rows:
        r = dict(row)
        if r["event_time"]:
            event_dt_str = f"{r['event_date']}T{r['event_time']}:00"
            macro_events.append({
                "name": r["name"],
                "event_date": r["event_date"],
                "event_time": datetime.fromisoformat(event_dt_str).replace(
                    tzinfo=timezone.utc),
            })
        else:
            macro_events.append({
                "name": r["name"],
                "event_date": r["event_date"],
            })
    return macro_events


class TestHTTPWriteOverlayRead:
    def test_http_create_visible_to_overlay(self, setup):
        """HTTP POST /api/calendar/macro → same SQLite → overlay sees it."""
        client, db_path = setup
        now = datetime.now(timezone.utc)
        event_time = (now + timedelta(minutes=30)).strftime("%H:%M")

        r = client.post("/api/calendar/macro", headers=_auth(), json={
            "date": _today_str(),
            "time_utc": event_time,
            "event": "FOMC_RATE_DECISION",
            "region": "US",
            "severity": "high",
        })
        assert r.status_code == 201, f"Create failed: {r.get_json()}"

        macro_events = _read_overlay_from_db(db_path, now)
        assert len(macro_events) >= 1, "Event not in DB after HTTP POST"

        overlay = MacroLockoutOverlay()
        check = overlay.check("AAPL", now, {"macro_events": macro_events})

        assert check.is_active is True, (
            f"INTEGRATION FAILURE: HTTP POST wrote to DB but overlay does not "
            f"see the event. DB path: {db_path}, events in DB: {macro_events}, "
            f"overlay check: is_active={check.is_active}, reason={check.reason}"
        )
        assert "FOMC" in check.reason

    def test_http_csv_import_visible_to_overlay(self, setup):
        """HTTP POST CSV import → same SQLite → overlay sees imported events."""
        client, db_path = setup
        now = datetime.now(timezone.utc)
        event_time = (now + timedelta(minutes=15)).strftime("%H:%M")

        csv_data = (
            f"date,time_utc,event,region,severity,notes\n"
            f"{_today_str()},{event_time},BOE_RATE_DECISION,UK,high,\n"
        )
        r = client.post("/api/calendar/macro/import",
                        data=csv_data, content_type="text/csv",
                        headers=_auth())
        assert r.status_code == 200
        data = r.get_json()
        assert data["inserted"] == 1, f"CSV import failed: {data}"

        macro_events = _read_overlay_from_db(db_path, now)
        overlay = MacroLockoutOverlay()
        check = overlay.check("MSFT", now, {"macro_events": macro_events})

        assert check.is_active is True, (
            f"INTEGRATION FAILURE: CSV import wrote to DB but overlay does not "
            f"see the event. events in DB: {macro_events}"
        )

    def test_http_delete_removes_from_overlay(self, setup):
        """HTTP DELETE → event gone from same SQLite → overlay no longer sees it."""
        client, db_path = setup
        now = datetime.now(timezone.utc)
        event_time = (now + timedelta(minutes=30)).strftime("%H:%M")

        r = client.post("/api/calendar/macro", headers=_auth(), json={
            "date": _today_str(),
            "time_utc": event_time,
            "event": "FOMC_RATE_DECISION",
            "region": "US",
            "severity": "high",
        })
        event_id = r.get_json()["id"]

        headers = {**_auth(), "X-Confirm-Event": "FOMC_RATE_DECISION"}
        r = client.delete(f"/api/calendar/macro/{event_id}", headers=headers)
        assert r.status_code == 200

        macro_events = _read_overlay_from_db(db_path, now)
        assert len(macro_events) == 0

        overlay = MacroLockoutOverlay()
        check = overlay.check("AAPL", now, {"macro_events": macro_events})
        assert check.is_active is False


class TestFullCRUDCycleHTTP:
    def test_create_edit_delete_via_http(self, setup):
        """Full CRUD cycle through HTTP routes with audit verification."""
        client, db_path = setup

        r = client.post("/api/calendar/macro", headers=_auth(), json={
            "date": "2026-07-01",
            "time_utc": "14:00",
            "event": "FOMC_RATE_DECISION",
            "region": "US",
            "severity": "high",
        })
        assert r.status_code == 201
        event_id = r.get_json()["id"]

        r = client.put(f"/api/calendar/macro/{event_id}", headers=_auth(), json={
            "date": "2026-07-01",
            "time_utc": "14:30",
            "event": "FOMC_RATE_DECISION",
            "region": "US",
            "severity": "medium",
        })
        assert r.status_code == 200
        assert r.get_json()["event_time"] == "14:30"

        headers = {**_auth(), "X-Confirm-Event": "FOMC_RATE_DECISION"}
        r = client.delete(f"/api/calendar/macro/{event_id}", headers=headers)
        assert r.status_code == 200

        r = client.get(f"/api/calendar/macro/{event_id}", headers=_auth())
        assert r.status_code == 404

        audit = get_audit_log(db_path)
        actions = [a["action"] for a in audit]
        assert "create" in actions
        assert "update" in actions
        assert "delete" in actions
        assert all(a["user_jwt_sub"] == "e2euser" for a in audit)
