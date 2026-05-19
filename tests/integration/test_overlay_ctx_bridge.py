"""
§10.3 integration: Calendar DB → overlay registry → MacroLockoutOverlay.

Proves that macro events written to the DB are visible to the overlay
system in production without manual ctx construction. This is the bridge
test that catches the "component exists, integration missing" bug class.
"""
import pytest
from datetime import datetime, timezone, timedelta

from bot.overlays.calendar_db import CalendarDB
from bot.overlays.registry import active_overlays, init_overlay_registry, _load_macro_events
from bot.regime.orchestrator import RegimeOrchestrator
from bot.regime.flags import FeatureFlags


def _today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@pytest.fixture
def bridge_setup(tmp_path):
    db_path = str(tmp_path / "regime.db")
    db = CalendarDB(db_path)
    init_overlay_registry(db_path)
    return db, db_path


class TestOverlayCtxBridge:
    def test_registry_loads_macro_events_from_db(self, bridge_setup):
        """_load_macro_events() returns events written via CalendarDB."""
        db, db_path = bridge_setup
        now = datetime.now(timezone.utc)
        event_time = (now + timedelta(minutes=30)).strftime("%H:%M")

        db.add_macro_event(
            event_date=_today_str(),
            name="FOMC_RATE_DECISION",
            source="calendar_ui",
            event_time=event_time,
            impact="high",
        )

        events = _load_macro_events()
        assert len(events) >= 1
        assert events[0]["name"] == "FOMC_RATE_DECISION"
        assert isinstance(events[0]["event_time"], datetime)

    def test_active_overlays_sees_db_events_without_ctx(self, bridge_setup):
        """active_overlays() enriches empty ctx with DB macro events."""
        db, db_path = bridge_setup
        now = datetime.now(timezone.utc)
        event_time = (now + timedelta(minutes=30)).strftime("%H:%M")

        db.add_macro_event(
            event_date=_today_str(),
            name="FOMC_RATE_DECISION",
            source="calendar_ui",
            event_time=event_time,
            impact="high",
        )

        results = active_overlays("AAPL", now, {})
        overlay_names = [r.overlay_name for r in results]
        assert "MACRO_LOCKOUT" in overlay_names, (
            "INTEGRATION FAILURE: macro event in DB but active_overlays() "
            "does not produce MACRO_LOCKOUT. The DB → ctx bridge is broken."
        )

    def test_orchestrator_sees_calendar_entries(self, bridge_setup):
        """Calendar UI write → orchestrator overlay check → MACRO_LOCKOUT active."""
        db, db_path = bridge_setup
        now = datetime.now(timezone.utc)
        event_time = (now + timedelta(minutes=30)).strftime("%H:%M")

        db.add_macro_event(
            event_date=_today_str(),
            name="FOMC_RATE_DECISION",
            source="calendar_ui",
            event_time=event_time,
            impact="high",
        )

        flags = FeatureFlags({
            "enable_event_overlays_shadow": True,
            "enable_event_overlays_live": True,
        })

        orch = RegimeOrchestrator(
            flags=flags,
            overlay_registry_fn=active_overlays,
        )

        overlays = orch._get_overlays("AAPL")
        overlay_names = [o.overlay_name for o in overlays]

        assert "MACRO_LOCKOUT" in overlay_names, (
            "INTEGRATION FAILURE: macro event written to DB but orchestrator's "
            "overlay path does not surface it. The DB → ctx bridge is missing "
            "in production."
        )

    def test_no_events_means_no_lockout(self, bridge_setup):
        """Empty DB → no MACRO_LOCKOUT overlay."""
        db, db_path = bridge_setup
        now = datetime.now(timezone.utc)

        results = active_overlays("AAPL", now, {})
        macro_results = [r for r in results if r.overlay_name == "MACRO_LOCKOUT"]
        assert len(macro_results) == 0

    def test_explicit_ctx_not_overwritten(self, bridge_setup):
        """If caller provides macro_events in ctx, DB is not consulted."""
        db, db_path = bridge_setup
        now = datetime.now(timezone.utc)

        explicit_events = [{
            "name": "EXPLICIT_EVENT",
            "event_date": _today_str(),
            "event_time": now + timedelta(minutes=10),
        }]

        results = active_overlays("AAPL", now, {"macro_events": explicit_events})
        macro_results = [r for r in results if r.overlay_name == "MACRO_LOCKOUT"]
        assert len(macro_results) == 1
        assert "EXPLICIT_EVENT" in macro_results[0].reason


class TestBridgeDeliberateFailure:
    def test_without_init_no_lockout(self, tmp_path):
        """Without init_overlay_registry(), no macro events are loaded."""
        db_path = str(tmp_path / "no_init.db")
        db = CalendarDB(db_path)
        now = datetime.now(timezone.utc)
        event_time = (now + timedelta(minutes=30)).strftime("%H:%M")

        db.add_macro_event(
            event_date=_today_str(),
            name="FOMC_RATE_DECISION",
            source="calendar_ui",
            event_time=event_time,
            impact="high",
        )

        # Deliberately do NOT call init_overlay_registry — simulate the bug
        import bot.overlays.registry as reg
        old_db_path = reg._db_path
        reg._db_path = None

        try:
            results = active_overlays("AAPL", now, {})
            macro_results = [r for r in results if r.overlay_name == "MACRO_LOCKOUT"]
            assert len(macro_results) == 0, (
                "META-TEST FAILURE: overlay returned MACRO_LOCKOUT even without "
                "init_overlay_registry(). The test would pass even if the bridge "
                "were broken — it does not actually test the bridge."
            )
        finally:
            reg._db_path = old_db_path
