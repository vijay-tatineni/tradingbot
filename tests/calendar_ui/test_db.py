"""§15.4.2: Calendar UI database operations tests."""
import sqlite3
import pytest

from bot.calendar_ui.db import (
    ensure_tables,
    create_event,
    get_event,
    list_events,
    update_event,
    delete_event,
    bulk_delete,
    csv_import,
    get_audit_log,
    DuplicateEventError,
)


def _db(tmp_path):
    path = str(tmp_path / "cal.db")
    ensure_tables(path)
    return path


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


class TestCreateEvent:
    def test_create_returns_event(self, tmp_path):
        db = _db(tmp_path)
        result = create_event(db, _event(), "testuser")
        assert result["name"] == "FOMC_RATE_DECISION"
        assert result["event_date"] == "2026-06-18"
        assert result["id"] is not None

    def test_create_writes_audit(self, tmp_path):
        db = _db(tmp_path)
        create_event(db, _event(), "testuser")
        audit = get_audit_log(db)
        assert len(audit) == 1
        assert audit[0]["action"] == "create"
        assert audit[0]["user_jwt_sub"] == "testuser"

    def test_duplicate_raises(self, tmp_path):
        db = _db(tmp_path)
        create_event(db, _event(), "user1")
        with pytest.raises(DuplicateEventError):
            create_event(db, _event(), "user2")

    def test_no_audit_on_duplicate(self, tmp_path):
        db = _db(tmp_path)
        create_event(db, _event(), "user1")
        try:
            create_event(db, _event(), "user2")
        except DuplicateEventError:
            pass
        audit = get_audit_log(db)
        assert len(audit) == 1


class TestGetEvent:
    def test_get_existing(self, tmp_path):
        db = _db(tmp_path)
        created = create_event(db, _event(), "user")
        result = get_event(db, created["id"])
        assert result["name"] == "FOMC_RATE_DECISION"

    def test_get_nonexistent(self, tmp_path):
        db = _db(tmp_path)
        assert get_event(db, 9999) is None


class TestListEvents:
    def test_list_all(self, tmp_path):
        db = _db(tmp_path)
        create_event(db, _event(), "user")
        create_event(db, _event(event="BOE_RATE_DECISION", region="UK"), "user")
        results = list_events(db)
        assert len(results) == 2

    def test_filter_by_date(self, tmp_path):
        db = _db(tmp_path)
        create_event(db, _event(date="2026-06-01"), "user")
        create_event(db, _event(date="2026-07-01", event="BOE_RATE_DECISION"), "user")
        results = list_events(db, from_date="2026-06-15")
        assert len(results) == 1


class TestUpdateEvent:
    def test_update_changes_fields(self, tmp_path):
        db = _db(tmp_path)
        created = create_event(db, _event(), "user")
        updated = update_event(db, created["id"],
                               _event(severity="low"), "user")
        assert updated["impact"] == "low"

    def test_update_writes_audit_with_before(self, tmp_path):
        db = _db(tmp_path)
        created = create_event(db, _event(), "user")
        update_event(db, created["id"], _event(severity="low"), "user")
        audit = get_audit_log(db)
        update_audit = [a for a in audit if a["action"] == "update"]
        assert len(update_audit) == 1
        assert update_audit[0]["before_json"] is not None
        assert update_audit[0]["after_json"] is not None

    def test_update_nonexistent(self, tmp_path):
        db = _db(tmp_path)
        assert update_event(db, 9999, _event(), "user") is None


class TestDeleteEvent:
    def test_delete_removes(self, tmp_path):
        db = _db(tmp_path)
        created = create_event(db, _event(), "user")
        deleted = delete_event(db, created["id"], "user")
        assert deleted is not None
        assert get_event(db, created["id"]) is None

    def test_delete_writes_audit(self, tmp_path):
        db = _db(tmp_path)
        created = create_event(db, _event(), "user")
        delete_event(db, created["id"], "user")
        audit = get_audit_log(db)
        del_audit = [a for a in audit if a["action"] == "delete"]
        assert len(del_audit) == 1
        assert del_audit[0]["before_json"] is not None
        assert del_audit[0]["after_json"] is None

    def test_delete_nonexistent(self, tmp_path):
        db = _db(tmp_path)
        assert delete_event(db, 9999, "user") is None


class TestBulkDelete:
    def test_bulk_delete(self, tmp_path):
        db = _db(tmp_path)
        e1 = create_event(db, _event(), "user")
        e2 = create_event(db, _event(event="BOE_RATE_DECISION", region="UK"), "user")
        deleted = bulk_delete(db, [e1["id"], e2["id"]], "user")
        assert deleted == 2
        assert list_events(db) == []

    def test_bulk_delete_audit(self, tmp_path):
        db = _db(tmp_path)
        e1 = create_event(db, _event(), "user")
        bulk_delete(db, [e1["id"]], "user")
        audit = get_audit_log(db)
        bulk_audit = [a for a in audit if a["action"] == "bulk_delete"]
        assert len(bulk_audit) == 1
        assert bulk_audit[0]["affected_count"] == 1


class TestCSVImport:
    def test_import_valid(self, tmp_path):
        db = _db(tmp_path)
        rows = [
            {"date": "2026-06-18", "time_utc": "18:00", "event": "FOMC_RATE_DECISION",
             "region": "US", "severity": "high", "notes": ""},
            {"date": "2026-06-20", "time_utc": "11:00", "event": "BOE_RATE_DECISION",
             "region": "UK", "severity": "high", "notes": ""},
        ]
        result = csv_import(db, rows, "user")
        assert result["inserted"] == 2
        assert result["skipped_duplicates"] == 0
        assert result["validation_errors"] == []

    def test_import_duplicate_skipped(self, tmp_path):
        db = _db(tmp_path)
        rows = [
            {"date": "2026-06-18", "time_utc": "18:00", "event": "FOMC_RATE_DECISION",
             "region": "US", "severity": "high", "notes": ""},
        ]
        csv_import(db, rows, "user")
        result = csv_import(db, rows, "user")
        assert result["inserted"] == 0
        assert result["skipped_duplicates"] == 1

    def test_import_validation_errors(self, tmp_path):
        db = _db(tmp_path)
        rows = [
            {"date": "bad", "time_utc": "18:00", "event": "FOMC_RATE_DECISION",
             "region": "US", "severity": "high", "notes": ""},
        ]
        result = csv_import(db, rows, "user")
        assert result["inserted"] == 0
        assert len(result["validation_errors"]) == 1

    def test_import_writes_audit(self, tmp_path):
        db = _db(tmp_path)
        rows = [
            {"date": "2026-06-18", "time_utc": "18:00", "event": "FOMC_RATE_DECISION",
             "region": "US", "severity": "high", "notes": ""},
        ]
        csv_import(db, rows, "user")
        audit = get_audit_log(db)
        import_audit = [a for a in audit if a["action"] == "bulk_import"]
        assert len(import_audit) == 1


class TestAuditLog:
    def test_filter_by_action(self, tmp_path):
        db = _db(tmp_path)
        created = create_event(db, _event(), "user")
        update_event(db, created["id"], _event(severity="low"), "user")
        audit = get_audit_log(db, action="update")
        assert len(audit) == 1
        assert audit[0]["action"] == "update"
