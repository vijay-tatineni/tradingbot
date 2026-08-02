"""
backtest/breakout_strategy.py — Frozen breakout signal + indicator engine.

Phase 3 breakout strategy (pre-registration v3.2, experiments/breakout_strategy.md).
This module computes the FROZEN indicators and the FROZEN 4-condition entry +
SMA50 trend-break exit. It deliberately does NOT reuse bot/signals.py or
bot/indicators.py — those implement the unrelated triple-confirmation strategy.
Nothing in bot/ or main.py is touched (§9).

Frozen indicators (§2):
  - SMA50, SMA200          simple moving averages (not EMA)
  - Wilder ATR(14)         explicit Wilder recurrence (library-version independent)
  - Wilder ADX(14)         explicit Wilder recurrence
  - 20-day intraday high   highest HIGH of the *preceding* 20 bars, EXCLUDING the
                           current bar  ->  high.shift(1).rolling(20).max()

Frozen entry (all true, on a completed bar i):
  close[i] > high20_excl[i]   AND   close[i] > sma200[i]
  AND  sma50[i] > sma200[i]   AND   adx14[i] > 25

Frozen trend-break (completed bar i):  close[i] < sma50[i]

The exact formulas here are frozen by code hash at Commit B.
"""

import numpy as np
import pandas as pd

SMA_FAST = 50
SMA_SLOW = 200
ATR_PERIOD = 14
ADX_PERIOD = 14
BREAKOUT_LOOKBACK = 20
ADX_THRESHOLD = 25.0


def wilder_atr(high, low, close, n=ATR_PERIOD):
    """Wilder's ATR(n), explicit recurrence (frozen).

    TR[i]   = max(H-L, |H-prevC|, |L-prevC|), defined for i >= 1.
    ATR[n]  = mean(TR[1..n])                         (Wilder seed)
    ATR[i]  = (ATR[i-1]*(n-1) + TR[i]) / n           for i > n
    Returns float array aligned to the input; NaN before index n.
    """
    high = np.asarray(high, float)
    low = np.asarray(low, float)
    close = np.asarray(close, float)
    m = len(close)
    tr = np.full(m, np.nan)
    for i in range(1, m):
        pc = close[i - 1]
        tr[i] = max(high[i] - low[i], abs(high[i] - pc), abs(low[i] - pc))
    atr = np.full(m, np.nan)
    if m > n:
        atr[n] = np.mean(tr[1:n + 1])           # seed = SMA of first n TRs
        for i in range(n + 1, m):
            atr[i] = (atr[i - 1] * (n - 1) + tr[i]) / n
    return atr


def wilder_adx(high, low, close, n=ADX_PERIOD):
    """Wilder's ADX(n), explicit recurrence (frozen).

    Directional movement, Wilder-smoothed TR/+DM/-DM (sum-then-decay), +DI/-DI,
    DX, then a final Wilder average of DX. First DX at index n; first ADX at
    index 2n-1. Returns float array aligned to input; NaN before that.
    """
    high = np.asarray(high, float)
    low = np.asarray(low, float)
    close = np.asarray(close, float)
    m = len(close)
    tr = np.full(m, np.nan)
    plus_dm = np.full(m, np.nan)
    minus_dm = np.full(m, np.nan)
    for i in range(1, m):
        pc = close[i - 1]
        tr[i] = max(high[i] - low[i], abs(high[i] - pc), abs(low[i] - pc))
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0

    # Wilder smoothing (sum form): S[n] = sum(x[1..n]); S[i] = S[i-1] - S[i-1]/n + x[i]
    def _smooth(x):
        s = np.full(m, np.nan)
        if m > n:
            s[n] = np.nansum(x[1:n + 1])
            for i in range(n + 1, m):
                s[i] = s[i - 1] - s[i - 1] / n + x[i]
        return s

    str_ = _smooth(tr)
    splus = _smooth(plus_dm)
    sminus = _smooth(minus_dm)

    adx = np.full(m, np.nan)
    if m > n:
        dx = np.full(m, np.nan)
        for i in range(n, m):
            denom = splus[i] + sminus[i]
            if denom > 0 and str_[i] > 0:
                plus_di = 100.0 * splus[i] / str_[i]
                minus_di = 100.0 * sminus[i] / str_[i]
                ddenom = plus_di + minus_di
                dx[i] = 100.0 * abs(plus_di - minus_di) / ddenom if ddenom > 0 else 0.0
            else:
                dx[i] = 0.0
        first_adx = 2 * n - 1
        if m > first_adx:
            adx[first_adx] = np.mean(dx[n:first_adx + 1])
            for i in range(first_adx + 1, m):
                adx[i] = (adx[i - 1] * (n - 1) + dx[i]) / n
    return adx


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of df with frozen indicator + signal columns added.

    Columns added: sma50, sma200, atr14, adx14, high20_excl, entry_signal,
    trend_break. entry_signal/trend_break are False wherever any required
    indicator is undefined.
    """
    out = df.copy().reset_index(drop=True)
    close = out["close"]
    out["sma50"] = close.rolling(SMA_FAST).mean()
    out["sma200"] = close.rolling(SMA_SLOW).mean()
    out["atr14"] = wilder_atr(out["high"], out["low"], out["close"])
    out["adx14"] = wilder_adx(out["high"], out["low"], out["close"])
    # Preceding 20-day intraday high, EXCLUDING the current bar (§2).
    out["high20_excl"] = out["high"].shift(1).rolling(BREAKOUT_LOOKBACK).max()

    defined = (out["sma50"].notna() & out["sma200"].notna()
               & out["atr14"].notna() & out["adx14"].notna()
               & out["high20_excl"].notna())
    out["entry_signal"] = (
        defined
        & (out["close"] > out["high20_excl"])
        & (out["close"] > out["sma200"])
        & (out["sma50"] > out["sma200"])
        & (out["adx14"] > ADX_THRESHOLD)
    )
    # Trend-break needs SMA50 defined (it is whenever the full set is, but guard
    # independently so it can fire even outside the entry-defined region).
    out["trend_break"] = out["sma50"].notna() & (out["close"] < out["sma50"])
    return out


def first_valid_signal_index(ind_df: pd.DataFrame) -> int | None:
    """First bar index at which ALL required indicators are defined (warm-up
    boundary). None if never. SMA200 is the binding constraint."""
    defined = (ind_df["sma50"].notna() & ind_df["sma200"].notna()
               & ind_df["atr14"].notna() & ind_df["adx14"].notna()
               & ind_df["high20_excl"].notna())
    if not defined.any():
        return None
    return int(defined.idxmax())
