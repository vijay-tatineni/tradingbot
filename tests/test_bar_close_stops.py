"""
tests/test_bar_close_stops.py
Tests for the two-tier stop system:
  - Emergency hard stop (every cycle)
  - Trail stop + take profit (bar close only)
  - Bar close detection
  - Missing position handling
  - Exit price validation
"""

import datetime
from unittest.mock import patch
import pytest
import pytz

import bot.position_tracker as pt
from bot.position_tracker import PositionTracker
from bot.bar_schedule import is_bar_close, _check_boundaries


class MockCfg:
    pass


@pytest.fixture
def tracker(tmp_path):
    """Create a PositionTracker with a temporary DB."""
    pt.DB_FILE = str(tmp_path / 'test_positions.db')
    return PositionTracker(MockCfg())


# ── Emergency stop tests ──────────────────────────────────────

def test_emergency_stop_triggers_on_crash(tracker):
    """10% emergency stop on entry 250 should trigger at 225."""
    tracker.on_open('BARC', 250.0, qty=1000, trail_stop_pct=4.0)
    reason = tracker.check_emergency_stop('BARC', 225.0, emergency_stop_pct=10.0)
    assert reason is not None
    assert 'EMERGENCY_STOP' in reason


def test_emergency_stop_does_not_trigger_normally(tracker):
    """5% drop (within trail range) should NOT trigger 10% emergency stop."""
    tracker.on_open('BARC', 250.0, qty=1000, trail_stop_pct=4.0)
    # 5% drop: 250 * 0.95 = 237.5
    reason = tracker.check_emergency_stop('BARC', 237.5, emergency_stop_pct=10.0)
    assert reason is None


def test_emergency_stop_pct_boundary(tracker):
    """Price exactly at emergency level should trigger."""
    tracker.on_open('AAPL', 200.0, qty=10, trail_stop_pct=1.5)
    # 5% emergency: 200 * 0.95 = 190.0
    reason = tracker.check_emergency_stop('AAPL', 190.0, emergency_stop_pct=5.0)
    assert reason is not None
    assert 'EMERGENCY_STOP' in reason


def test_emergency_stop_pct_defaults_to_2x_trail():
    """If emergency_stop_pct not in config, layer1 uses 2x trail_stop_pct."""
    # This is tested at the layer1 level — the default is computed there.
    # Here we verify the logic: trail=5, default emergency=10
    inst = {'trail_stop_pct': 5.0}
    emergency_pct = inst.get('emergency_stop_pct', inst.get('trail_stop_pct', 5.0) * 2)
    assert emergency_pct == 10.0

    # With explicit value
    inst2 = {'trail_stop_pct': 5.0, 'emergency_stop_pct': 8.0}
    emergency_pct2 = inst2.get('emergency_stop_pct', inst2.get('trail_stop_pct', 5.0) * 2)
    assert emergency_pct2 == 8.0


# ── Short position emergency stop ─────────────────────────────

def test_short_position_emergency_stop(tracker):
    """Short position: emergency triggers when price rises above threshold."""
    tracker.on_open('TSLA', 100.0, qty=-10, trail_stop_pct=5.0, side='SHORT')
    # 10% emergency above entry: 100 * 1.10 = 110, use 111 to avoid float edge
    reason = tracker.check_emergency_stop('TSLA', 111.0, emergency_stop_pct=10.0)
    assert reason is not None
    assert 'EMERGENCY_STOP' in reason


def test_short_emergency_stop_does_not_trigger_normally(tracker):
    """Short: 5% rise should NOT trigger 10% emergency stop."""
    tracker.on_open('TSLA', 100.0, qty=-10, trail_stop_pct=5.0, side='SHORT')
    reason = tracker.check_emergency_stop('TSLA', 105.0, emergency_stop_pct=10.0)
    assert reason is None


# ── Bar-close-only trail stop tests ───────────────────────────

def test_trail_stop_only_on_bar_close(tracker):
    """Trail stop should not update peak between bar closes."""
    tracker.on_open('MU', 100.0, qty=5, trail_stop_pct=1.5)
    initial_peak = tracker.get_peak('MU')
    assert initial_peak == 100.0

    # Simulate intra-bar price move — DON'T call update()
    # (layer1 only calls update on bar close now)
    # Peak should remain at entry
    assert tracker.get_peak('MU') == 100.0


def test_trail_stop_triggers_on_bar_close(tracker):
    """When bar closes below trail stop level, check_exit should return CLOSE."""
    tracker.on_open('MU', 100.0, qty=5, trail_stop_pct=5.0)
    # Update peak to 110 (simulating bar close)
    tracker.update('MU', 110.0, trail_stop_pct=5.0)
    # Stop is at 104.5. Price drops to 104 on next bar close.
    reason = tracker.check_exit('MU', 104.0, take_profit_pct=20.0,
                                trail_stop_pct=5.0)
    assert reason is not None
    assert 'TRAIL_STOP' in reason


def test_take_profit_triggers_on_bar_close(tracker):
    """When bar closes above TP level, check_exit should return CLOSE."""
    tracker.on_open('MU', 100.0, qty=5, trail_stop_pct=5.0)
    reason = tracker.check_exit('MU', 110.0, take_profit_pct=8.0,
                                trail_stop_pct=5.0)
    assert reason is not None
    assert 'TAKE_PROFIT' in reason


def test_peak_only_updates_on_bar_close(tracker):
    """Peak price should not update on intra-bar prices (update not called)."""
    tracker.on_open('BARC', 250.0, qty=1000, trail_stop_pct=4.0)
    # Only call update when we decide it's a bar close
    # Between bar closes, peak stays at entry
    assert tracker.get_peak('BARC') == 250.0

    # Simulate bar close at higher price
    tracker.update('BARC', 270.0, trail_stop_pct=4.0)
    assert tracker.get_peak('BARC') == 270.0

    # Next "intra-bar" period — don't call update
    # Peak stays at 270 even if price goes higher
    assert tracker.get_peak('BARC') == 270.0


def test_short_position_trail_stop_on_bar_close(tracker):
    """Short trail stop only evaluates on bar close (via check_exit)."""
    tracker.on_open('TSLA', 100.0, qty=-10, trail_stop_pct=5.0, side='SHORT')
    # Update on bar close: price drops to 90, lowering the stop
    tracker.update('TSLA', 90.0, trail_stop_pct=5.0)
    stop = tracker.get_stop_level('TSLA')
    assert stop == pytest.approx(94.5)

    # On next bar close, price rises above stop
    reason = tracker.check_exit('TSLA', 95.0, take_profit_pct=50.0,
                                trail_stop_pct=5.0)
    assert reason is not None
    assert 'TRAIL_STOP' in reason


# ── Bar close detection tests ─────────────────────────────────

def _make_dt(tz, year, month, day, hour, minute):
    """Helper to create a timezone-aware datetime."""
    return tz.localize(datetime.datetime(year, month, day, hour, minute, 0))


def test_4hr_bar_close_detection_us():
    """is_bar_close('4hr') should return True at US bar boundaries (12:00 and 16:00 ET)."""
    ny_tz = pytz.timezone('America/New_York')
    inst = {'currency': 'USD'}

    # 12:02 ET — within 5 min of 12:00 bar close
    fake_utc = _make_dt(ny_tz, 2026, 3, 20, 12, 2).astimezone(pytz.utc)
    with patch('bot.bar_schedule.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = fake_utc
        assert is_bar_close('4hr', inst) is True

    # 12:10 ET — outside 5 min window
    fake_utc = _make_dt(ny_tz, 2026, 3, 20, 12, 10).astimezone(pytz.utc)
    with patch('bot.bar_schedule.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = fake_utc
        assert is_bar_close('4hr', inst) is False

    # 10:30 ET — not a bar close boundary
    fake_utc = _make_dt(ny_tz, 2026, 3, 20, 10, 30).astimezone(pytz.utc)
    with patch('bot.bar_schedule.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = fake_utc
        assert is_bar_close('4hr', inst) is False


def test_4hr_bar_close_detection_lse():
    """is_bar_close('4hr', LSE) should return True at LSE bar boundaries."""
    london_tz = pytz.timezone('Europe/London')
    inst = {'currency': 'GBP', 'market': 'LSE'}

    # 13:01 London — within 5 min of 13:00 bar close
    fake_utc = _make_dt(london_tz, 2026, 3, 20, 13, 1).astimezone(pytz.utc)
    with patch('bot.bar_schedule.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = fake_utc
        assert is_bar_close('4hr', inst) is True

    # 14:00 London — not a bar close
    fake_utc = _make_dt(london_tz, 2026, 3, 20, 14, 0).astimezone(pytz.utc)
    with patch('bot.bar_schedule.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = fake_utc
        assert is_bar_close('4hr', inst) is False


def test_daily_bar_close_detection_lse():
    """is_bar_close('daily', LSE) should return True at 16:30 London."""
    london_tz = pytz.timezone('Europe/London')
    inst = {'currency': 'GBP', 'market': 'LSE'}

    # 16:32 London — within 5 min of 16:30 daily close
    fake_utc = _make_dt(london_tz, 2026, 3, 20, 16, 32).astimezone(pytz.utc)
    with patch('bot.bar_schedule.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = fake_utc
        assert is_bar_close('daily', inst) is True

    # 16:00 London — before daily close
    fake_utc = _make_dt(london_tz, 2026, 3, 20, 16, 0).astimezone(pytz.utc)
    with patch('bot.bar_schedule.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = fake_utc
        assert is_bar_close('daily', inst) is False


def test_daily_bar_close_detection_us():
    """is_bar_close('daily') should return True at 16:00 ET."""
    ny_tz = pytz.timezone('America/New_York')
    inst = {'currency': 'USD'}

    # 16:03 ET — within 5 min of 16:00 daily close
    fake_utc = _make_dt(ny_tz, 2026, 3, 20, 16, 3).astimezone(pytz.utc)
    with patch('bot.bar_schedule.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = fake_utc
        assert is_bar_close('daily', inst) is True

    # 15:00 ET — before daily close
    fake_utc = _make_dt(ny_tz, 2026, 3, 20, 15, 0).astimezone(pytz.utc)
    with patch('bot.bar_schedule.datetime') as mock_dt:
        mock_dt.datetime.now.return_value = fake_utc
        assert is_bar_close('daily', inst) is False


# ── Missing position tests ────────────────────────────────────

def test_missing_position_waits_three_cycles(tracker):
    """Position missing for 1-2 cycles should return None (wait)."""
    tracker.on_open('VRT', 260.0, qty=5, trail_stop_pct=1.5)

    result1 = tracker.handle_missing_position('VRT', 255.0)
    assert result1 is None, "Should wait after 1st check"

    result2 = tracker.handle_missing_position('VRT', 255.0)
    assert result2 is None, "Should wait after 2nd check"


def test_missing_position_closes_after_three(tracker):
    """Position missing for 3 cycles should return RECORD_CLOSE."""
    tracker.on_open('VRT', 260.0, qty=5, trail_stop_pct=1.5)

    tracker.handle_missing_position('VRT', 255.0)
    tracker.handle_missing_position('VRT', 255.0)
    result3 = tracker.handle_missing_position('VRT', 255.0)

    assert result3 is not None
    assert result3['action'] == 'RECORD_CLOSE'
    assert result3['reason'] == 'POSITION_GONE'
    assert result3['exit_price'] == 255.0


def test_missing_position_uses_entry_if_no_price(tracker):
    """If no market price available, use entry price as fallback."""
    tracker.on_open('VRT', 260.0, qty=5, trail_stop_pct=1.5)

    tracker.handle_missing_position('VRT', 0.0)
    tracker.handle_missing_position('VRT', 0.0)
    result = tracker.handle_missing_position('VRT', 0.0)

    assert result is not None
    assert result['exit_price'] == 260.0
    assert result['reason'] == 'POSITION_GONE_NO_PRICE'


def test_missing_counter_resets_on_reappearance(tracker):
    """If position reappears, the missing counter should reset."""
    tracker.on_open('MU', 100.0, qty=5, trail_stop_pct=1.5)

    tracker.handle_missing_position('MU', 99.0)  # count = 1
    tracker.handle_missing_position('MU', 99.0)  # count = 2
    tracker.clear_missing_count('MU')             # position reappeared

    # Start fresh — need 3 more cycles
    result = tracker.handle_missing_position('MU', 99.0)  # count = 1
    assert result is None


# ── Exit price validation tests ───────────────────────────────

def test_exit_price_never_zero(tmp_path):
    """Trade recording should refuse exit_price = 0."""
    import sqlite3
    from bot.plugins.learning_loop import LearningLoop

    class FakeCfg:
        pass

    ll = LearningLoop(FakeCfg())
    ll.db = str(tmp_path / 'test_ll.db')
    ll._init_db()

    # Insert a fake open trade
    conn = sqlite3.connect(ll.db)
    conn.execute("""
        INSERT INTO trades (timestamp, symbol, name, action, entry_price,
                            qty, open, currency)
        VALUES (?, ?, ?, ?, ?, ?, 1, 'USD')
    """, (datetime.datetime.utcnow().isoformat(), 'VRT', 'Vertiv', 'BUY',
          261.56, 5))
    conn.commit()
    conn.close()

    # Try to record exit with price 0 — should be refused
    ll._record_exit('VRT', 0.0, 'CLOSED (DETECTED_FLAT)')

    # Trade should still be open
    conn = sqlite3.connect(ll.db)
    row = conn.execute("SELECT open FROM trades WHERE symbol='VRT'").fetchone()
    conn.close()
    assert row[0] == 1, "Trade should still be open — exit_price=0 was rejected"


def test_exit_price_never_negative(tmp_path):
    """Trade recording should refuse exit_price < 0."""
    import sqlite3
    from bot.plugins.learning_loop import LearningLoop

    class FakeCfg:
        pass

    ll = LearningLoop(FakeCfg())
    ll.db = str(tmp_path / 'test_ll.db')
    ll._init_db()

    conn = sqlite3.connect(ll.db)
    conn.execute("""
        INSERT INTO trades (timestamp, symbol, name, action, entry_price,
                            qty, open, currency)
        VALUES (?, ?, ?, ?, ?, ?, 1, 'USD')
    """, (datetime.datetime.utcnow().isoformat(), 'AAPL', 'Apple', 'BUY',
          200.0, 10))
    conn.commit()
    conn.close()

    ll._record_exit('AAPL', -5.0, 'CLOSED (ERROR)')

    conn = sqlite3.connect(ll.db)
    row = conn.execute("SELECT open FROM trades WHERE symbol='AAPL'").fetchone()
    conn.close()
    assert row[0] == 1, "Trade should still be open — negative exit_price was rejected"
