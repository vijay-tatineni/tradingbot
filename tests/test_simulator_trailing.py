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
    """If price rises to 110 then falls back, the stop stays at the high-water
    110 × (1 - 5%) = 104.5, not 105 × (1 - 5%) = 99.75.

    Fill is bar 1's open. The entry bar is flat (close == open) so it does not
    ratchet — keeping the triggering stop level set by a prior bar's close, so
    the hand-computed exit (104.5) carries no intra-bar lookahead.
      bar 1 (entry): open 100, close 100 -> peak 100, stop 95
      bar 2: close 110 -> peak 110, stop 104.5; low 105 > 104.5, no trigger
      bar 3: close 105 (< peak) no ratchet; low 104 <= 104.5 -> stop @ 104.5
    """
    df = _make_df([
        ("2024-01-01", 100, 101, 99, 100),    # bar 0: signal
        ("2024-01-02", 100, 101, 99, 100),    # bar 1: entry @ open 100, flat
        ("2024-01-03", 105, 111, 105, 110),   # bar 2: peak 110, stop 104.5
        ("2024-01-04", 110, 110, 104, 105),   # bar 3: low 104 <= 104.5 -> stop
    ])
    sigs = [_signal(price=100.0, bar_index=0)]
    trades = simulate_trades(sigs, df, stop_pct=5.0, tp_pct=30.0,
                             trailing_mode=True)

    assert len(trades) == 1
    t = trades[0]
    assert t.entry_price == 100.0
    assert t.outcome == "loss"
    assert abs(t.exit_price - 104.5) < 0.01, "stop held at 104.5, did not ratchet down"
    assert t.holding_bars == 2, "filled bar 1, exited bar 3 -> 2 bars"


def test_trailing_stop_triggers_on_bar_low():
    """Stop triggers when the bar's low pierces the stop level, even though
    the bar closes back ABOVE the stop.

    Fill is bar 1's open (100). The entry bar closes at 98 (<= open) so it
    does not ratchet; the stop stays at the clean 100 * 0.95 = 95. The bar's
    low 94 pierces it while its close 98 is still above 95.
    """
    df = _make_df([
        ("2024-01-01", 100, 101, 99, 100),    # bar 0: signal
        ("2024-01-02", 100, 102, 94, 98),     # bar 1: entry @ 100; low 94 <= stop 95
    ])
    sigs = [_signal(price=100.0, bar_index=0)]
    trades = simulate_trades(sigs, df, stop_pct=5.0, tp_pct=20.0,
                             trailing_mode=True)

    assert len(trades) == 1
    t = trades[0]
    assert t.entry_price == 100.0
    assert t.outcome == "loss"
    assert t.exit_price == 95.0, "exit at the un-ratcheted stop 95, not the bar low"
    assert t.holding_bars == 0, "stop hit on the entry bar itself"


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
    """TP triggers when the bar's high reaches tp_price, on the entry bar.

    Fill is bar 1's open (100); tp_pct=10 -> tp = 110. The entry bar closes
    flat at 100 (no ratchet, stop stays 95), so its low 100 cannot trip the
    stop and only the TP is in play.
    """
    df = _make_df([
        ("2024-01-01", 100, 101, 99, 100),    # bar 0: signal
        ("2024-01-02", 100, 111, 100, 100),   # bar 1: entry @ 100; high 111 >= tp 110
    ])
    sigs = [_signal(price=100.0, bar_index=0)]
    trades = simulate_trades(sigs, df, stop_pct=5.0, tp_pct=10.0,
                             trailing_mode=True)

    assert len(trades) == 1
    t = trades[0]
    assert t.entry_price == 100.0
    assert t.outcome == "win"
    assert t.exit_price == 110.0
    assert t.holding_bars == 0


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
    """Trailing-stop P&L for GBP instruments converts pence -> pounds.

    Fill is bar 1's open (250p). The entry bar closes at 248 (<= open) so it
    does not ratchet; stop stays at 250 * 0.95 = 237.5p. The bar low 236
    pierces it.
      P&L = (237.5 - 250) * 100 = -1250p = -£12.50
    """
    df = _make_df([
        ("2024-01-01", 250, 251, 249, 250),   # bar 0: signal
        ("2024-01-02", 250, 252, 236, 248),   # bar 1: entry @ 250; low 236 <= stop 237.5
    ])
    sigs = [_signal(price=250.0, bar_index=0)]
    trades = simulate_trades(sigs, df, stop_pct=5.0, tp_pct=20.0,
                             qty=100, currency="GBP", trailing_mode=True)

    assert len(trades) == 1
    t = trades[0]
    assert t.entry_price == 250.0
    assert t.outcome == "loss"
    assert abs(t.exit_price - 237.5) < 0.01
    assert t.holding_bars == 0
    assert abs(t.pnl - (-12.50)) < 0.01
