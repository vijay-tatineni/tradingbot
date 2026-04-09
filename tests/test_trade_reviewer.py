"""
Tests for the LLM trade reviewer.
"""
import os
import sqlite3
import tempfile
import pytest
from unittest.mock import MagicMock


def _make_llm(response: str):
    llm = MagicMock()
    llm.is_available.return_value = True
    llm.chat.return_value = response
    return llm


def _sample_trade_data():
    return {
        "symbol": "BARC",
        "entry_price": 100.0,
        "exit_price": 105.0,
        "pnl": 50.0,
        "outcome": "WIN",
        "hold_days": 3,
        "exit_reason": "TRAIL_STOP",
        "action": "BUY",
        "indicators_at_entry": {"rsi": 45, "adx": 25},
    }


def _sample_bars():
    return [
        {"datetime": f"2024-01-{i+1:02d}", "open": 100+i, "high": 102+i,
         "low": 99+i, "close": 101+i, "volume": 1000}
        for i in range(10)
    ]


def test_review_produces_analysis():
    """review_trade() should return analysis text."""
    from bot.llm.reviewer import review_trade
    llm = _make_llm(
        "ANALYSIS: Good entry timing on breakout. Price moved strongly in direction.\n"
        "ENTRY_QUALITY: GOOD\nEXIT_QUALITY: FAIR\n"
        "PATTERN: Breakout above resistance\n"
        "LESSON: Hold winners longer when ADX is rising"
    )
    result = review_trade(llm, _sample_trade_data(), _sample_bars(), _sample_bars())
    assert "analysis" in result
    assert len(result["analysis"]) > 0


def test_review_rates_entry_quality():
    """Should return GOOD, FAIR, or POOR for entry quality."""
    from bot.llm.reviewer import review_trade
    llm = _make_llm(
        "ANALYSIS: Well-timed entry.\n"
        "ENTRY_QUALITY: GOOD\nEXIT_QUALITY: POOR\n"
        "PATTERN: Trend continuation\n"
        "LESSON: Exit earlier when momentum fades"
    )
    result = review_trade(llm, _sample_trade_data(), _sample_bars(), _sample_bars())
    assert result["entry_quality"] in ("GOOD", "FAIR", "POOR")


def test_review_saves_to_database():
    """Review should be persisted to trade_reviews table."""
    from bot.plugins.learning_loop import LearningLoop

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        # Create a minimal config mock
        cfg = MagicMock()
        cfg._raw = {"settings": {"llm_review_enabled": False}}

        loop = LearningLoop(cfg)
        loop.db = db_path
        loop._init_db()

        # Manually save a review
        review = {
            "analysis": "Good trade",
            "entry_quality": "GOOD",
            "exit_quality": "FAIR",
            "pattern_at_entry": "Breakout",
            "suggestion": "Hold longer",
            "raw_response": "...",
        }
        loop._save_review(1, "BARC", review)

        # Check it was saved
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT * FROM trade_reviews WHERE trade_id=1")
        row = cursor.fetchone()
        conn.close()
        assert row is not None
    finally:
        os.unlink(db_path)


def test_review_handles_llm_error():
    """If LLM fails, review is skipped, no crash."""
    from bot.llm.reviewer import review_trade
    llm = _make_llm("")
    result = review_trade(llm, _sample_trade_data(), _sample_bars(), _sample_bars())
    assert result["analysis"] is not None  # Should return default, not crash


def test_review_parses_structured_response():
    """Parse ANALYSIS: ...\nENTRY_QUALITY: ...\nLESSON: ..."""
    from bot.llm.reviewer import _parse_review
    result = _parse_review(
        "ANALYSIS: The trade entered on a pullback to support.\n"
        "ENTRY_QUALITY: GOOD\n"
        "EXIT_QUALITY: FAIR\n"
        "PATTERN: Pullback to MA50\n"
        "LESSON: Consider scaling out at 50% of target"
    )
    assert result["entry_quality"] == "GOOD"
    assert result["exit_quality"] == "FAIR"
    assert "pullback" in result["pattern_at_entry"].lower()
    assert len(result["lessons"]) == 1


def test_review_handles_malformed_response():
    """Malformed LLM response should not crash."""
    from bot.llm.reviewer import _parse_review
    result = _parse_review("This is just random text with no structure at all.")
    assert result is not None
    assert "analysis" in result


def test_review_includes_trade_details():
    """Prompt should contain entry/exit price, P&L, hold days."""
    from bot.llm.reviewer import review_trade
    llm = _make_llm("ANALYSIS: ok\nENTRY_QUALITY: FAIR\nEXIT_QUALITY: FAIR\nPATTERN: x\nLESSON: y")
    review_trade(llm, _sample_trade_data(), _sample_bars(), _sample_bars())

    call_args = llm.chat.call_args[0][0]
    user_msg = call_args[1]["content"]
    assert "100.0" in user_msg   # entry_price
    assert "105.0" in user_msg   # exit_price
    assert "50.00" in user_msg   # pnl
    assert "3" in user_msg       # hold_days


def test_review_includes_bars():
    """Prompt should contain bars at entry and exit."""
    from bot.llm.reviewer import review_trade
    llm = _make_llm("ANALYSIS: ok\nENTRY_QUALITY: FAIR\nEXIT_QUALITY: FAIR\nPATTERN: x\nLESSON: y")
    review_trade(llm, _sample_trade_data(), _sample_bars(), _sample_bars())

    call_args = llm.chat.call_args[0][0]
    user_msg = call_args[1]["content"]
    assert "Bars before entry" in user_msg
    assert "Bars before exit" in user_msg
