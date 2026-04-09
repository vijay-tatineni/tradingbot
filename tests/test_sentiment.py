"""
Tests for the LLM sentiment filter.
"""
import pytest
import pandas as pd
from unittest.mock import MagicMock


def _make_bars_df(n=20):
    """Create a simple DataFrame with OHLCV data."""
    data = {
        "datetime": [f"2024-01-{i+1:02d}" for i in range(n)],
        "open": [100 + i for i in range(n)],
        "high": [102 + i for i in range(n)],
        "low": [99 + i for i in range(n)],
        "close": [101 + i for i in range(n)],
        "volume": [1000 + i * 100 for i in range(n)],
    }
    return pd.DataFrame(data)


def _make_llm(response: str):
    """Create a mock LLM that returns the given response."""
    llm = MagicMock()
    llm.is_available.return_value = True
    llm.chat.return_value = response
    return llm


def test_sentiment_confirm_proceeds():
    """CONFIRM verdict should not block the trade."""
    from bot.llm.sentiment import analyze_sentiment
    llm = _make_llm("VERDICT: CONFIRM\nCONFIDENCE: 0.8\nREASON: Strong uptrend")
    result = analyze_sentiment(llm, "BARC", _make_bars_df(), "BUY")
    assert result["verdict"] == "CONFIRM"
    assert result["confidence"] == 0.8


def test_sentiment_reject_blocks_trade():
    """REJECT with confidence > threshold should block the trade."""
    from bot.llm.sentiment import analyze_sentiment
    llm = _make_llm("VERDICT: REJECT\nCONFIDENCE: 0.9\nREASON: Bearish reversal")
    result = analyze_sentiment(llm, "BARC", _make_bars_df(), "BUY")
    assert result["verdict"] == "REJECT"
    assert result["confidence"] == 0.9


def test_sentiment_caution_proceeds():
    """CAUTION verdict should proceed (with warning logged)."""
    from bot.llm.sentiment import analyze_sentiment
    llm = _make_llm("VERDICT: CAUTION\nCONFIDENCE: 0.6\nREASON: Mixed signals")
    result = analyze_sentiment(llm, "BARC", _make_bars_df(), "BUY")
    assert result["verdict"] == "CAUTION"


def test_sentiment_reject_below_threshold_proceeds():
    """REJECT with confidence < threshold should proceed."""
    from bot.llm.sentiment import analyze_sentiment
    llm = _make_llm("VERDICT: REJECT\nCONFIDENCE: 0.4\nREASON: Weak signal")
    result = analyze_sentiment(llm, "BARC", _make_bars_df(), "BUY")
    assert result["verdict"] == "REJECT"
    assert result["confidence"] == 0.4
    # The verdict is REJECT but confidence is low — caller should check threshold


def test_sentiment_llm_unavailable_proceeds():
    """If LLM is unavailable, trade proceeds normally."""
    from bot.llm.sentiment import analyze_sentiment
    result = analyze_sentiment(None, "BARC", _make_bars_df(), "BUY")
    assert result["verdict"] == "CONFIRM"
    assert result["confidence"] == 0.0
    assert result["reason"] == "LLM unavailable"


def test_sentiment_llm_error_proceeds():
    """If LLM returns error/empty, trade proceeds normally."""
    from bot.llm.sentiment import analyze_sentiment
    llm = _make_llm("")
    result = analyze_sentiment(llm, "BARC", _make_bars_df(), "BUY")
    assert result["verdict"] == "CONFIRM"


def test_sentiment_parses_verdict_correctly():
    """Parse 'VERDICT: CONFIRM\nCONFIDENCE: 0.8\nREASON: ...'"""
    from bot.llm.sentiment import _parse_response
    result = _parse_response(
        "VERDICT: CONFIRM\nCONFIDENCE: 0.85\nREASON: Strong bullish momentum"
    )
    assert result["verdict"] == "CONFIRM"
    assert result["confidence"] == 0.85
    assert "bullish momentum" in result["reason"]


def test_sentiment_handles_malformed_response():
    """If LLM returns unexpected format, default to CONFIRM."""
    from bot.llm.sentiment import _parse_response
    result = _parse_response("I'm not sure about this trade")
    assert result["verdict"] == "CONFIRM"  # No verdict keyword found, defaults to CONFIRM


def test_sentiment_includes_bars_in_prompt():
    """The prompt should contain formatted OHLCV bars."""
    from bot.llm.sentiment import analyze_sentiment
    llm = _make_llm("VERDICT: CONFIRM\nCONFIDENCE: 0.5\nREASON: ok")
    analyze_sentiment(llm, "BARC", _make_bars_df(), "BUY")

    # Check the prompt sent to the LLM
    call_args = llm.chat.call_args[0][0]
    user_msg = call_args[1]["content"]
    assert "Symbol: BARC" in user_msg
    assert "BUY" in user_msg
    # Should contain bar data
    assert "100.00" in user_msg


def test_sentiment_parses_fuzzy_response():
    """LLM responds with 'I would REJECT this trade because...'
    instead of exact format — should still parse REJECT verdict."""
    from bot.llm.sentiment import _parse_response
    result = _parse_response(
        "I would REJECT this trade because the price is showing "
        "clear bearish signals with CONFIDENCE around 0.75."
    )
    assert result["verdict"] == "REJECT"
    assert result["confidence"] == 0.75
