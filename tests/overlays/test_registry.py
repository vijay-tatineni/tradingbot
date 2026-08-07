"""Tests for overlay registry."""
from datetime import datetime, timezone

from bot.overlays.registry import (
    active_overlays,
    get_overlay,
    instruments_affected_by_overlay,
    OVERLAY_ORDER,
    OVERLAY_BY_NAME,
)


def _now():
    return datetime(2026, 5, 18, 14, 0, 0, tzinfo=timezone.utc)


def test_no_active_overlays():
    results = active_overlays("AAPL", _now(), {})
    assert results == []


def test_data_quality_returned_when_active():
    from datetime import timedelta
    old = _now() - timedelta(minutes=45)
    results = active_overlays("AAPL", _now(), {"last_bar_time": old})
    assert len(results) >= 1
    names = [r.overlay_name for r in results]
    assert "DATA_QUALITY" in names


def test_multiple_overlays_can_fire():
    from datetime import timedelta
    old = _now() - timedelta(minutes=45)
    ctx = {
        "last_bar_time": old,
        "median_volume_for_bucket": 1000,
        "current_cumulative_volume": 300,
    }
    results = active_overlays("AAPL", _now(), ctx)
    names = [r.overlay_name for r in results]
    assert "DATA_QUALITY" in names
    assert "LOW_LIQUIDITY" in names


def test_overlay_order_is_precedence():
    names = [o.name for o in OVERLAY_ORDER]
    assert names == ["DATA_QUALITY", "LOW_LIQUIDITY", "MACRO_LOCKOUT"]


def test_get_overlay_valid():
    overlay = get_overlay("DATA_QUALITY")
    assert overlay.name == "DATA_QUALITY"


def test_get_overlay_invalid():
    try:
        get_overlay("NONEXISTENT")
        assert False, "Should raise ValueError"
    except ValueError:
        pass


def test_get_overlay_earnings_not_registered():
    try:
        get_overlay("EARNINGS_LOCKOUT")
        assert False, "EARNINGS_LOCKOUT should not be registered (deferred)"
    except ValueError:
        pass


def test_instruments_affected_data_quality():
    result = instruments_affected_by_overlay("DATA_QUALITY")
    assert "__all__" in result


def test_all_overlays_in_registry():
    assert len(OVERLAY_BY_NAME) == 3
    assert set(OVERLAY_BY_NAME.keys()) == {
        "DATA_QUALITY", "LOW_LIQUIDITY", "MACRO_LOCKOUT",
    }
