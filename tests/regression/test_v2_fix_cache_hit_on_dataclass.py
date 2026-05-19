"""
Regression: v2 added cache_hit field to RegimeClassification dataclass.

Bug: v1 had cache_hit as a separate return value, not on the dataclass.
Fix: v2 added cache_hit as a field with default False.
Spec: §7.1, v2 changelog.
"""
from datetime import datetime, timezone
from bot.regime.models import RegimeClassification


def test_cache_hit_field_accessible():
    c = RegimeClassification(
        instrument="BARC.L",
        classified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        trading_date="2026-01-01",
        raw_regime="TRENDING",
        confidence=0.9,
        rationale="Test",
        features={},
        model_version="test",
        prompt_version="V1",
        input_hash="test",
    )
    assert c.cache_hit is False


def test_cache_hit_set_to_true():
    c = RegimeClassification(
        instrument="BARC.L",
        classified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        trading_date="2026-01-01",
        raw_regime="TRENDING",
        confidence=0.9,
        rationale="Test",
        features={},
        model_version="test",
        prompt_version="V1",
        input_hash="test",
        cache_hit=True,
    )
    assert c.cache_hit is True
