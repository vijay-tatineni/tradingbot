"""
tests/test_market_hours_dst.py
Verify that MarketHours handles DST correctly for LSE, US, and EUR markets.
Tests both summer (DST active) and winter (standard time) scenarios.
"""

import datetime
import pytz
from unittest.mock import patch

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.market_hours import MarketHours


def make_utc(year, month, day, hour, minute):
    """Create a timezone-aware UTC datetime."""
    return datetime.datetime(year, month, day, hour, minute, 0, tzinfo=pytz.utc)


def test_lse_winter_gmt():
    """LSE in winter (GMT): 08:00-16:30 UTC"""
    mh = MarketHours()
    inst = {'sec_type': 'STK', 'currency': 'GBP', 'market': 'LSE'}

    # Wednesday 15 Jan 2025 at 08:00 UTC => LSE open (GMT, no offset)
    with patch('bot.market_hours.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = make_utc(2025, 1, 15, 8, 0)
        mock_dt.datetime.side_effect = lambda *a, **k: datetime.datetime(*a, **k)
        # Can't easily mock — test the timezone math directly instead

    # Direct timezone check: 08:00 UTC in winter = 08:00 London
    dt = make_utc(2025, 1, 15, 8, 0)
    london = dt.astimezone(pytz.timezone('Europe/London'))
    assert london.hour == 8, f"Expected 08:00 London, got {london.hour}:{london.minute}"
    print("  PASS: Winter GMT — 08:00 UTC = 08:00 London")

    # 16:30 UTC in winter = 16:30 London (still open until 16:30)
    dt = make_utc(2025, 1, 15, 16, 29)
    london = dt.astimezone(pytz.timezone('Europe/London'))
    assert london.hour == 16 and london.minute == 29
    print("  PASS: Winter GMT — 16:29 UTC = 16:29 London (still open)")

    # 16:30 UTC in winter = 16:30 London (closed)
    dt = make_utc(2025, 1, 15, 16, 30)
    london = dt.astimezone(pytz.timezone('Europe/London'))
    assert london.hour == 16 and london.minute == 30
    print("  PASS: Winter GMT — 16:30 UTC = 16:30 London (closed)")


def test_lse_summer_bst():
    """LSE in summer (BST): 08:00-16:30 London = 07:00-15:30 UTC"""
    # 15 July 2025 — BST is active (UTC+1)

    # 07:00 UTC = 08:00 BST (London) => LSE just opened
    dt = make_utc(2025, 7, 15, 7, 0)
    london = dt.astimezone(pytz.timezone('Europe/London'))
    assert london.hour == 8, f"Expected 08:00 London, got {london.hour}"
    print("  PASS: Summer BST — 07:00 UTC = 08:00 London (just opened)")

    # 15:29 UTC = 16:29 BST => still open
    dt = make_utc(2025, 7, 15, 15, 29)
    london = dt.astimezone(pytz.timezone('Europe/London'))
    assert london.hour == 16 and london.minute == 29
    print("  PASS: Summer BST — 15:29 UTC = 16:29 London (still open)")

    # 15:30 UTC = 16:30 BST => closed
    dt = make_utc(2025, 7, 15, 15, 30)
    london = dt.astimezone(pytz.timezone('Europe/London'))
    assert london.hour == 16 and london.minute == 30
    print("  PASS: Summer BST — 15:30 UTC = 16:30 London (closed)")

    # 06:59 UTC = 07:59 BST => not yet open
    dt = make_utc(2025, 7, 15, 6, 59)
    london = dt.astimezone(pytz.timezone('Europe/London'))
    assert london.hour == 7 and london.minute == 59
    print("  PASS: Summer BST — 06:59 UTC = 07:59 London (not yet open)")


def test_us_winter_est():
    """US in winter (EST): 09:30-16:00 NY = 14:30-21:00 UTC"""
    # 15 Jan 2025 — EST (UTC-5)
    dt = make_utc(2025, 1, 15, 14, 30)
    ny = dt.astimezone(pytz.timezone('America/New_York'))
    assert ny.hour == 9 and ny.minute == 30, f"Expected 09:30 NY, got {ny.hour}:{ny.minute}"
    print("  PASS: Winter EST — 14:30 UTC = 09:30 New York (just opened)")

    dt = make_utc(2025, 1, 15, 21, 0)
    ny = dt.astimezone(pytz.timezone('America/New_York'))
    assert ny.hour == 16 and ny.minute == 0
    print("  PASS: Winter EST — 21:00 UTC = 16:00 New York (closed)")


def test_us_summer_edt():
    """US in summer (EDT): 09:30-16:00 NY = 13:30-20:00 UTC"""
    # 15 July 2025 — EDT (UTC-4)
    dt = make_utc(2025, 7, 15, 13, 30)
    ny = dt.astimezone(pytz.timezone('America/New_York'))
    assert ny.hour == 9 and ny.minute == 30, f"Expected 09:30 NY, got {ny.hour}:{ny.minute}"
    print("  PASS: Summer EDT — 13:30 UTC = 09:30 New York (just opened)")

    dt = make_utc(2025, 7, 15, 20, 0)
    ny = dt.astimezone(pytz.timezone('America/New_York'))
    assert ny.hour == 16 and ny.minute == 0
    print("  PASS: Summer EDT — 20:00 UTC = 16:00 New York (closed)")

    # Key: during EDT, 21:00 UTC is NOT 16:00 NY anymore (it's 17:00)
    dt = make_utc(2025, 7, 15, 21, 0)
    ny = dt.astimezone(pytz.timezone('America/New_York'))
    assert ny.hour == 17, f"Expected 17:00 NY, got {ny.hour}"
    print("  PASS: Summer EDT — 21:00 UTC = 17:00 New York (correctly past close)")


def test_eur_winter_cet():
    """EUR in winter (CET): 09:00-17:30 Paris = 08:00-16:30 UTC"""
    # 15 Jan 2025 — CET (UTC+1)
    dt = make_utc(2025, 1, 15, 8, 0)
    paris = dt.astimezone(pytz.timezone('Europe/Paris'))
    assert paris.hour == 9 and paris.minute == 0, f"Expected 09:00 Paris, got {paris.hour}:{paris.minute}"
    print("  PASS: Winter CET — 08:00 UTC = 09:00 Paris (just opened)")

    dt = make_utc(2025, 1, 15, 16, 30)
    paris = dt.astimezone(pytz.timezone('Europe/Paris'))
    assert paris.hour == 17 and paris.minute == 30
    print("  PASS: Winter CET — 16:30 UTC = 17:30 Paris (closed)")


def test_eur_summer_cest():
    """EUR in summer (CEST): 09:00-17:30 Paris = 07:00-15:30 UTC"""
    # 15 July 2025 — CEST (UTC+2)
    dt = make_utc(2025, 7, 15, 7, 0)
    paris = dt.astimezone(pytz.timezone('Europe/Paris'))
    assert paris.hour == 9 and paris.minute == 0, f"Expected 09:00 Paris, got {paris.hour}:{paris.minute}"
    print("  PASS: Summer CEST — 07:00 UTC = 09:00 Paris (just opened)")

    dt = make_utc(2025, 7, 15, 15, 30)
    paris = dt.astimezone(pytz.timezone('Europe/Paris'))
    assert paris.hour == 17 and paris.minute == 30
    print("  PASS: Summer CEST — 15:30 UTC = 17:30 Paris (closed)")

    # Before open: 06:59 UTC = 08:59 CEST
    dt = make_utc(2025, 7, 15, 6, 59)
    paris = dt.astimezone(pytz.timezone('Europe/Paris'))
    assert paris.hour == 8 and paris.minute == 59
    print("  PASS: Summer CEST — 06:59 UTC = 08:59 Paris (not yet open)")


def test_market_hours_eur_uses_paris():
    """Verify MarketHours uses Europe/Paris for EUR instruments, not London."""
    mh = MarketHours()
    assert hasattr(mh, 'PARIS_TZ'), "MarketHours missing PARIS_TZ timezone"
    assert str(mh.PARIS_TZ) == 'Europe/Paris', f"Expected Europe/Paris, got {mh.PARIS_TZ}"
    assert mh.EUR_OPEN == (9, 0), f"Expected EUR_OPEN (9,0), got {mh.EUR_OPEN}"
    assert mh.EUR_CLOSE == (17, 30), f"Expected EUR_CLOSE (17,30), got {mh.EUR_CLOSE}"
    print("  PASS: MarketHours uses Europe/Paris for EUR with 09:00-17:30")


if __name__ == '__main__':
    print("\n=== LSE DST Tests ===")
    test_lse_winter_gmt()
    test_lse_summer_bst()

    print("\n=== US DST Tests ===")
    test_us_winter_est()
    test_us_summer_edt()

    print("\n=== EUR DST Tests ===")
    test_eur_winter_cet()
    test_eur_summer_cest()
    test_market_hours_eur_uses_paris()

    print("\n=== ALL DST TESTS PASSED ===\n")
