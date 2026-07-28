"""The final 4hr bar of a session was never evaluated (PR C2).

Same defect class as PR C. A 4hr boundary at or after the market close cannot
be observed through a post-close window, because layer1._process_instrument
returns early once the market is shut:

    US : 4hr closes 12:00 / 16:00, market closes 16:00 -> 16:00 unreachable
    LSE: 4hr closes 13:00 / 17:00, market closes 16:30 -> 17:00 unreachable
    EUR: 4hr closes 13:00 / 17:00, market closes 17:30 -> both reachable

So US and LSE 4hr names got one Tier-2 evaluation per day instead of two, and
the last bar of every session was never acted on.

The ``*_now_evaluates_*`` tests below fail against the old behaviour.
"""

import datetime

import pytz

from bot.bar_schedule import (
    WINDOW_MINUTES, exchange_schedule, has_unreachable_4hr_boundary,
    is_bar_close, should_evaluate_tier2,
)

NEW_YORK = pytz.timezone("America/New_York")
LONDON = pytz.timezone("Europe/London")
PARIS = pytz.timezone("Europe/Paris")

US = {"symbol": "AAPL", "market": "SMART", "currency": "USD"}
LSE = {"symbol": "BARC", "market": "LSE", "currency": "GBP"}
EUR = {"symbol": "ASML", "market": "AEB", "currency": "EUR"}


def _utc(tz, hour, minute):
    return tz.localize(
        datetime.datetime(2026, 7, 15, hour, minute)
    ).astimezone(pytz.utc)


# ── which exchanges have the defect ──────────────────────────────────

def test_us_and_lse_have_an_unreachable_boundary_eur_does_not():
    assert has_unreachable_4hr_boundary(US) is True
    assert has_unreachable_4hr_boundary(LSE) is True
    assert has_unreachable_4hr_boundary(EUR) is False


def test_the_unreachable_boundary_is_at_or_after_the_market_close():
    for inst in (US, LSE):
        _tz, four_hr, daily = exchange_schedule(inst)
        assert any(b >= daily for b in four_hr)


# ── the defect, demonstrated ─────────────────────────────────────────

def test_us_final_bar_window_only_opens_after_trading_has_ended():
    """16:02 ET is inside the old window, but the market shut at 16:00."""
    assert is_bar_close("4hr", US, _utc(NEW_YORK, 16, 2)) is True


def test_lse_final_bar_window_opens_half_an_hour_after_the_close():
    """LSE is worse: 17:00 is 30 minutes after the 16:30 close."""
    assert is_bar_close("4hr", LSE, _utc(LONDON, 17, 2)) is True


# ── the fix: evaluate just before the close instead ──────────────────

def test_us_4hr_now_evaluates_just_before_the_close():
    """Fails on the old behaviour: is_bar_close is False at 15:57."""
    moment = _utc(NEW_YORK, 15, 57)
    assert is_bar_close("4hr", US, moment) is False
    assert should_evaluate_tier2("4hr", US, moment) is True


def test_lse_4hr_now_evaluates_just_before_its_1630_close():
    moment = _utc(LONDON, 16, 27)
    assert is_bar_close("4hr", LSE, moment) is False
    assert should_evaluate_tier2("4hr", LSE, moment) is True


def test_preclose_window_is_window_minutes_wide():
    inside = _utc(NEW_YORK, 16, 0) - datetime.timedelta(minutes=WINDOW_MINUTES - 1)
    outside = _utc(NEW_YORK, 16, 0) - datetime.timedelta(minutes=WINDOW_MINUTES + 2)
    assert should_evaluate_tier2("4hr", US, inside) is True
    assert should_evaluate_tier2("4hr", US, outside) is False


# ── nothing else moves ───────────────────────────────────────────────

def test_reachable_midday_boundary_is_unchanged():
    assert should_evaluate_tier2("4hr", US, _utc(NEW_YORK, 12, 1)) is True
    assert should_evaluate_tier2("4hr", LSE, _utc(LONDON, 13, 1)) is True


def test_quiet_midsession_still_does_not_evaluate():
    assert should_evaluate_tier2("4hr", US, _utc(NEW_YORK, 14, 0)) is False
    assert should_evaluate_tier2("4hr", LSE, _utc(LONDON, 11, 0)) is False


def test_eur_gains_no_preclose_window_because_it_needs_none():
    """EUR's 17:00 boundary is already reachable before the 17:30 close."""
    assert should_evaluate_tier2("4hr", EUR, _utc(PARIS, 17, 27)) is False
    assert should_evaluate_tier2("4hr", EUR, _utc(PARIS, 17, 2)) is True


def test_daily_behaviour_from_pr_c_is_untouched():
    assert should_evaluate_tier2("daily", US, _utc(NEW_YORK, 14, 0)) is True


def test_plugin_bar_close_signal_is_not_changed_by_this_fix():
    """is_bar_close feeds on_instrument_tick; C2 must not alter it."""
    assert is_bar_close("4hr", US, _utc(NEW_YORK, 15, 57)) is False
    assert is_bar_close("4hr", US, _utc(NEW_YORK, 12, 1)) is True


def test_us_4hr_gets_two_evaluations_per_session_not_one():
    """The whole point: count evaluations across a full session."""
    start = _utc(NEW_YORK, 9, 30)
    old = new = 0
    for minute in range(390):                      # 09:30 -> 16:00
        moment = start + datetime.timedelta(minutes=minute)
        old += bool(is_bar_close("4hr", US, moment))
        new += bool(should_evaluate_tier2("4hr", US, moment))

    assert old > 0 and new > old
    # Old: only the 12:00 window is observable. New: 12:00 plus pre-close.
    assert old == WINDOW_MINUTES + 1
    assert new == 2 * WINDOW_MINUTES + 1
