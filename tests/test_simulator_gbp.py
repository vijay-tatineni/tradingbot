"""
tests/test_simulator_gbp.py
Test the backtest simulator handles GBP correctly under honest semantics:
  - entry fills at the NEXT bar's open (not the signal bar's close)
  - stops/TP are scanned from the entry bar INCLUSIVE
  - when both stop and TP touch in one bar, the stop wins (conservative)
  - a signal on the last bar is skipped (no next bar to fill on)

All fills below are hand-computed from the entry bar's OPEN. These use
trailing_mode=False so stop/TP are fixed and the arithmetic is unambiguous.
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

    Signal computed at bar 0's close (245). Fill is bar 1's OPEN = 250p
    (NOT the signal price). TP touches on that same entry bar.
      tp_price = 250 * 1.04 = 260; bar 1 high 265 >= 260 -> win
      stop_price = 250 * 0.96 = 240; bar 1 low 248 > 240 -> no stop
      P&L = (260 - 250) * 400 / 100 = 40.00 pounds (raw 4000p / 100)
    """
    df = make_df([
        (245, 248, 242, 246),   # bar 0: signal bar (close 246)
        (250, 265, 248, 260),   # bar 1: fill at open 250; high 265 >= tp 260
    ])
    # sig.price is the signal-bar close; it must NOT be used as the fill.
    signals = [make_signal('SGLN', 'BUY', 246.0, 0)]
    trades = simulate_trades(signals, df, stop_pct=4.0, tp_pct=4.0,
                             qty=400, currency='GBP', trailing_mode=False)
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_price == 250.0, "fill must be bar 1's open, not the signal price"
    assert t.outcome == 'win'
    assert t.exit_price == 260.0
    assert t.holding_bars == 0, "entered and exited on the same bar -> 0 holding bars"
    assert t.pnl == 40.0, f"Expected 40.0 pounds, got {t.pnl}"


def test_simulator_usd_pnl_unchanged():
    """USD P&L should be raw dollars, fill at next bar's open.
      entry = bar 1 open = 100; tp_pct=5 -> tp = 105; stop = 96
      bar 1 high 106 >= 105 -> win; P&L = (105 - 100) * 10 = 50
    """
    df = make_df([
        (98, 99, 97, 98),       # bar 0: signal bar
        (100, 106, 98, 105),    # bar 1: fill at 100; high 106 >= tp 105
    ])
    signals = [make_signal('MSFT', 'BUY', 98.0, 0)]
    trades = simulate_trades(signals, df, stop_pct=4.0, tp_pct=5.0,
                             qty=10, currency='USD', trailing_mode=False)
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_price == 100.0
    assert t.outcome == 'win'
    assert t.exit_price == 105.0
    assert t.holding_bars == 0
    assert t.pnl == 50.0, f"Expected 50.0, got {t.pnl}"


def test_simulator_gbp_stop_loss():
    """GBP stop loss on the entry bar itself.
      entry = bar 1 open = 3450; stop_pct=4 -> stop = 3312
      bar 1 low 3300 <= 3312 -> loss; high 3460 < tp 3864 -> no TP
      P&L = (3312 - 3450) * 40 / 100 = -55.20 pounds
    """
    df = make_df([
        (3440, 3460, 3430, 3450),   # bar 0: signal
        (3450, 3460, 3300, 3310),   # bar 1: fill 3450; low 3300 <= stop 3312
    ])
    signals = [make_signal('SHEL', 'BUY', 3450.0, 0)]
    trades = simulate_trades(signals, df, stop_pct=4.0, tp_pct=12.0,
                             qty=40, currency='GBP', trailing_mode=False)
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_price == 3450.0
    assert t.outcome == 'loss'
    assert t.exit_price == 3312.0
    assert t.holding_bars == 0
    assert t.pnl == -55.20, f"Expected -55.20, got {t.pnl}"


def test_simulator_gbp_take_profit():
    """GBP take profit on a LATER bar (proves the scan continues past the
    entry bar and that holding_bars is measured from the fill bar).
      entry = bar 1 open = 3450; tp_pct=12 -> tp = 3864; stop = 3312
      bar 1: no trigger (high 3460 < 3864, low 3440 > 3312)
      bar 2: high 3870 >= 3864 -> win; holding = 2 - 1 = 1 bar
      P&L = (3864 - 3450) * 40 / 100 = 165.60 pounds
    """
    df = make_df([
        (3440, 3460, 3430, 3450),   # bar 0: signal
        (3450, 3460, 3440, 3455),   # bar 1: fill 3450; no trigger
        (3500, 3870, 3490, 3860),   # bar 2: high 3870 >= tp 3864
    ])
    signals = [make_signal('SHEL', 'BUY', 3450.0, 0)]
    trades = simulate_trades(signals, df, stop_pct=4.0, tp_pct=12.0,
                             qty=40, currency='GBP', trailing_mode=False)
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_price == 3450.0
    assert t.outcome == 'win'
    assert t.exit_price == 3864.0
    assert t.holding_bars == 1, "filled on bar 1, exited on bar 2 -> 1 bar held"
    assert t.pnl == 165.60, f"Expected 165.60, got {t.pnl}"


# ── Honest-semantics property tests ───────────────────────────

def test_stop_first_when_both_touch_in_one_bar():
    """When both the stop and the TP fall inside one bar's range, the
    conservative convention assumes the stop hit first (loss).
      entry = bar 1 open = 100; stop = 95, tp = 105
      bar 1 low 94 <= 95 AND high 106 >= 105 -> both touch -> LOSS at stop
    """
    df = make_df([
        (98, 99, 97, 98),       # bar 0: signal
        (100, 106, 94, 100),    # bar 1: fill 100; low 94 <= stop, high 106 >= tp
    ])
    signals = [make_signal('MSFT', 'BUY', 98.0, 0)]
    trades = simulate_trades(signals, df, stop_pct=5.0, tp_pct=5.0,
                             qty=10, currency='USD', trailing_mode=False)
    assert len(trades) == 1
    t = trades[0]
    assert t.outcome == 'loss', "stop-first convention: both touched -> loss"
    assert t.exit_price == 95.0, "exit at the stop level, not the TP"
    assert t.holding_bars == 0


def test_last_bar_signal_is_skipped():
    """A signal on the final bar has no next bar to fill on, so it is
    skipped entirely (no trade)."""
    df = make_df([
        (100, 102, 98, 101),    # bar 0
        (101, 103, 99, 102),    # bar 1 (last) -> signal here cannot fill
    ])
    signals = [make_signal('MSFT', 'BUY', 102.0, bar_index=1)]
    trades = simulate_trades(signals, df, stop_pct=5.0, tp_pct=5.0,
                             qty=10, currency='USD', trailing_mode=False)
    assert trades == [], "last-bar signal must be skipped (no next-open fill)"
