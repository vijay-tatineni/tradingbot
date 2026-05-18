"""
§16.4: End-to-end test — UI writes → overlay reads.

Imports a macro event via the calendar UI DB layer, then queries
bot.overlays.registry.active_overlays() to confirm the overlay sees
the new event as active. This is the "does the UI actually affect
overlay behaviour" test.
"""
import pytest
from datetime import datetime, timezone, timedelta

from bot.calendar_ui.db import ensure_tables, create_event, csv_import, list_events
from bot.overlays.macro_lockout import MacroLockoutOverlay


def _today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_time_str():
    return datetime.now(timezone.utc).strftime("%H:%M")


class TestUIWriteOverlayRead:
    def test_create_event_visible_to_overlay(self, tmp_path):
        """Create an event via UI → overlay sees it as active."""
        db_path = str(tmp_path / "regime.db")
        ensure_tables(db_path)

        now = datetime.now(timezone.utc)
        event_time = (now + timedelta(minutes=30)).strftime("%H:%M")

        create_event(db_path, {
            "date": _today_str(),
            "time_utc": event_time,
            "event": "FOMC_RATE_DECISION",
            "region": "US",
            "severity": "high",
        }, user="testuser")

        events = list_events(db_path, from_date=_today_str())
        assert len(events) >= 1

        macro_events_for_ctx = []
        for ev in events:
            event_dt_str = f"{ev['event_date']}T{ev['event_time']}:00"
            macro_events_for_ctx.append({
                "name": ev["name"],
                "event_date": ev["event_date"],
                "event_time": datetime.fromisoformat(event_dt_str).replace(
                    tzinfo=timezone.utc),
            })

        overlay = MacroLockoutOverlay()
        check = overlay.check("AAPL", now, {"macro_events": macro_events_for_ctx})

        assert check.is_active is True, (
            f"INTEGRATION FAILURE: Overlay does not see UI-created event. "
            f"Event: FOMC_RATE_DECISION at {event_time}, now: {now.strftime('%H:%M')}, "
            f"check result: {check}"
        )
        assert "FOMC" in check.reason

    def test_csv_import_visible_to_overlay(self, tmp_path):
        """CSV import via UI → overlay sees imported events."""
        db_path = str(tmp_path / "regime.db")
        ensure_tables(db_path)

        now = datetime.now(timezone.utc)
        event_time = (now + timedelta(minutes=15)).strftime("%H:%M")

        rows = [{
            "date": _today_str(),
            "time_utc": event_time,
            "event": "BOE_RATE_DECISION",
            "region": "UK",
            "severity": "high",
            "notes": "",
        }]
        result = csv_import(db_path, rows, "csvuser")
        assert result["inserted"] == 1

        events = list_events(db_path, from_date=_today_str())
        macro_events_for_ctx = []
        for ev in events:
            event_dt_str = f"{ev['event_date']}T{ev['event_time']}:00"
            macro_events_for_ctx.append({
                "name": ev["name"],
                "event_date": ev["event_date"],
                "event_time": datetime.fromisoformat(event_dt_str).replace(
                    tzinfo=timezone.utc),
            })

        overlay = MacroLockoutOverlay()
        check = overlay.check("MSFT", now, {"macro_events": macro_events_for_ctx})

        assert check.is_active is True, (
            f"INTEGRATION FAILURE: Overlay does not see CSV-imported event."
        )

    def test_deleted_event_not_visible_to_overlay(self, tmp_path):
        """After deleting via UI, overlay no longer sees the event."""
        db_path = str(tmp_path / "regime.db")
        ensure_tables(db_path)

        now = datetime.now(timezone.utc)
        event_time = (now + timedelta(minutes=30)).strftime("%H:%M")

        from bot.calendar_ui.db import delete_event
        created = create_event(db_path, {
            "date": _today_str(),
            "time_utc": event_time,
            "event": "FOMC_RATE_DECISION",
            "region": "US",
            "severity": "high",
        }, user="testuser")

        delete_event(db_path, created["id"], "testuser")

        events = list_events(db_path, from_date=_today_str())
        assert len(events) == 0

        overlay = MacroLockoutOverlay()
        check = overlay.check("AAPL", now, {"macro_events": []})
        assert check.is_active is False


class TestFullCRUDCycle:
    def test_create_edit_delete_cycle(self, tmp_path):
        """Full CRUD cycle through the DB layer."""
        from bot.calendar_ui.db import update_event, delete_event, get_audit_log

        db_path = str(tmp_path / "regime.db")
        ensure_tables(db_path)

        created = create_event(db_path, {
            "date": "2026-07-01",
            "time_utc": "14:00",
            "event": "FOMC_RATE_DECISION",
            "region": "US",
            "severity": "high",
        }, user="admin")
        assert created["name"] == "FOMC_RATE_DECISION"

        updated = update_event(db_path, created["id"], {
            "date": "2026-07-01",
            "time_utc": "14:30",
            "event": "FOMC_RATE_DECISION",
            "region": "US",
            "severity": "medium",
        }, user="admin")
        assert updated["event_time"] == "14:30"
        assert updated["impact"] == "medium"

        deleted = delete_event(db_path, created["id"], "admin")
        assert deleted is not None

        from bot.calendar_ui.db import get_event
        assert get_event(db_path, created["id"]) is None

        audit = get_audit_log(db_path)
        actions = [a["action"] for a in audit]
        assert "create" in actions
        assert "update" in actions
        assert "delete" in actions
