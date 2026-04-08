"""
tests/test_simulator_trailing.py — Tests for simulator trailing stop logic.
"""

import pandas as pd
import pytest

from backtest.offline_signals import Signal
from backtest.simulator import simulate_trades


def _make_df(bars):
    """Build a DataFrame from a list of (datetime, open, high, low, close) tuples."""
    return pd.DataFrame(bars, columns=["datetime", "open", "high", "low", "close"])


def _signal(direction="BUY", price=100.0, bar_index=0, symbol="TEST"):
    return Signal(
        symbol=symbol, datetime="2024-01-01", direction=direction,
        price=price, bar_index=bar_index, indicators={},
    )


def test_trailing_stop_ratchets_up():
    """Peak price increases as close prices rise.
    Entry 100, close goes 102, 105, 103.
    Peak should be 105, stop should be 105 × (1 - 5/100) = 99.75.
    NOT 100 × (1 - 5/100) = 95."""
    df = _make_df([
        ("2024-01-01", 100, 101, 99, 100),   # entry bar
        ("2024-01-02", 101, 103, 100, 102),   # close 102, peak=102
        ("2024-01-03", 102, 106, 101, 105),   # close 105, peak=105
        ("2024-01-04", 105, 106, 99.7, 103),  # low 99.7 < 99.75 → stop hit
    ])
    sigs = [_signal(price=100.0, bar_index=0)]
    trades = simulate_trades(sigs, df, stop_pct=5.0, tp_pct=20.0,
                             trailing_mode=True)

    assert len(trades) == 1
    t = trades[0]
    assert t.outcome == "loss"
    # Stop should be 105 * 0.95 = 99.75, not 95.0
    assert abs(t.exit_price - 99.75) < 0.01


def test_trailing_stop_never_ratchets_down():
    """If price rises to 110 then falls to 105, stop stays at
    110 × (1 - 5%) = 104.5, not 105 × (1 - 5%) = 99.75."""
    df = _make_df([
        ("2024-01-01", 100, 101, 99, 100),   # entry
        ("2024-01-02", 100, 111, 100, 110),   # peak 110, stop=104.5
        ("2024-01-03", 110, 110, 104, 105),   # close drops, stop stays 104.5
        ("2024-01-04", 105, 106, 104.4, 105), # low 104.4 < 104.5 → stop
    ])
    sigs = [_signal(price=100.0, bar_index=0)]
    trades = simulate_trades(sigs, df, stop_pct=5.0, tp_pct=30.0,
                             trailing_mode=True)

    assert len(trades) == 1
    assert trades[0].outcome == "loss"
    assert abs(trades[0].exit_price - 104.5) < 0.01


def test_trailing_stop_triggers_on_bar_low():
    """Stop triggers when bar low goes below stop level,
    even if close is above stop."""
    df = _make_df([
        ("2024-01-01", 100, 101, 99, 100),   # entry
        ("2024-01-02", 100, 102, 94, 101),    # low 94 < stop 95 → triggered
    ])
    sigs = [_signal(price=100.0, bar_index=0)]
    trades = simulate_trades(sigs, df, stop_pct=5.0, tp_pct=20.0,
                             trailing_mode=True)

    assert len(trades) == 1
    assert trades[0].outcome == "loss"


def test_trailing_stop_exit_price_is_stop_level():
    """Exit price should be the stop level, not the bar low
    (simulating a stop order fill)."""
    df = _make_df([
        ("2024-01-01", 100, 101, 99, 100),
        ("2024-01-02", 100, 101, 90, 95),     # low 90 < stop 95
    ])
    sigs = [_signal(price=100.0, bar_index=0)]
    trades = simulate_trades(sigs, df, stop_pct=5.0, tp_pct=20.0,
                             trailing_mode=True)

    assert trades[0].exit_price == 95.0  # stop level, not 90 (bar low)


def test_fixed_stop_does_not_trail():
    """With trailing_mode=False, stop stays at entry × (1 - pct),
    even if price rises significantly."""
    df = _make_df([
        ("2024-01-01", 100, 101, 99, 100),   # entry at 100
        ("2024-01-02", 100, 120, 100, 120),   # close 120 → trailing would ratchet
        ("2024-01-03", 120, 121, 94, 95),     # low 94 < fixed stop 95 → loss
    ])
    sigs = [_signal(price=100.0, bar_index=0)]

    # With trailing: stop would be 120*0.95=114, no trigger at low 94 (actually triggers)
    # With fixed: stop stays at 95, low 94 triggers
    trades = simulate_trades(sigs, df, stop_pct=5.0, tp_pct=30.0,
                             trailing_mode=False)

    assert len(trades) == 1
    assert trades[0].outcome == "loss"
    assert trades[0].exit_price == 95.0  # fixed stop at entry * 0.95


def test_trailing_tp_uses_bar_high():
    """TP triggers when bar high reaches tp_price."""
    df = _make_df([
        ("2024-01-01", 100, 101, 99, 100),
        ("2024-01-02", 100, 111, 105, 108),   # high 111 >= tp 110, low above stop
    ])
    sigs = [_signal(price=100.0, bar_index=0)]
    trades = simulate_trades(sigs, df, stop_pct=5.0, tp_pct=10.0,
                             trailing_mode=True)

    assert len(trades) == 1
    assert trades[0].outcome == "win"
    assert trades[0].exit_price == 110.0


def test_trailing_mode_default_true():
    """simulate_trades() should default to trailing_mode=True."""
    import inspect
    sig = inspect.signature(simulate_trades)
    assert sig.parameters['trailing_mode'].default is True


def test_trailing_produces_different_results():
    """Same data with trailing_mode=True vs False should produce
    different trade results (trailing stop ratchets up)."""
    df = _make_df([
        ("2024-01-01", 100, 101, 99, 100),
        ("2024-01-02", 100, 111, 100, 110),   # close 110 → trailing ratchets
        ("2024-01-03", 110, 112, 104, 105),   # trailing stop: 110*0.95=104.5, low 104 < 104.5 → stop
                                                # fixed stop: 100*0.95=95, low 104 > 95 → no stop
        ("2024-01-04", 105, 106, 94, 95),     # fixed stop: low 94 < 95 → stop
    ])
    sigs = [_signal(price=100.0, bar_index=0)]

    trailing = simulate_trades(sigs, df, stop_pct=5.0, tp_pct=30.0,
                               trailing_mode=True)
    fixed = simulate_trades(sigs, df, stop_pct=5.0, tp_pct=30.0,
                            trailing_mode=False)

    # Trailing exits at bar 3 with stop at ~104.5
    # Fixed exits at bar 4 with stop at 95
    assert trailing[0].exit_price != fixed[0].exit_price


def test_trailing_stop_gbp_pnl():
    """Trailing stop P&L for GBP instruments uses pence_to_pounds."""
    df = _make_df([
        ("2024-01-01", 250, 251, 249, 250),   # entry at 250p
        ("2024-01-02", 250, 260, 236, 240),   # low 236 < stop 237.5 → stop
    ])
    sigs = [_signal(price=250.0, bar_index=0)]
    trades = simulate_trades(sigs, df, stop_pct=5.0, tp_pct=20.0,
                             qty=100, currency="GBP", trailing_mode=True)

    assert len(trades) == 1
    t = trades[0]
    assert t.outcome == "loss"
    # P&L: (237.5 - 250) * 100 = -1250p = -£12.50
    assert abs(t.pnl - (-12.50)) < 0.01
