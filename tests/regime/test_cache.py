"""Unit tests for regime classification cache — §9.4."""
import pytest
from datetime import datetime, timezone

from bot.regime.cache import RegimeCache
from bot.regime.models import RegimeClassification


@pytest.fixture
def cache(tmp_path):
    return RegimeCache(str(tmp_path / "test.db"))


def _make_classification(instrument="BARC.L", trading_date="2026-01-01",
                         input_hash="abc123", regime="TRENDING"):
    return RegimeClassification(
        instrument=instrument,
        classified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        trading_date=trading_date,
        raw_regime=regime,
        confidence=0.9,
        rationale="Test",
        features={"adx_14": 30.0},
        model_version="claude-sonnet-4-6",
        prompt_version="V1",
        input_hash=input_hash,
    )


class TestCache:
    def test_miss(self, cache):
        result = cache.get("BARC.L", "2026-01-01", "nonexistent")
        assert result is None

    def test_put_and_get(self, cache):
        c = _make_classification()
        cache.put(c)
        result = cache.get("BARC.L", "2026-01-01", "abc123")
        assert result is not None
        assert result.raw_regime == "TRENDING"
        assert result.cache_hit is True

    def test_different_hash_misses(self, cache):
        c = _make_classification(input_hash="hash1")
        cache.put(c)
        result = cache.get("BARC.L", "2026-01-01", "hash2")
        assert result is None

    def test_different_instrument_misses(self, cache):
        c = _make_classification(instrument="BARC.L")
        cache.put(c)
        result = cache.get("SGLN.L", "2026-01-01", "abc123")
        assert result is None

    def test_replace_on_same_key(self, cache):
        c1 = _make_classification(regime="TRENDING")
        cache.put(c1)
        c2 = _make_classification(regime="RANGING")
        cache.put(c2)
        result = cache.get("BARC.L", "2026-01-01", "abc123")
        assert result.raw_regime == "RANGING"

    def test_preserves_features(self, cache):
        c = _make_classification()
        cache.put(c)
        result = cache.get("BARC.L", "2026-01-01", "abc123")
        assert result.features == {"adx_14": 30.0}
