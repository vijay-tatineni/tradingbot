"""Tier-2 (trailing stop / take profit) evaluation cadence.

The defect: ``is_bar_close('daily', ...)`` is true only for WINDOW_MINUTES
after the daily bar close, and the daily bar close *is* the market close
(US 16:00 ET, LSE 16:30). ``layer1._process_instrument`` returns early when the
market is shut, so a cycle never runs inside that window. The two conditions
never overlap, so Tier-2 for daily-timeframe instruments evaluated **never** --
trailing stops and take-profits were dead code for those names.

``test_daily_tier2_*`` below fail against the old behaviour (Tier-2 gated
directly on ``is_bar_close``) and pass with ``should_evaluate_tier2``.
"""

import datetime

import pytz

from bot.bar_schedule import (
    US_DAILY_CLOSE, WINDOW_MINUTES, is_bar_close, should_evaluate_tier2,
)

NEW_YORK = pytz.timezone("America/New_York")
LONDON = pytz.timezone("Europe/London")

US_DAILY = {"symbol": "AAPL", "market": "SMART", "currency": "USD"}
US_4HR = {"symbol": "AAPL", "market": "SMART", "currency": "USD"}
LSE_DAILY = {"symbol": "BARC", "market": "LSE", "currency": "GBP"}


def _utc(tz, year, month, day, hour, minute):
    return tz.localize(
        datetime.datetime(year, month, day, hour, minute)
    ).astimezone(pytz.utc)


# A Wednesday, comfortably inside the US session.
MIDSESSION = _utc(NEW_YORK, 2026, 7, 15, 14, 0)
# The only moment the old daily gate opened — after the close.
AFTER_US_CLOSE = _utc(NEW_YORK, 2026, 7, 15, 16, 2)


# ── the defect, demonstrated ─────────────────────────────────────────

def test_daily_bar_close_window_only_opens_after_the_market_shuts():
    """The window exists, but only once trading has ended for the day."""
    assert is_bar_close("daily", US_DAILY, AFTER_US_CLOSE) is True
    assert US_DAILY_CLOSE == (16, 0), "daily close is the market close"


def test_daily_bar_close_is_false_all_through_the_session():
    """Every cycle that actually runs sees False — hence 'never'."""
    for hour, minute in [(9, 35), (11, 0), (13, 30), (14, 0), (15, 59)]:
        moment = _utc(NEW_YORK, 2026, 7, 15, hour, minute)
        assert is_bar_close("daily", US_DAILY, moment) is False


def test_daily_tier2_now_evaluates_mid_session():
    """Fails on the old behaviour: is_bar_close is False here."""
    assert is_bar_close("daily", US_DAILY, MIDSESSION) is False
    assert should_evaluate_tier2("daily", US_DAILY, MIDSESSION) is True


def test_daily_tier2_evaluates_at_every_point_in_the_session():
    for hour, minute in [(9, 30), (10, 15), (12, 0), (14, 45), (15, 59)]:
        moment = _utc(NEW_YORK, 2026, 7, 15, hour, minute)
        assert should_evaluate_tier2("daily", US_DAILY, moment) is True


def test_lse_daily_tier2_also_evaluates_mid_session():
    """Same defect, different exchange: LSE daily close is 16:30 London."""
    moment = _utc(LONDON, 2026, 7, 15, 11, 0)
    assert is_bar_close("daily", LSE_DAILY, moment) is False
    assert should_evaluate_tier2("daily", LSE_DAILY, moment) is True


# ── 4hr behaviour is deliberately unchanged ──────────────────────────

def test_4hr_still_gates_on_the_bar_close_window():
    """4hr names keep window semantics — their 12:00 boundary is reachable."""
    at_boundary = _utc(NEW_YORK, 2026, 7, 15, 12, 1)
    assert is_bar_close("4hr", US_4HR, at_boundary) is True
    assert should_evaluate_tier2("4hr", US_4HR, at_boundary) is True


def test_4hr_does_not_evaluate_away_from_a_boundary():
    away = _utc(NEW_YORK, 2026, 7, 15, 14, 0)
    assert should_evaluate_tier2("4hr", US_4HR, away) is False


def test_4hr_window_closes_after_window_minutes():
    inside = _utc(NEW_YORK, 2026, 7, 15, 12, WINDOW_MINUTES - 1)
    outside = _utc(NEW_YORK, 2026, 7, 15, 12, WINDOW_MINUTES + 2)
    assert should_evaluate_tier2("4hr", US_4HR, inside) is True
    assert should_evaluate_tier2("4hr", US_4HR, outside) is False


# ── dead band ────────────────────────────────────────────────────────

def test_daily_dead_band_is_one_cycle_not_unbounded():
    """Worst case between evaluations, within a session, is one cycle.

    Simulated at the configured 1-minute interval across a full US session:
    previously zero evaluations (unbounded dead band), now one per cycle.
    """
    session_start = _utc(NEW_YORK, 2026, 7, 15, 9, 30)
    old_hits = new_hits = 0
    for minute in range(390):                    # 09:30 → 16:00
        moment = session_start + datetime.timedelta(minutes=minute)
        old_hits += bool(is_bar_close("daily", US_DAILY, moment))
        new_hits += bool(should_evaluate_tier2("daily", US_DAILY, moment))

    assert old_hits == 0, "old behaviour never evaluated during the session"
    assert new_hits == 390, "new behaviour evaluates every cycle"
