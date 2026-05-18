"""Tests for macro lockout overlay."""
from datetime import datetime, timezone, timedelta

from bot.overlays.macro_lockout import MacroLockoutOverlay


def _now():
    return datetime(2026, 5, 18, 14, 0, 0, tzinfo=timezone.utc)


def test_no_events_inactive():
    overlay = MacroLockoutOverlay()
    result = overlay.check("AAPL", _now(), {})
    assert result.is_active is False


def test_event_2h_before_active():
    overlay = MacroLockoutOverlay()
    event_time = datetime(2026, 5, 18, 15, 30, 0, tzinfo=timezone.utc)
    ctx = {"macro_events": [{"event_time": event_time, "name": "FOMC Decision"}]}
    result = overlay.check("AAPL", _now(), ctx)
    assert result.is_active is True
    assert "FOMC" in result.reason


def test_event_3h_before_inactive():
    overlay = MacroLockoutOverlay()
    event_time = datetime(2026, 5, 18, 18, 0, 0, tzinfo=timezone.utc)
    ctx = {"macro_events": [{"event_time": event_time, "name": "CPI Release"}]}
    result = overlay.check("AAPL", _now(), ctx)
    assert result.is_active is False


def test_event_past_but_same_day_active():
    overlay = MacroLockoutOverlay()
    event_time = datetime(2026, 5, 18, 10, 0, 0, tzinfo=timezone.utc)
    ctx = {"macro_events": [{"event_time": event_time, "name": "NFP"}]}
    result = overlay.check("AAPL", _now(), ctx)
    assert result.is_active is True


def test_strict_mode_full_day():
    overlay = MacroLockoutOverlay()
    event_time = datetime(2026, 5, 18, 23, 0, 0, tzinfo=timezone.utc)
    ctx = {
        "macro_events": [{"event_time": event_time, "name": "Fed Minutes"}],
        "data_quality_strict_mode": True,
    }
    result = overlay.check("AAPL", _now(), ctx)
    assert result.is_active is True


def test_event_date_only():
    overlay = MacroLockoutOverlay()
    ctx = {"macro_events": [{"event_date": "2026-05-18", "name": "BoE Decision"}]}
    result = overlay.check("AAPL", _now(), ctx)
    assert result.is_active is True


def test_event_date_tomorrow_inactive():
    overlay = MacroLockoutOverlay()
    ctx = {"macro_events": [{"event_date": "2026-05-19", "name": "BoE Decision"}]}
    result = overlay.check("AAPL", _now(), ctx)
    assert result.is_active is False


def test_string_event_time():
    overlay = MacroLockoutOverlay()
    ctx = {"macro_events": [{"event_time": "2026-05-18T15:00:00+00:00", "name": "ECB"}]}
    result = overlay.check("AAPL", _now(), ctx)
    assert result.is_active is True


def test_expires_at_set():
    overlay = MacroLockoutOverlay()
    event_time = datetime(2026, 5, 18, 15, 0, 0, tzinfo=timezone.utc)
    ctx = {"macro_events": [{"event_time": event_time, "name": "FOMC"}]}
    result = overlay.check("AAPL", _now(), ctx)
    assert result.expires_at is not None


def test_self_test():
    overlay = MacroLockoutOverlay()
    assert overlay.self_test() is True


def test_instruments_affected():
    overlay = MacroLockoutOverlay()
    assert overlay.instruments_affected() == ["__all__"]
