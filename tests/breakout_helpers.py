"""Shared builders for breakout portfolio tests (not a test module itself).

The portfolio loop reads only: datetime, open, high, low, close, atr14,
entry_signal, trend_break — so tests hand-craft minimal frames and skip the
200-bar indicator warm-up.
"""
import pandas as pd

from backtest.breakout_sim import BreakoutInstrument
from backtest.simulator import CostConfig


def bar(date, open, high, low, close, atr14=1.0, entry_signal=False,
        trend_break=False):
    return {"datetime": pd.Timestamp(date, tz="UTC"), "open": open, "high": high,
            "low": low, "close": close, "atr14": atr14,
            "entry_signal": entry_signal, "trend_break": trend_break}


def make_inst(symbol, order_idx, bars, currency="USD", sec_type="STK",
              cost=None):
    df = pd.DataFrame(bars)
    return BreakoutInstrument(
        symbol=symbol, order_idx=order_idx, df=df, currency=currency,
        sec_type=sec_type, cost_config=cost if cost is not None else CostConfig.zero(),
    )
