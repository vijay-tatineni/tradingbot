"""
Invariant: RegimeClassification.cache_hit field exists.

§7.1 / §16.2. Removing this field should fail this test.
"""
from datetime import datetime, timezone
from bot.regime.models import RegimeClassification


def test_cache_hit_field_exists():
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
    assert hasattr(c, "cache_hit")
    assert c.cache_hit is False


def test_cache_hit_field_settable_at_construction():
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
