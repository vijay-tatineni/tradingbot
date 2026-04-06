"""
backtest/offline_signals.py — Run the bot's exact signal logic on historical DataFrames.

This is the most critical module in the backtest framework. It imports
and reuses the REAL indicator calculations from bot/indicators.py and
the REAL signal logic from bot/signals.py. ZERO reimplementation.

Approach:
    bot.indicators.Indicators.calculate(df) computes all indicators on the
    LAST bar of a DataFrame. To generate signals across a full history, we
    slide a growing window through the DataFrame: at each bar i, we pass
    df[:i+1] to calculate() and evaluate().

    This is intentionally slow (O(n^2) in the number of bars) but correct.
    It guarantees identical indicator values to the live bot, because it
    calls the exact same code path.

    The bot.config.Config class is needed by Indicators — we build a
    lightweight config object from the instrument's settings dict rather
    than loading instruments.json again.
"""

from dataclasses import dataclass

import pandas as pd

from bot.indicators import Indicators, IndicatorBundle
from bot.signals import SignalEngine, SignalResult


@dataclass
class Signal:
    """A confirmed entry signal at a specific bar."""
    datetime: str
    bar_index: int
    direction: str    # "BUY" or "SELL"
    price: float
    symbol: str
    indicators: dict  # Snapshot of indicator values at signal time


class OfflineConfig:
    """
    Lightweight config object that satisfies bot.indicators.Indicators(cfg).

    Mirrors the attributes that Indicators reads from bot.config.Config:
        - alligator_min_gap_pct
        - ma200_period
        - williams_r_period, williams_r_mid, williams_r_oversold, williams_r_overbought
        - rsi_period, rsi_oversold, rsi_overbought
        - adx_period, adx_threshold
    """

    def __init__(self, settings: dict):
        self.alligator_min_gap_pct = settings.get("alligator_min_gap_pct", 0.003)
        self.ma200_period          = settings.get("ma200_period", 200)
        self.williams_r_period     = settings.get("williams_r_period", 14)
        self.williams_r_mid        = settings.get("williams_r_mid", -50)
        self.williams_r_oversold   = settings.get("williams_r_oversold", -80)
        self.williams_r_overbought = settings.get("williams_r_overbought", -20)
        self.rsi_period            = settings.get("rsi_period", 14)
        self.rsi_oversold          = settings.get("rsi_oversold", 35)
        self.rsi_overbought        = settings.get("rsi_overbought", 70)
        self.adx_period            = settings.get("adx_period", 14)
        self.adx_threshold         = settings.get("adx_threshold", 20)


def _bundle_to_dict(bundle: IndicatorBundle) -> dict:
    """Snapshot indicator values for logging/debugging."""
    return {
        "price": bundle.price,
        "alligator_state": bundle.alligator.state,
        "alligator_dir": bundle.alligator.direction,
        "alligator_jaw": bundle.alligator.jaw,
        "alligator_teeth": bundle.alligator.teeth,
        "alligator_lips": bundle.alligator.lips,
        "ma200_value": bundle.ma200.value,
        "ma200_trend": bundle.ma200.trend,
        "wr_value": bundle.wr.value,
        "wr_signal": bundle.wr.signal,
        "rsi": bundle.rsi,
        "adx_value": bundle.adx.value,
        "adx_strength": bundle.adx.trend_strength,
    }


def generate_signals(
    df: pd.DataFrame,
    indicator_settings: dict,
    symbol: str = "",
    start_from: int = 0,
) -> list[Signal]:
    """
    Run the bot's exact indicator + signal logic on every bar in df.

    For each bar i (starting from ma200_period to ensure enough history),
    we pass df[:i+1] to Indicators.calculate() and SignalEngine.evaluate().
    When the signal engine returns BUY (1) or SELL (-1), we record it.

    Args:
        df: DataFrame with columns: datetime, open, high, low, close, volume.
            The DataFrame should include warmup bars BEFORE the target window
            so that indicators (especially MA200) have enough history.
        indicator_settings: Dict with indicator params (from instruments.json settings).
        symbol: Instrument symbol for labelling.
        start_from: Only record signals from this index onwards. Bars before
            this index are used for indicator warmup only. This allows the
            walk-forward to pass full history but only collect signals within
            a specific date window.

    Returns:
        List of Signal objects in chronological order.
    """
    cfg = OfflineConfig(indicator_settings)
    indicators = Indicators(cfg)
    engine = SignalEngine()

    signals = []

    # Need at least ma200_period bars before we can get a valid bundle.
    min_bars = max(cfg.ma200_period, 30)
    actual_start = max(min_bars, start_from)

    for i in range(actual_start, len(df)):
        # Pass the DataFrame up to and including bar i
        window = df.iloc[:i + 1]

        bundle = indicators.calculate(window)
        if bundle is None:
            continue

        result = engine.evaluate(bundle)

        if result.signal == 1:
            signals.append(Signal(
                datetime=str(df.iloc[i]["datetime"]),
                bar_index=i,
                direction="BUY",
                price=bundle.price,
                symbol=symbol,
                indicators=_bundle_to_dict(bundle),
            ))
        elif result.signal == -1:
            signals.append(Signal(
                datetime=str(df.iloc[i]["datetime"]),
                bar_index=i,
                direction="SELL",
                price=bundle.price,
                symbol=symbol,
                indicators=_bundle_to_dict(bundle),
            ))

    return signals
