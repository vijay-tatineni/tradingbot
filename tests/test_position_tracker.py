"""
tests/test_position_tracker.py
Unit tests for bot/position_tracker.py — uses temp DB, no IBKR needed.

Note: The current tracker implementation's update() and check_exit() methods
are designed for LONG positions. Short-side tests verify on_open() stop placement
and the actual code behavior for short positions.
"""

import datetime
import pytest

import bot.position_tracker as pt
from bot.position_tracker import PositionTracker


class MockCfg:
    pass


@pytest.fixture
def tracker(tmp_path):
    """Create a PositionTracker with a temporary DB."""
    pt.DB_FILE = str(tmp_path / 'test_positions.db')
    return PositionTracker(MockCfg())


# ── Long trailing stop tests ────────────────────────────────

def test_long_trail_stop_moves_up(tracker):
    """Open long at 100, update to 110 → stop should rise."""
    tracker.on_open('AAPL', 100.0, qty=10, trail_stop_pct=5.0)
    initial_stop = tracker.get_stop_level('AAPL')
    assert initial_stop == pytest.approx(95.0), f"Initial stop should be 95, got {initial_stop}"

    tracker.update('AAPL', 110.0, trail_stop_pct=5.0)
    new_stop = tracker.get_stop_level('AAPL')
    assert new_stop > initial_stop, f"Stop should have risen: {new_stop} vs {initial_stop}"
    assert new_stop == pytest.approx(104.5), f"Stop at peak 110 with 5% trail should be 104.5, got {new_stop}"


def test_long_trail_stop_never_moves_down(tracker):
    """Open at 100, update to 110 then 105 → stop stays at 110-level."""
    tracker.on_open('AAPL', 100.0, qty=10, trail_stop_pct=5.0)
    tracker.update('AAPL', 110.0, trail_stop_pct=5.0)
    stop_at_peak = tracker.get_stop_level('AAPL')

    tracker.update('AAPL', 105.0, trail_stop_pct=5.0)
    stop_after_dip = tracker.get_stop_level('AAPL')
    assert stop_after_dip == stop_at_peak, (
        f"Stop should not decrease: {stop_after_dip} vs {stop_at_peak}"
    )


def test_long_take_profit(tracker):
    """Open at 100, check_exit at 110 with take_profit_pct=8 → should trigger."""
    tracker.on_open('AAPL', 100.0, qty=10, trail_stop_pct=5.0)
    reason = tracker.check_exit('AAPL', 110.0, take_profit_pct=8.0, trail_stop_pct=5.0)
    assert reason is not None, "Take profit should trigger at +10% with 8% target"
    assert 'TAKE_PROFIT' in reason


def test_long_trail_stop_triggers(tracker):
    """Open at 100, peak at 110, drop to stop level → triggers."""
    tracker.on_open('AAPL', 100.0, qty=10, trail_stop_pct=5.0)
    tracker.update('AAPL', 110.0, trail_stop_pct=5.0)
    stop = tracker.get_stop_level('AAPL')  # 104.5

    # Drop to stop level
    reason = tracker.check_exit('AAPL', stop, take_profit_pct=20.0, trail_stop_pct=5.0)
    assert reason is not None, f"Trail stop should trigger at price={stop}"
    assert 'TRAIL_STOP' in reason


# ── Short position tests ────────────────────────────────────
# The tracker fully supports SHORT positions: on_open places stop above,
# update tracks lowest price and lowers stop, check_exit uses short logic.

def test_short_trail_stop_moves_down(tracker):
    """Open short at 100, update to 90 → stop should decrease (tighter)."""
    tracker.on_open('TSLA', 100.0, qty=-10, trail_stop_pct=5.0, side='SHORT')
    initial_stop = tracker.get_stop_level('TSLA')
    # Short: stop = entry * (1 + pct/100) = 100 * 1.05 = 105
    assert initial_stop == pytest.approx(105.0), f"Initial short stop should be 105, got {initial_stop}"

    # Price drops to 90 — new low for short, stop should tighten
    tracker.update('TSLA', 90.0, trail_stop_pct=5.0)
    new_stop = tracker.get_stop_level('TSLA')
    # new_stop = 90 * 1.05 = 94.5, which is < 105, so it replaces
    assert new_stop < initial_stop, f"Stop should decrease: {new_stop} vs {initial_stop}"
    assert new_stop == pytest.approx(94.5), f"Stop at low 90 with 5% trail should be 94.5, got {new_stop}"


def test_short_trail_stop_never_moves_up(tracker):
    """Open short at 100, update to 90 then 95 → stop stays at 90-level."""
    tracker.on_open('TSLA', 100.0, qty=-10, trail_stop_pct=5.0, side='SHORT')
    tracker.update('TSLA', 90.0, trail_stop_pct=5.0)
    stop_at_low = tracker.get_stop_level('TSLA')  # 94.5

    # Price rises back to 95 — not a new low, stop should not increase
    tracker.update('TSLA', 95.0, trail_stop_pct=5.0)
    stop_after = tracker.get_stop_level('TSLA')
    assert stop_after == stop_at_low, (
        f"Short stop should not increase when price rises: {stop_after} vs {stop_at_low}"
    )


def test_short_take_profit(tracker):
    """Open short at 100, check_exit at 90 with take_profit_pct=8 → triggers.
    Short take profit: target = 100 * (1 - 0.08) = 92. Price 90 <= 92 → trigger."""
    tracker.on_open('TSLA', 100.0, qty=-10, trail_stop_pct=5.0, side='SHORT')
    reason = tracker.check_exit('TSLA', 90.0, take_profit_pct=8.0, trail_stop_pct=5.0)
    assert reason is not None, "Short take profit should trigger at price 90 with 8% target"
    assert 'TAKE_PROFIT' in reason


def test_short_trail_stop_triggers(tracker):
    """Open short at 100 (stop=105). Price rises to 106 → trail stop triggers."""
    tracker.on_open('TSLA', 100.0, qty=-10, trail_stop_pct=5.0, side='SHORT')
    # Stop is at 105. Price at 106 >= 105 → trail stop triggers
    reason = tracker.check_exit('TSLA', 106.0, take_profit_pct=50.0, trail_stop_pct=5.0)
    assert reason is not None, "Short trail stop should trigger at price 106 >= stop 105"
    assert 'TRAIL_STOP' in reason


# ── Re-entry cooldown tests ─────────────────────────────────

def test_reentry_cooldown(tracker):
    """Close position, check reentry within cooldown → should be blocked."""
    tracker.on_open('AAPL', 100.0, qty=10, trail_stop_pct=5.0)
    tracker.on_close('AAPL', exit_price=95.0, reason='TRAIL_STOP', cooldown_mins=30)

    # Immediately check re-entry — should be blocked by cooldown
    can_reenter, reason = tracker.check_reentry('AAPL', price=96.0, signal_valid=True)
    assert can_reenter is False, f"Should be blocked by cooldown, got {can_reenter}"
    assert 'Cooldown' in reason, f"Expected cooldown message, got: {reason}"


def test_reentry_after_recovery(tracker):
    """Close position, wait past cooldown, price recovers → should trigger."""
    tracker.on_open('AAPL', 100.0, qty=10, trail_stop_pct=5.0)
    tracker.on_close('AAPL', exit_price=95.0, reason='TRAIL_STOP', cooldown_mins=30)

    # Manually backdate exit_time to simulate cooldown expiry
    watch = tracker.watching['AAPL']
    past = datetime.datetime.utcnow() - datetime.timedelta(minutes=60)
    watch.exit_time = past.isoformat()
    tracker._save_watch('AAPL')

    # First call with low price to set low_since_exit
    tracker.check_reentry('AAPL', price=90.0, signal_valid=True, reentry_recovery_pct=1.5)

    # Price recovers by > 1.5% from the low of 90 → 90 * 1.015 = 91.35
    can_reenter, reason = tracker.check_reentry(
        'AAPL', price=92.0, signal_valid=True, reentry_recovery_pct=1.5,
    )
    assert can_reenter is True, f"Should trigger re-entry after recovery, got: {reason}"
    assert 'RE-ENTRY' in reason
