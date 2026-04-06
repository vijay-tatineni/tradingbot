"""
tests/test_simulator_gbp.py
Test the backtest simulator handles GBP correctly.
"""

import pytest
import pandas as pd

from backtest.simulator import simulate_trades, TradeResult
from backtest.offline_signals import Signal


def make_df(prices):
    """Build a minimal OHLCV DataFrame from a list of (open, high, low, close)."""
    rows = []
    for i, (o, h, l, c) in enumerate(prices):
        rows.append({
            'datetime': f'2024-01-{i+1:02d}',
            'open': o, 'high': h, 'low': l, 'close': c, 'volume': 1000,
        })
    return pd.DataFrame(rows)


def make_signal(symbol, direction, price, bar_index):
    return Signal(
        datetime=f'2024-01-{bar_index+1:02d}',
        bar_index=bar_index,
        direction=direction,
        price=price,
        symbol=symbol,
        indicators={},
    )


# ── GBP P&L tests ─────────────────────────────────────────────

def test_simulator_gbp_pnl_in_pounds():
    """Simulator P&L for GBP instruments should be in pounds.
    Entry 250p, exit 260p, qty 400 -> raw 4000p -> 40 pounds, not 4000."""
    # Signal at bar 0, TP hit at bar 1 (high >= tp_price)
    # entry=250, tp_pct=4 -> tp_price = 250 * 1.04 = 260
    df = make_df([
        (250, 255, 245, 250),   # bar 0: signal bar
        (252, 265, 248, 260),   # bar 1: high=265 >= 260 -> TP hit
    ])
    signals = [make_signal('SGLN', 'BUY', 250.0, 0)]
    trades = simulate_trades(signals, df, stop_pct=4.0, tp_pct=4.0,
                             qty=400, currency='GBP')
    assert len(trades) == 1
    t = trades[0]
    assert t.outcome == 'win'
    # (260 - 250) * 400 / 100 = 40.0 pounds
    assert t.pnl == 40.0, f"Expected 40.0 pounds, got {t.pnl}"


def test_simulator_usd_pnl_unchanged():
    """Simulator P&L for USD instruments should be raw dollars.
    Entry $100, exit $105, qty 10 -> $50."""
    # entry=100, tp_pct=5 -> tp_price = 105
    df = make_df([
        (100, 102, 98, 100),
        (101, 106, 99, 105),  # high=106 >= 105 -> TP
    ])
    signals = [make_signal('MSFT', 'BUY', 100.0, 0)]
    trades = simulate_trades(signals, df, stop_pct=4.0, tp_pct=5.0,
                             qty=10, currency='USD')
    assert len(trades) == 1
    assert trades[0].pnl == 50.0, f"Expected 50.0, got {trades[0].pnl}"


def test_simulator_gbp_stop_loss():
    """GBP stop loss: entry 3450p, stop 4% -> triggers at 3312p.
    P&L = (3312 - 3450) * 40 / 100 = -55.20 pounds"""
    # stop_price = 3450 * (1 - 0.04) = 3312
    df = make_df([
        (3450, 3460, 3440, 3450),   # bar 0: signal
        (3440, 3445, 3300, 3310),    # bar 1: low=3300 <= 3312 -> SL
    ])
    signals = [make_signal('SHEL', 'BUY', 3450.0, 0)]
    trades = simulate_trades(signals, df, stop_pct=4.0, tp_pct=12.0,
                             qty=40, currency='GBP')
    assert len(trades) == 1
    t = trades[0]
    assert t.outcome == 'loss'
    # (3312 - 3450) * 40 / 100 = -138 * 40 / 100 = -55.20
    assert t.pnl == -55.20, f"Expected -55.20, got {t.pnl}"


def test_simulator_gbp_take_profit():
    """GBP take profit: entry 3450p, TP 12% -> triggers at 3864p.
    P&L = (3864 - 3450) * 40 / 100 = 165.60 pounds"""
    # tp_price = 3450 * (1 + 0.12) = 3864
    df = make_df([
        (3450, 3460, 3440, 3450),   # bar 0: signal
        (3460, 3470, 3440, 3460),   # bar 1: no trigger
        (3500, 3870, 3490, 3860),   # bar 2: high=3870 >= 3864 -> TP
    ])
    signals = [make_signal('SHEL', 'BUY', 3450.0, 0)]
    trades = simulate_trades(signals, df, stop_pct=4.0, tp_pct=12.0,
                             qty=40, currency='GBP')
    assert len(trades) == 1
    t = trades[0]
    assert t.outcome == 'win'
    # (3864 - 3450) * 40 / 100 = 414 * 40 / 100 = 165.60
    assert t.pnl == 165.60, f"Expected 165.60, got {t.pnl}"
