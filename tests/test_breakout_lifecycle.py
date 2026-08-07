"""Daily event lifecycle (§2.5). Entry next session open; gap stop at open;
intra-session stop incl. positions entered today; trend-break exit at next open."""
from backtest.breakout_sim import run_breakout_portfolio
from tests.breakout_helpers import make_inst, bar


def test_entry_at_next_session_open():
    # entry_signal on bar0 -> fill at bar1 open. Initial stop = open - 2*signal_atr.
    inst = make_inst("X", 0, [
        bar("2025-01-02", 99, 100, 98, 99, atr14=5, entry_signal=True),
        bar("2025-01-03", 100, 105, 99, 104, atr14=5),
    ])
    res = run_breakout_portfolio([inst], initial_capital_usd=100_000.0)
    assert len(res.entries) == 1
    e = res.entries[0]
    assert e.date.startswith("2025-01-03")        # next session
    assert e.entry_fill == 100.0
    assert e.initial_stop == 100.0 - 2 * 5.0       # 90


def test_position_entered_today_can_be_stopped_today():
    # Entry bar's own low pierces the initial stop -> intra_stop same session.
    inst = make_inst("X", 0, [
        bar("2025-01-02", 99, 100, 98, 99, atr14=5, entry_signal=True),
        bar("2025-01-03", 100, 101, 85, 95, atr14=5),     # low 85 < stop 90
    ])
    res = run_breakout_portfolio([inst], liquidate_at_end=False)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.exit_reason == "intra_stop"
    assert t.exit_fill == 90.0                      # filled at the stop level
    assert t.holding_bars == 0                      # same session as entry


def test_gap_through_fills_at_open_not_stop():
    inst = make_inst("X", 0, [
        bar("2025-01-02", 99, 100, 98, 99, atr14=5, entry_signal=True),
        bar("2025-01-03", 100, 101, 95, 100, atr14=5),    # survives; ratchet -> stop 90
        bar("2025-01-06", 88, 89, 80, 82, atr14=5),       # opens 88 < stop 90 -> gap
    ])
    res = run_breakout_portfolio([inst], liquidate_at_end=False)
    t = res.trades[0]
    assert t.exit_reason == "gap_stop"
    assert t.exit_fill == 88.0                      # filled at the OPEN, not the stop


def test_trend_break_exits_at_next_open():
    inst = make_inst("X", 0, [
        bar("2025-01-02", 99, 100, 98, 99, atr14=5, entry_signal=True),
        bar("2025-01-03", 100, 110, 99, 108, atr14=5, trend_break=True),  # schedule exit
        bar("2025-01-06", 106, 107, 104, 105, atr14=5),  # exit at this open
    ])
    res = run_breakout_portfolio([inst], liquidate_at_end=False)
    t = res.trades[0]
    assert t.exit_reason == "trend_break"
    assert t.exit_date.startswith("2025-01-06")
    assert t.exit_fill == 106.0                     # next session open


def test_open_position_ignores_new_entry_signals():
    # A second entry_signal while already open must NOT create a second position.
    inst = make_inst("X", 0, [
        bar("2025-01-02", 99, 100, 98, 99, atr14=5, entry_signal=True),
        bar("2025-01-03", 100, 110, 99, 108, atr14=5, entry_signal=True),  # held -> ignored
        bar("2025-01-06", 109, 112, 108, 111, atr14=5),
    ])
    res = run_breakout_portfolio([inst], liquidate_at_end=True)
    assert len(res.entries) == 1
