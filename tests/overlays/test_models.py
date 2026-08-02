"""Tests for overlay dataclasses."""
from datetime import datetime, timezone

from bot.overlays.models import OverlayCheck


def test_overlay_check_frozen():
    oc = OverlayCheck(
        overlay_name="TEST",
        is_active=True,
        expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        reason="test reason",
    )
    assert oc.overlay_name == "TEST"
    assert oc.is_active is True
    try:
        oc.overlay_name = "CHANGED"
        assert False, "Should be frozen"
    except AttributeError:
        pass


def test_overlay_check_inactive():
    oc = OverlayCheck(
        overlay_name="TEST",
        is_active=False,
        expires_at=None,
        reason="all clear",
    )
    assert oc.is_active is False
    assert oc.expires_at is None
