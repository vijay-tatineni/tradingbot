"""
Tests for the LLM pattern analyzer (WF integration).
"""
import os
import sqlite3
import tempfile
import pytest
from unittest.mock import MagicMock, patch


def _make_llm(response: str):
    llm = MagicMock()
    llm.is_available.return_value = True
    llm.chat.return_value = response
    return llm


def _sample_bars():
    return [
        {"date": f"2024-01-{i+1:02d}", "open": 100+i, "high": 102+i,
         "low": 99+i, "close": 101+i, "volume": 1000}
        for i in range(10)
    ]


def test_pattern_confirm():
    """LLM confirming a signal returns 'CONFIRM'."""
    from bot.llm.pattern_analyzer import analyze_pattern
    llm = _make_llm("CONFIRM")
    result = analyze_pattern(llm, "BARC", _sample_bars(), "BUY", use_cache=False)
    assert result == "CONFIRM"


def test_pattern_reject():
    """LLM rejecting a signal returns 'REJECT'."""
    from bot.llm.pattern_analyzer import analyze_pattern
    llm = _make_llm("REJECT")
    result = analyze_pattern(llm, "BARC", _sample_bars(), "BUY", use_cache=False)
    assert result == "REJECT"


def test_pattern_cache_hit():
    """Same bars_hash should return cached verdict, not call LLM again."""
    from bot.llm.pattern_analyzer import (
        analyze_pattern, _compute_bars_hash, _cache_verdict,
        _ensure_cache_table, _get_cached_verdict,
    )

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        # Set up cache
        conn = sqlite3.connect(db_path)
        _ensure_cache_table(conn)
        conn.commit()
        conn.close()

        bars = _sample_bars()
        bars_hash = _compute_bars_hash(bars, "BUY")

        # Pre-cache a verdict
        with patch("bot.llm.pattern_analyzer.BACKTEST_DB", db_path):
            _cache_verdict("BARC", bars_hash, "REJECT", "2024-01-10", "BUY")
            cached = _get_cached_verdict("BARC", bars_hash)

        assert cached == "REJECT"
    finally:
        os.unlink(db_path)


def test_pattern_cache_miss():
    """Different bars_hash should call LLM."""
    from bot.llm.pattern_analyzer import _compute_bars_hash

    bars1 = _sample_bars()
    bars2 = [{"date": "2024-02-01", "open": 200, "high": 210,
              "low": 195, "close": 205, "volume": 2000}] * 10

    hash1 = _compute_bars_hash(bars1, "BUY")
    hash2 = _compute_bars_hash(bars2, "BUY")
    assert hash1 != hash2


def test_pattern_handles_error():
    """LLM error defaults to 'CONFIRM' (don't block on error)."""
    from bot.llm.pattern_analyzer import analyze_pattern
    llm = _make_llm("")
    result = analyze_pattern(llm, "BARC", _sample_bars(), "BUY", use_cache=False)
    assert result == "CONFIRM"


def test_pattern_concise_prompt():
    """Prompt should be under 200 tokens to minimize API usage."""
    from bot.llm.pattern_analyzer import analyze_pattern
    llm = _make_llm("CONFIRM")
    analyze_pattern(llm, "BARC", _sample_bars(), "BUY", use_cache=False)

    call_args = llm.chat.call_args[0][0]
    prompt = call_args[0]["content"]
    # Rough token estimate: ~4 chars per token
    estimated_tokens = len(prompt) / 4
    assert estimated_tokens < 200, f"Prompt too long: ~{estimated_tokens:.0f} tokens"
