"""
Tests for the news headline collector.
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


def test_collect_news_returns_list():
    """collect_news('BARC') returns list of headline dicts."""
    from bot.llm.news_collector import collect_news
    with patch("bot.llm.news_collector._collect_google_news") as mock_google, \
         patch("bot.llm.news_collector._collect_finnhub_news") as mock_fh:
        mock_google.return_value = [
            {"headline": "Barclays reports Q4 earnings", "source": "Reuters",
             "published": "2024-01-15", "origin": "google"}
        ]
        mock_fh.return_value = []
        result = collect_news("BARC")

    assert isinstance(result, list)
    assert len(result) >= 1


def test_headline_has_required_fields():
    """Each headline dict has: headline, source, published, origin."""
    from bot.llm.news_collector import collect_news
    with patch("bot.llm.news_collector._collect_google_news") as mock_google, \
         patch("bot.llm.news_collector._collect_finnhub_news") as mock_fh:
        mock_google.return_value = [
            {"headline": "Test headline", "source": "Test", "published": "2024-01-01",
             "origin": "google"}
        ]
        mock_fh.return_value = []
        result = collect_news("BARC")

    for h in result:
        assert "headline" in h
        assert "source" in h
        assert "published" in h
        assert "origin" in h


def test_google_news_uses_search_terms():
    """BARC should search 'Barclays stock', not 'BARC'."""
    from bot.llm.news_collector import GOOGLE_SEARCH_TERMS
    assert GOOGLE_SEARCH_TERMS["BARC"] == "Barclays stock"
    assert GOOGLE_SEARCH_TERMS["MSFT"] == "Microsoft stock"


def test_finnhub_uses_symbol_mapping():
    """BARC should query Finnhub as 'BARC.L' (LSE suffix)."""
    from bot.llm.news_collector import FINNHUB_SYMBOLS
    assert FINNHUB_SYMBOLS["BARC"] == "BARC.L"
    assert FINNHUB_SYMBOLS["SHEL"] == "SHEL.L"


def test_finnhub_skips_commodities():
    """XAUUSD should not query Finnhub (no company news for gold)."""
    from bot.llm.news_collector import _collect_finnhub_news
    with patch.dict(os.environ, {"FINNHUB_API_KEY": "test-key"}):
        result = _collect_finnhub_news("XAUUSD")
    assert result == []


def test_finnhub_skips_without_api_key():
    """If FINNHUB_API_KEY not set, Finnhub returns empty list."""
    from bot.llm.news_collector import _collect_finnhub_news
    with patch.dict(os.environ, {"FINNHUB_API_KEY": ""}, clear=False):
        result = _collect_finnhub_news("BARC")
    assert result == []


def test_deduplication():
    """Same headline from Google and Finnhub should appear once."""
    from bot.llm.news_collector import collect_news
    headline = "Barclays reports record profits in Q4"
    with patch("bot.llm.news_collector._collect_google_news") as mock_google, \
         patch("bot.llm.news_collector._collect_finnhub_news") as mock_fh:
        mock_google.return_value = [
            {"headline": headline, "source": "Reuters", "published": "2024-01-15",
             "origin": "google"}
        ]
        mock_fh.return_value = [
            {"headline": headline, "source": "Finnhub", "published": "2024-01-15",
             "origin": "finnhub"}
        ]
        result = collect_news("BARC")

    assert len(result) == 1  # Deduplicated


def test_score_headlines_adds_sentiment():
    """After scoring, each headline has sentiment_score field."""
    from bot.llm.news_collector import score_headlines
    llm = _make_llm("+1\n0\n-1")
    headlines = [
        {"headline": "Great earnings", "source": "A", "published": "", "origin": "google"},
        {"headline": "Normal day", "source": "B", "published": "", "origin": "google"},
        {"headline": "Stock crashes", "source": "C", "published": "", "origin": "google"},
    ]
    result = score_headlines(llm, "BARC", headlines)
    assert all("sentiment_score" in h for h in result)
    assert result[0]["sentiment_score"] == 1.0
    assert result[1]["sentiment_score"] == 0.0
    assert result[2]["sentiment_score"] == -1.0


def test_score_handles_llm_error():
    """If LLM fails, sentiment_score defaults to 0."""
    from bot.llm.news_collector import score_headlines
    llm = _make_llm("")
    headlines = [
        {"headline": "Test", "source": "A", "published": "", "origin": "google"},
    ]
    result = score_headlines(llm, "BARC", headlines)
    assert result[0].get("sentiment_score", 0) == 0.0


def test_aggregate_sentiment_calculation():
    """Headlines scored +1, +1, 0 -> aggregate ~ 0.67."""
    from bot.llm.news_collector import get_aggregate_sentiment, init_news_db, save_headlines

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        init_news_db(db_path)
        headlines = [
            {"headline": "Great", "source": "A", "published": "",
             "origin": "google", "sentiment_score": 1.0},
            {"headline": "Good", "source": "B", "published": "",
             "origin": "google", "sentiment_score": 1.0},
            {"headline": "Neutral", "source": "C", "published": "",
             "origin": "google", "sentiment_score": 0.0},
        ]
        save_headlines("BARC", headlines, db_path)
        result = get_aggregate_sentiment("BARC", db_path)
        assert abs(result - 0.667) < 0.01
    finally:
        os.unlink(db_path)


def test_aggregate_sentiment_no_data():
    """No headlines in DB -> returns 0.0."""
    from bot.llm.news_collector import get_aggregate_sentiment

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        result = get_aggregate_sentiment("NONEXISTENT", db_path)
        assert result == 0.0
    finally:
        os.unlink(db_path)


def test_max_ten_headlines():
    """collect_news should cap at 10 headlines per instrument."""
    from bot.llm.news_collector import collect_news
    with patch("bot.llm.news_collector._collect_google_news") as mock_google, \
         patch("bot.llm.news_collector._collect_finnhub_news") as mock_fh:
        mock_google.return_value = [
            {"headline": f"Headline {i}", "source": "Test",
             "published": "2024-01-01", "origin": "google"}
            for i in range(15)
        ]
        mock_fh.return_value = []
        result = collect_news("BARC")

    assert len(result) <= 10
