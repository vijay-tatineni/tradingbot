"""§15.4.4: Calendar UI HTTP route tests — macro endpoints."""
import json
import pytest
import jwt as pyjwt
import datetime

from flask import Flask
from bot.calendar_ui.routes import calendar_bp, init_calendar_routes
from bot.calendar_ui.db import ensure_tables, get_audit_log, list_events

JWT_SECRET = "test-secret-key"


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "test_cal.db")
    app = Flask(__name__)
    app.register_blueprint(calendar_bp)
    init_calendar_routes(db_path, JWT_SECRET)
    app.config["TESTING"] = True
    with app.test_client() as c:
        c._db_path = db_path
        yield c


def _token(username="testuser"):
    return pyjwt.encode(
        {"sub": username, "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1)},
        JWT_SECRET, algorithm="HS256",
    )


def _auth():
    return {"Authorization": f"Bearer {_token()}"}


def _event(**overrides):
    base = {
        "date": "2026-06-18",
        "time_utc": "18:00",
        "event": "FOMC_RATE_DECISION",
        "region": "US",
        "severity": "high",
        "notes": "",
    }
    base.update(overrides)
    return base


class TestAuth:
    def test_no_token_returns_401(self, client):
        r = client.get("/api/calendar/macro")
        assert r.status_code == 401

    def test_invalid_token_returns_401(self, client):
        r = client.get("/api/calendar/macro",
                       headers={"Authorization": "Bearer bad-token"})
        assert r.status_code == 401

    def test_valid_token_returns_200(self, client):
        r = client.get("/api/calendar/macro", headers=_auth())
        assert r.status_code == 200


class TestCreateMacro:
    def test_create_happy_path(self, client):
        r = client.post("/api/calendar/macro",
                        json=_event(), headers=_auth())
        assert r.status_code == 201
        data = r.get_json()
        assert data["name"] == "FOMC_RATE_DECISION"

    def test_create_validation_failure(self, client):
        r = client.post("/api/calendar/macro",
                        json={"date": "bad"}, headers=_auth())
        assert r.status_code == 400
        assert "validation_failed" in r.get_json()["error"]

    def test_create_duplicate_returns_409(self, client):
        client.post("/api/calendar/macro", json=_event(), headers=_auth())
        r = client.post("/api/calendar/macro", json=_event(), headers=_auth())
        assert r.status_code == 409

    def test_create_writes_audit(self, client):
        client.post("/api/calendar/macro", json=_event(), headers=_auth())
        audit = get_audit_log(client._db_path)
        assert len(audit) == 1
        assert audit[0]["action"] == "create"
        assert audit[0]["user_jwt_sub"] == "testuser"


class TestGetMacro:
    def test_get_existing(self, client):
        r = client.post("/api/calendar/macro", json=_event(), headers=_auth())
        eid = r.get_json()["id"]
        r = client.get(f"/api/calendar/macro/{eid}", headers=_auth())
        assert r.status_code == 200
        assert r.get_json()["name"] == "FOMC_RATE_DECISION"

    def test_get_nonexistent(self, client):
        r = client.get("/api/calendar/macro/9999", headers=_auth())
        assert r.status_code == 404


class TestListMacro:
    def test_list_all(self, client):
        client.post("/api/calendar/macro", json=_event(), headers=_auth())
        client.post("/api/calendar/macro",
                    json=_event(event="BOE_RATE_DECISION", region="UK"),
                    headers=_auth())
        r = client.get("/api/calendar/macro", headers=_auth())
        assert len(r.get_json()) == 2

    def test_filter_by_date(self, client):
        client.post("/api/calendar/macro",
                    json=_event(date="2026-06-01"), headers=_auth())
        client.post("/api/calendar/macro",
                    json=_event(date="2026-07-01", event="BOE_RATE_DECISION"),
                    headers=_auth())
        r = client.get("/api/calendar/macro?from=2026-06-15", headers=_auth())
        assert len(r.get_json()) == 1


class TestUpdateMacro:
    def test_update_happy_path(self, client):
        r = client.post("/api/calendar/macro", json=_event(), headers=_auth())
        eid = r.get_json()["id"]
        r = client.put(f"/api/calendar/macro/{eid}",
                       json=_event(severity="low"), headers=_auth())
        assert r.status_code == 200
        assert r.get_json()["impact"] == "low"

    def test_update_nonexistent(self, client):
        r = client.put("/api/calendar/macro/9999",
                       json=_event(), headers=_auth())
        assert r.status_code == 404

    def test_update_writes_audit(self, client):
        r = client.post("/api/calendar/macro", json=_event(), headers=_auth())
        eid = r.get_json()["id"]
        client.put(f"/api/calendar/macro/{eid}",
                   json=_event(severity="low"), headers=_auth())
        audit = get_audit_log(client._db_path, action="update")
        assert len(audit) == 1
        assert audit[0]["before_json"] is not None


class TestDeleteMacro:
    def test_delete_requires_confirmation(self, client):
        r = client.post("/api/calendar/macro", json=_event(), headers=_auth())
        eid = r.get_json()["id"]
        r = client.delete(f"/api/calendar/macro/{eid}", headers=_auth())
        assert r.status_code == 400
        assert "Confirmation required" in r.get_json()["error"]

    def test_delete_wrong_confirmation(self, client):
        r = client.post("/api/calendar/macro", json=_event(), headers=_auth())
        eid = r.get_json()["id"]
        headers = {**_auth(), "X-Confirm-Event": "WRONG_NAME"}
        r = client.delete(f"/api/calendar/macro/{eid}", headers=headers)
        assert r.status_code == 400

    def test_delete_correct_confirmation(self, client):
        r = client.post("/api/calendar/macro", json=_event(), headers=_auth())
        eid = r.get_json()["id"]
        headers = {**_auth(), "X-Confirm-Event": "FOMC_RATE_DECISION"}
        r = client.delete(f"/api/calendar/macro/{eid}", headers=headers)
        assert r.status_code == 200
        assert r.get_json()["deleted"] is True

    def test_delete_nonexistent(self, client):
        headers = {**_auth(), "X-Confirm-Event": "X"}
        r = client.delete("/api/calendar/macro/9999", headers=headers)
        assert r.status_code == 404

    def test_delete_writes_audit(self, client):
        r = client.post("/api/calendar/macro", json=_event(), headers=_auth())
        eid = r.get_json()["id"]
        headers = {**_auth(), "X-Confirm-Event": "FOMC_RATE_DECISION"}
        client.delete(f"/api/calendar/macro/{eid}", headers=headers)
        audit = get_audit_log(client._db_path, action="delete")
        assert len(audit) == 1


class TestBulkDelete:
    def test_bulk_delete_requires_confirmation(self, client):
        r = client.post("/api/calendar/macro", json=_event(), headers=_auth())
        eid = r.get_json()["id"]
        r = client.delete("/api/calendar/macro/bulk",
                          json={"ids": [eid]}, headers=_auth())
        assert r.status_code == 400

    def test_bulk_delete_with_confirmation(self, client):
        r = client.post("/api/calendar/macro", json=_event(), headers=_auth())
        eid = r.get_json()["id"]
        headers = {**_auth(), "X-Confirm-Count": "1"}
        r = client.delete("/api/calendar/macro/bulk",
                          json={"ids": [eid]}, headers=headers)
        assert r.status_code == 200
        assert r.get_json()["deleted"] == 1


class TestCSVImport:
    def test_import_csv_data(self, client):
        csv_data = (
            "date,time_utc,event,region,severity,notes\n"
            "2026-06-18,18:00,FOMC_RATE_DECISION,US,high,Powell presser\n"
            "2026-06-20,11:00,BOE_RATE_DECISION,UK,high,\n"
        )
        r = client.post("/api/calendar/macro/import",
                        data=csv_data,
                        content_type="text/csv",
                        headers=_auth())
        assert r.status_code == 200
        data = r.get_json()
        assert data["inserted"] == 2


class TestAuditEndpoint:
    def test_audit_returns_entries(self, client):
        client.post("/api/calendar/macro", json=_event(), headers=_auth())
        r = client.get("/api/calendar/audit", headers=_auth())
        assert r.status_code == 200
        assert len(r.get_json()) >= 1

    def test_audit_filter_by_action(self, client):
        client.post("/api/calendar/macro", json=_event(), headers=_auth())
        r = client.get("/api/calendar/audit?action=create", headers=_auth())
        assert len(r.get_json()) == 1
        r = client.get("/api/calendar/audit?action=delete", headers=_auth())
        assert len(r.get_json()) == 0
