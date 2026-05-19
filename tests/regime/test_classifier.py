"""Unit tests for regime classifier — §9.2."""
import json
import os
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from bot.regime.classifier import RegimeClassifier
from bot.regime.classifier_prompt import CLASSIFIER_PROMPT_V1
from bot.regime.cache import RegimeCache
from bot.regime.cost_tracker import CostTracker


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def cache(tmp_db):
    return RegimeCache(tmp_db)


@pytest.fixture
def cost_tracker(tmp_db):
    return CostTracker(tmp_db)


@pytest.fixture
def classifier(cache, cost_tracker):
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
        return RegimeClassifier(cache, cost_tracker)


SAMPLE_FEATURES = {
    "adx_14": 30.0,
    "atr_14": 5.0,
    "atr_pct": 2.5,
    "ma_200_slope_pct_per_day": 0.05,
    "range_efficiency": 0.45,
    "realized_volatility_20d": 15.0,
    "close_above_ma200": True,
    "distance_to_ma200_pct": 3.5,
}


class TestClassifierFallback:
    def test_fallback_when_no_api_key(self, classifier):
        result = classifier.classify("BARC.L", "2026-01-01", SAMPLE_FEATURES)
        assert result.raw_regime == "UNCLEAR"
        assert result.confidence == 0.0
        assert "client_unavailable" in result.rationale

    def test_fallback_preserves_instrument(self, classifier):
        result = classifier.classify("SGLN.L", "2026-01-01", SAMPLE_FEATURES)
        assert result.instrument == "SGLN.L"

    def test_fallback_preserves_features(self, classifier):
        result = classifier.classify("BARC.L", "2026-01-01", SAMPLE_FEATURES)
        assert result.features == SAMPLE_FEATURES


class TestClassifierCache:
    def test_cache_hit(self, cache, cost_tracker, tmp_db):
        from bot.regime.models import RegimeClassification
        c = RegimeClassification(
            instrument="BARC.L",
            classified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            trading_date="2026-01-01",
            raw_regime="TRENDING",
            confidence=0.9,
            rationale="Strong ADX",
            features=SAMPLE_FEATURES,
            model_version="claude-sonnet-4-6",
            prompt_version="V1",
            input_hash="testhash",
        )
        cache.put(c)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            classifier = RegimeClassifier(cache, cost_tracker)
        # Patch the hash to match
        classifier._compute_hash = lambda f: "testhash"
        result = classifier.classify("BARC.L", "2026-01-01", SAMPLE_FEATURES)
        assert result.cache_hit is True
        assert result.raw_regime == "TRENDING"


class TestClassifierBudget:
    def test_budget_exceeded_returns_fallback(self, cache, cost_tracker, tmp_db):
        cost_tracker.log_classification(
            instrument="BARC.L", trading_date="2026-01-01",
            model="claude-sonnet-4-6", prompt_version="V1",
            cost_usd=5.01,
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            classifier = RegimeClassifier(cache, cost_tracker)
            classifier._client = MagicMock()
        result = classifier.classify("BARC.L", "2026-01-01", SAMPLE_FEATURES)
        assert result.raw_regime == "UNCLEAR"
        assert "budget_exceeded" in result.rationale


class TestClassifierHash:
    def test_hash_deterministic(self, classifier):
        h1 = classifier._compute_hash(SAMPLE_FEATURES)
        h2 = classifier._compute_hash(SAMPLE_FEATURES)
        assert h1 == h2

    def test_hash_changes_with_features(self, classifier):
        h1 = classifier._compute_hash(SAMPLE_FEATURES)
        modified = dict(SAMPLE_FEATURES)
        modified["adx_14"] = 99.0
        h2 = classifier._compute_hash(modified)
        assert h1 != h2


class TestPromptSync:
    def test_prompt_sync(self):
        """Verify classifier_prompt.py matches specs/prompts/classifier_v1.md."""
        spec_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "specs", "prompts", "classifier_v1.md"
        )
        with open(spec_path) as f:
            content = f.read()
        # The md file has a preamble before the prompt content
        # Check that the core prompt text appears in the md file
        for line in CLASSIFIER_PROMPT_V1.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                assert line in content, f"Prompt line not found in spec mirror: {line[:60]}"
