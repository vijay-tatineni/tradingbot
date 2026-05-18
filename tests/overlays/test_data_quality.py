"""Tests for data quality overlay."""
from datetime import datetime, timezone, timedelta

from bot.overlays.data_quality import DataQualityOverlay


def _now():
    return datetime(2026, 5, 18, 14, 0, 0, tzinfo=timezone.utc)


def test_no_data_returns_inactive():
    overlay = DataQualityOverlay()
    result = overlay.check("AAPL", _now(), {})
    assert result.is_active is False
    assert result.overlay_name == "DATA_QUALITY"


def test_stale_bar_activates():
    overlay = DataQualityOverlay()
    old_time = _now() - timedelta(minutes=45)
    result = overlay.check("AAPL", _now(), {"last_bar_time": old_time})
    assert result.is_active is True
    assert "stale" in result.reason.lower()


def test_stale_bar_within_limit_inactive():
    overlay = DataQualityOverlay()
    recent_time = _now() - timedelta(minutes=15)
    result = overlay.check("AAPL", _now(), {"last_bar_time": recent_time})
    assert result.is_active is False


def test_strict_mode_halves_stale_limit():
    overlay = DataQualityOverlay()
    time_20m_ago = _now() - timedelta(minutes=20)
    ctx_normal = {"last_bar_time": time_20m_ago}
    ctx_strict = {"last_bar_time": time_20m_ago, "data_quality_strict_mode": True}

    result_normal = overlay.check("AAPL", _now(), ctx_normal)
    result_strict = overlay.check("AAPL", _now(), ctx_strict)
    assert result_normal.is_active is False
    assert result_strict.is_active is True


def test_ohlcv_low_gt_high():
    overlay = DataQualityOverlay()
    ctx = {"ohlcv": {"open": 100, "high": 95, "low": 105, "close": 100, "volume": 1000}}
    result = overlay.check("AAPL", _now(), ctx)
    assert result.is_active is True
    assert "Low" in result.reason


def test_gap_without_news():
    overlay = DataQualityOverlay()
    ctx = {"prev_close": 100.0, "current_open": 120.0}
    result = overlay.check("AAPL", _now(), ctx)
    assert result.is_active is True
    assert "Gap" in result.reason


def test_gap_with_news_inactive():
    overlay = DataQualityOverlay()
    ctx = {"prev_close": 100.0, "current_open": 120.0, "has_news_for_gap": True}
    result = overlay.check("AAPL", _now(), ctx)
    assert result.is_active is False


def test_self_test_passes():
    overlay = DataQualityOverlay()
    assert overlay.self_test() is True


def test_instruments_affected():
    overlay = DataQualityOverlay()
    assert overlay.instruments_affected() == ["__all__"]
