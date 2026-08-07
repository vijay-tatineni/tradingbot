"""
tests/test_simulator_sizing.py — Fix 3 (sizing): target_notional position sizing.

The simulator sizes each position as int(target_notional / fill_price) so every
trade carries roughly equal dollar exposure (matching bot.sizing.calculate_qty
and the live bot). target_notional=None falls back to the fixed qty argument.
"""

import pytest
import pandas as pd

from backtest.simulator import simulate_trades
from backtest.offline_signals import Signal


def _df(prices):
    rows = []
    for i, (o, h, l, c) in enumerate(prices):
        rows.append({'datetime': f'2024-01-{i+1:02d}', 'open': o, 'high': h,
                     'low': l, 'close': c, 'volume': 1000})
    return pd.DataFrame(rows)


def _sig(price=100.0, bar_index=0):
    return Signal(datetime='2024-01-01', bar_index=bar_index, direction='BUY',
                  price=price, symbol='TEST', indicators={})


def test_target_notional_sizes_qty_by_price():
    """qty = int(target_notional / entry fill). At $1000 target and a $100
    entry fill -> 10 shares; a clean +$10/share TP move -> +$100 P&L.
    Frictionless (cost_config=None) so the arithmetic is exact."""
    df = _df([
        (98, 99, 97, 98),        # bar 0: signal
        (100, 101, 99, 100),     # bar 1: entry @ open 100 -> qty 10
        (105, 111, 104, 110),    # bar 2: high 111 >= tp 110 -> win
    ])
    trades = simulate_trades([_sig(98.0, 0)], df, stop_pct=20.0, tp_pct=10.0,
                             qty=1, trailing_mode=False, target_notional=1000.0)
    t = trades[0]
    assert t.entry_price == 100.0
    assert t.outcome == 'win' and t.exit_price == 110.0
    assert t.pnl == 100.0, "int(1000/100)=10 shares * $10 move"


def test_target_notional_scales_pnl_linearly():
    """Doubling the target notional doubles the share count and the P&L."""
    df = _df([
        (98, 99, 97, 98),
        (100, 101, 99, 100),
        (105, 111, 104, 110),
    ])
    kw = dict(stop_pct=20.0, tp_pct=10.0, qty=1, trailing_mode=False)
    p1 = simulate_trades([_sig(98.0, 0)], df, target_notional=1000.0, **kw)[0].pnl
    p2 = simulate_trades([_sig(98.0, 0)], df, target_notional=2000.0, **kw)[0].pnl
    assert p1 == 100.0 and p2 == 200.0


def test_no_target_notional_uses_fixed_qty():
    """target_notional=None (default) ignores notional sizing and uses the
    fixed qty argument unchanged."""
    df = _df([
        (98, 99, 97, 98),
        (100, 101, 99, 100),
        (105, 111, 104, 110),
    ])
    t = simulate_trades([_sig(98.0, 0)], df, stop_pct=20.0, tp_pct=10.0,
                        qty=7, trailing_mode=False, target_notional=None)[0]
    assert t.pnl == 70.0, "fixed qty 7 * $10 move, no notional sizing"


def test_target_notional_at_least_one_share():
    """When target_notional is smaller than the price, sizing floors to 1
    share (calculate_qty guarantees >= 1)."""
    df = _df([
        (98, 99, 97, 98),
        (100, 101, 99, 100),     # entry @ 100, target $50 -> int(0.5)=0 -> floored to 1
        (105, 111, 104, 110),
    ])
    t = simulate_trades([_sig(98.0, 0)], df, stop_pct=20.0, tp_pct=10.0,
                        qty=1, trailing_mode=False, target_notional=50.0)[0]
    assert t.pnl == 10.0, "min 1 share * $10 move"
