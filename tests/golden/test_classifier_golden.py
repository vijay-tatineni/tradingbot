"""
Golden-file tests for classifier — §16.3.

Fixed input → fixed expected output via mocked Claude tool-use response.
Tests classifier wiring: parsing, schema validation, dataclass construction, cache.
"""
import json
import os
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from pathlib import Path

from bot.regime.classifier import RegimeClassifier
from bot.regime.cache import RegimeCache
from bot.regime.cost_tracker import CostTracker

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "classifier"


def _load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


def _mock_tool_response(tool_data: dict, input_tokens=100, output_tokens=50):
    """Build a mock Anthropic API response with tool_use block."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "classify_regime"
    tool_block.input = tool_data

    response = MagicMock()
    response.content = [tool_block]
    response.usage = MagicMock()
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    return response


class TestClassifierGolden:
    @pytest.fixture
    def setup(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        cache = RegimeCache(db_path)
        cost_tracker = CostTracker(db_path)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            classifier = RegimeClassifier(cache, cost_tracker)
        classifier._client = MagicMock()
        return classifier, cache, cost_tracker

    def test_trending_high_adx(self, setup):
        classifier, _, _ = setup
        fixture = _load_fixture("trending_high_adx.json")

        classifier._client.messages.create.return_value = _mock_tool_response(
            fixture["tool_response"]
        )

        result = classifier.classify(
            fixture["input"]["instrument"],
            fixture["input"]["trading_date"],
            fixture["input"]["features"],
        )
        assert result.raw_regime == fixture["expected"]["raw_regime"]
        assert result.confidence == fixture["expected"]["confidence"]
        assert result.cache_hit == fixture["expected"]["cache_hit"]

    def test_ranging_low_adx(self, setup):
        classifier, _, _ = setup
        fixture = _load_fixture("ranging_low_adx.json")

        classifier._client.messages.create.return_value = _mock_tool_response(
            fixture["tool_response"]
        )

        result = classifier.classify(
            fixture["input"]["instrument"],
            fixture["input"]["trading_date"],
            fixture["input"]["features"],
        )
        assert result.raw_regime == fixture["expected"]["raw_regime"]
        assert result.confidence == fixture["expected"]["confidence"]

    def test_unclear_mixed(self, setup):
        classifier, _, _ = setup
        fixture = _load_fixture("unclear_mixed.json")

        classifier._client.messages.create.return_value = _mock_tool_response(
            fixture["tool_response"]
        )

        result = classifier.classify(
            fixture["input"]["instrument"],
            fixture["input"]["trading_date"],
            fixture["input"]["features"],
        )
        assert result.raw_regime == fixture["expected"]["raw_regime"]
        assert result.confidence == fixture["expected"]["confidence"]

    def test_error_invalid_regime_falls_back(self, setup):
        classifier, _, _ = setup
        fixture = _load_fixture("error_invalid_regime.json")

        # Pydantic will reject "BULLISH" as it's not in the Literal
        classifier._client.messages.create.return_value = _mock_tool_response(
            fixture["tool_response"]
        )

        result = classifier.classify(
            fixture["input"]["instrument"],
            fixture["input"]["trading_date"],
            fixture["input"]["features"],
        )
        assert result.raw_regime == fixture["expected"]["raw_regime"]
        assert result.confidence == fixture["expected"]["confidence"]

    def test_cache_hit_on_second_call(self, setup):
        classifier, _, _ = setup
        fixture = _load_fixture("trending_high_adx.json")

        classifier._client.messages.create.return_value = _mock_tool_response(
            fixture["tool_response"]
        )

        # First call — hits API
        result1 = classifier.classify(
            fixture["input"]["instrument"],
            fixture["input"]["trading_date"],
            fixture["input"]["features"],
        )
        assert result1.cache_hit is False

        # Second call — should hit cache
        result2 = classifier.classify(
            fixture["input"]["instrument"],
            fixture["input"]["trading_date"],
            fixture["input"]["features"],
        )
        assert result2.cache_hit is True
        assert result2.raw_regime == result1.raw_regime

    def test_no_tool_use_block_falls_back(self, setup):
        classifier, _, _ = setup
        fixture = _load_fixture("trending_high_adx.json")

        # Response with no tool_use block
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "I think it's trending"
        response = MagicMock()
        response.content = [text_block]
        response.usage = MagicMock()
        response.usage.input_tokens = 100
        response.usage.output_tokens = 50
        classifier._client.messages.create.return_value = response

        result = classifier.classify(
            fixture["input"]["instrument"],
            fixture["input"]["trading_date"],
            fixture["input"]["features"],
        )
        assert result.raw_regime == "UNCLEAR"
        assert result.confidence == 0.0
