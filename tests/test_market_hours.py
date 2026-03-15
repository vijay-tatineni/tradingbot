"""
tests/test_market_hours.py
Unit tests for bot/market_hours.py — mock datetime to test open/closed logic.
"""

import datetime
import pytz
import pytest
from unittest.mock import patch, MagicMock

from bot.market_hours import MarketHours


def make_utc(year, month, day, hour, minute, weekday=None):
    """Create a timezone-aware UTC datetime."""
    return datetime.datetime(year, month, day, hour, minute, 0, tzinfo=pytz.utc)


# ── LSE tests ────────────────────────────────────────────────

def test_lse_open_during_hours():
    """Mock London time to 10:00 weekday → True."""
    mh = MarketHours()
    inst = {'sec_type': 'STK', 'currency': 'GBP', 'market': 'LSE'}
    # Wednesday 15 Jan 2025, 10:00 UTC = 10:00 London (winter, GMT)
    fake_now = make_utc(2025, 1, 15, 10, 0)
    with patch('bot.market_hours.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = fake_now
        mock_dt.datetime.fromisoformat = datetime.datetime.fromisoformat
        assert mh.is_open(inst) is True


def test_lse_closed_after_hours():
    """Mock London time to 17:00 weekday → False."""
    mh = MarketHours()
    inst = {'sec_type': 'STK', 'currency': 'GBP', 'market': 'LSE'}
    # Wednesday 15 Jan 2025, 17:00 UTC = 17:00 London (winter, GMT) — after 16:30 close
    fake_now = make_utc(2025, 1, 15, 17, 0)
    with patch('bot.market_hours.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = fake_now
        mock_dt.datetime.fromisoformat = datetime.datetime.fromisoformat
        assert mh.is_open(inst) is False


# ── US tests ─────────────────────────────────────────────────

def test_us_open_during_hours():
    """Mock NY time to 10:00 weekday → True."""
    mh = MarketHours()
    inst = {'sec_type': 'STK', 'currency': 'USD'}
    # Wednesday 15 Jan 2025, 15:00 UTC = 10:00 NY (EST, UTC-5)
    fake_now = make_utc(2025, 1, 15, 15, 0)
    with patch('bot.market_hours.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = fake_now
        mock_dt.datetime.fromisoformat = datetime.datetime.fromisoformat
        assert mh.is_open(inst) is True


def test_us_closed_weekend():
    """Saturday → False."""
    mh = MarketHours()
    inst = {'sec_type': 'STK', 'currency': 'USD'}
    # Saturday 18 Jan 2025, 15:00 UTC
    fake_now = make_utc(2025, 1, 18, 15, 0)
    with patch('bot.market_hours.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = fake_now
        mock_dt.datetime.fromisoformat = datetime.datetime.fromisoformat
        assert mh.is_open(inst) is False


# ── CFD tests ────────────────────────────────────────────────

def test_cfd_always_open():
    """CFD type always returns True, even on weekends."""
    mh = MarketHours()
    inst = {'sec_type': 'CFD', 'currency': 'USD'}
    # Saturday
    fake_now = make_utc(2025, 1, 18, 15, 0)
    with patch('bot.market_hours.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = fake_now
        mock_dt.datetime.fromisoformat = datetime.datetime.fromisoformat
        assert mh.is_open(inst) is True


# ── EUR tests ────────────────────────────────────────────────

def test_eur_open_during_hours():
    """Mock Paris time to 12:00 weekday → True."""
    mh = MarketHours()
    inst = {'sec_type': 'STK', 'currency': 'EUR'}
    # Wednesday 15 Jan 2025, 11:00 UTC = 12:00 Paris (CET, UTC+1)
    fake_now = make_utc(2025, 1, 15, 11, 0)
    with patch('bot.market_hours.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = fake_now
        mock_dt.datetime.fromisoformat = datetime.datetime.fromisoformat
        assert mh.is_open(inst) is True


# ── Holiday tests ────────────────────────────────────────────

def test_holiday_detection():
    """Mock date to Dec 25 → is_holiday returns True for UK."""
    mh = MarketHours()
    inst = {'sec_type': 'STK', 'currency': 'GBP', 'market': 'LSE'}
    # Thursday 25 Dec 2025, 10:00 UTC — Christmas Day
    fake_now = make_utc(2025, 12, 25, 10, 0)
    with patch('bot.market_hours.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = fake_now
        mock_dt.datetime.fromisoformat = datetime.datetime.fromisoformat
        is_hol, name = mh.is_holiday(inst)
        assert is_hol is True, f"Dec 25 should be a UK holiday, got is_holiday={is_hol}"
        assert 'Christmas' in name, f"Expected 'Christmas' in name, got '{name}'"
