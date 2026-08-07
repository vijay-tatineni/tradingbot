"""Tests for low liquidity overlay."""
from datetime import datetime, timezone

from bot.overlays.low_liquidity import LowLiquidityOverlay


def _now():
    return datetime(2026, 5, 18, 14, 0, 0, tzinfo=timezone.utc)


def test_no_volume_data_inactive():
    overlay = LowLiquidityOverlay()
    result = overlay.check("AAPL", _now(), {})
    assert result.is_active is False


def test_low_volume_active():
    overlay = LowLiquidityOverlay()
    ctx = {"median_volume_for_bucket": 1000, "current_cumulative_volume": 300}
    result = overlay.check("AAPL", _now(), ctx)
    assert result.is_active is True
    assert "30.0%" in result.reason


def test_normal_volume_inactive():
    overlay = LowLiquidityOverlay()
    ctx = {"median_volume_for_bucket": 1000, "current_cumulative_volume": 600}
    result = overlay.check("AAPL", _now(), ctx)
    assert result.is_active is False


def test_strict_mode_raises_threshold():
    overlay = LowLiquidityOverlay()
    ctx = {
        "median_volume_for_bucket": 1000,
        "current_cumulative_volume": 500,
        "data_quality_strict_mode": True,
    }
    result = overlay.check("AAPL", _now(), ctx)
    assert result.is_active is True

    ctx_normal = {
        "median_volume_for_bucket": 1000,
        "current_cumulative_volume": 500,
    }
    result_normal = overlay.check("AAPL", _now(), ctx_normal)
    assert result_normal.is_active is False


def test_exactly_at_threshold_inactive():
    overlay = LowLiquidityOverlay()
    ctx = {"median_volume_for_bucket": 1000, "current_cumulative_volume": 400}
    result = overlay.check("AAPL", _now(), ctx)
    assert result.is_active is False


def test_zero_median_inactive():
    overlay = LowLiquidityOverlay()
    ctx = {"median_volume_for_bucket": 0, "current_cumulative_volume": 100}
    result = overlay.check("AAPL", _now(), ctx)
    assert result.is_active is False


def test_self_test():
    overlay = LowLiquidityOverlay()
    assert overlay.self_test() is True


def test_instruments_affected():
    overlay = LowLiquidityOverlay()
    assert overlay.instruments_affected() == ["__all__"]
