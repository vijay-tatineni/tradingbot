"""§15.4.7: CSV import endpoint tests."""
import pytest
import jwt as pyjwt
import datetime

from flask import Flask
from bot.calendar_ui.routes import calendar_bp, init_calendar_routes
from bot.calendar_ui.db import ensure_tables, list_events, get_audit_log

JWT_SECRET = "test-csv-secret"


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "csv_test.db")
    app = Flask(__name__)
    app.register_blueprint(calendar_bp)
    init_calendar_routes(db_path, JWT_SECRET)
    app.config["TESTING"] = True
    with app.test_client() as c:
        c._db_path = db_path
        yield c


def _auth():
    token = pyjwt.encode(
        {"sub": "csvtester", "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1)},
        JWT_SECRET, algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


VALID_CSV = (
    "date,time_utc,event,region,severity,notes\n"
    "2026-06-18,18:00,FOMC_RATE_DECISION,US,high,Powell press conference\n"
    "2026-06-20,11:00,BOE_RATE_DECISION,UK,high,\n"
)


def test_import_happy_path(client):
    r = client.post("/api/calendar/macro/import",
                    data=VALID_CSV, content_type="text/csv",
                    headers=_auth())
    assert r.status_code == 200
    data = r.get_json()
    assert data["inserted"] == 2
    assert data["skipped_duplicates"] == 0
    assert data["validation_errors"] == []
    events = list_events(client._db_path)
    assert len(events) == 2


def test_import_idempotent(client):
    client.post("/api/calendar/macro/import",
                data=VALID_CSV, content_type="text/csv",
                headers=_auth())
    r = client.post("/api/calendar/macro/import",
                    data=VALID_CSV, content_type="text/csv",
                    headers=_auth())
    data = r.get_json()
    assert data["inserted"] == 0
    assert data["skipped_duplicates"] == 2
    events = list_events(client._db_path)
    assert len(events) == 2


def test_import_partial_validation(client):
    csv_data = (
        "date,time_utc,event,region,severity,notes\n"
        "2026-06-18,18:00,FOMC_RATE_DECISION,US,high,\n"
        "bad-date,18:00,INVALID_EVENT,US,high,\n"
    )
    r = client.post("/api/calendar/macro/import",
                    data=csv_data, content_type="text/csv",
                    headers=_auth())
    data = r.get_json()
    assert data["inserted"] == 1
    assert len(data["validation_errors"]) == 1
    assert data["validation_errors"][0]["row"] == 2


def test_import_empty_csv(client):
    r = client.post("/api/calendar/macro/import",
                    data="", content_type="text/csv",
                    headers=_auth())
    assert r.status_code == 400


def test_import_writes_audit(client):
    client.post("/api/calendar/macro/import",
                data=VALID_CSV, content_type="text/csv",
                headers=_auth())
    audit = get_audit_log(client._db_path, action="bulk_import")
    assert len(audit) == 1
    assert audit[0]["affected_count"] == 2


def test_import_requires_auth(client):
    r = client.post("/api/calendar/macro/import",
                    data=VALID_CSV, content_type="text/csv")
    assert r.status_code == 401
