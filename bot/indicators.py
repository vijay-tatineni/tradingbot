"""
bot/indicators.py
All technical indicators used by the triple confirmation strategy.

  - Williams Alligator  (SMMA 13/8/5 with shifts)
  - 200-period MA filter
  - Williams %R
  - RSI

Each function is pure — takes a DataFrame, returns values.
No side effects, no IB calls. Easy to unit test.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional
from bot.config import Config


# ── Result dataclasses ────────────────────────────────────────

@dataclass
class AlligatorResult:
    jaw:       Optional[float]
    teeth:     Optional[float]
    lips:      Optional[float]
    state:     str    # SLEEPING | WAKING | EATING
    direction: str    # BULL | BEAR | NONE

@dataclass
class MA200Result:
    value: Optional[float]
    trend: str    # BULL | BEAR | UNKNOWN

@dataclass
class WilliamsRResult:
    value:  float
    signal: str   # CROSS_UP | CROSS_DOWN | ABOVE | BELOW | NEUTRAL

@dataclass
class ADXResult:
    value: float
    trend_strength: str  # STRONG | WEAK | NONE

@dataclass
class IndicatorBundle:
    """All indicators for one instrument at one point in time."""
    price:    float
    alligator: AlligatorResult
    ma200:    MA200Result
    wr:       WilliamsRResult
    rsi:      float
    adx:      ADXResult


# ── Indicators class ──────────────────────────────────────────

class Indicators:
    """
    Calculates all technical indicators from OHLCV DataFrames.
    Stateless — create once and call calculate() per instrument per cycle.
    """

    # Alligator periods (Bill Williams original settings — Fibonacci numbers)
    JAW_PERIOD   = 13;  JAW_SHIFT   = 8
    TEETH_PERIOD = 8;   TEETH_SHIFT = 5
    LIPS_PERIOD  = 5;   LIPS_SHIFT  = 3

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def calculate(self, df: pd.DataFrame) -> Optional[IndicatorBundle]:
        """
        Run all indicators on a price DataFrame.
        Returns IndicatorBundle or None if data is insufficient.
        """
        if df is None or len(df) < self.cfg.ma200_period:
            return None

        price     = float(df['close'].iloc[-1])
        alligator = self._alligator(df)
        ma200     = self._ma200(df)
        wr        = self._williams_r(df)
        rsi       = self._rsi(df)
        adx       = self._adx(df)

        return IndicatorBundle(
            price=round(price, 4),
            alligator=alligator,
            ma200=ma200,
            wr=wr,
            rsi=rsi,
            adx=adx,
        )

    # ── Private: Alligator ────────────────────────────────────

    def _smma(self, series: pd.Series, period: int) -> list:
        """Smoothed Moving Average — different from EMA/SMA."""
        result = [None] * len(series)
        if len(series) < period:
            return result
        result[period - 1] = float(series[:period].mean())
        for i in range(period, len(series)):
            if result[i-1] is not None:
                result[i] = (result[i-1] * (period - 1) + float(series.iloc[i])) / period
        return result

    def _shifted_value(self, vals: list, shift: int) -> Optional[float]:
        valid = [v for v in vals if v is not None]
        return valid[-(shift + 1)] if len(valid) > shift else None

    def _alligator(self, df: pd.DataFrame) -> AlligatorResult:
        if len(df) < self.JAW_PERIOD + self.JAW_SHIFT + 5:
            return AlligatorResult(None, None, None, 'SLEEPING', 'NONE')

        median = (df['high'] + df['low']) / 2
        jaw    = self._shifted_value(self._smma(median, self.JAW_PERIOD),   self.JAW_SHIFT)
        teeth  = self._shifted_value(self._smma(median, self.TEETH_PERIOD), self.TEETH_SHIFT)
        lips   = self._shifted_value(self._smma(median, self.LIPS_PERIOD),  self.LIPS_SHIFT)

        if None in (jaw, teeth, lips):
            return AlligatorResult(None, None, None, 'SLEEPING', 'NONE')

        price  = float(df['close'].iloc[-1])
        gap_jt = abs(jaw - teeth) / price
        gap_tl = abs(teeth - lips) / price
        mg     = self.cfg.alligator_min_gap_pct

        state = (
            'SLEEPING' if gap_jt < mg and gap_tl < mg else
            'EATING'   if gap_jt > mg*3 and gap_tl > mg*3 else
            'WAKING'
        )
        direction = (
            'BULL' if lips > teeth > jaw else
            'BEAR' if lips < teeth < jaw else
            'NONE'
        )

        return AlligatorResult(
            jaw=round(jaw, 4), teeth=round(teeth, 4), lips=round(lips, 4),
            state=state, direction=direction
        )

    # ── Private: MA200 ────────────────────────────────────────

    def _ma200(self, df: pd.DataFrame) -> MA200Result:
        if len(df) < self.cfg.ma200_period:
            return MA200Result(None, 'UNKNOWN')
        ma200 = float(df['close'].rolling(self.cfg.ma200_period).mean().iloc[-1])
        price = float(df['close'].iloc[-1])
        return MA200Result(
            value=round(ma200, 4),
            trend='BULL' if price > ma200 else 'BEAR'
        )

    # ── Private: Williams %R ──────────────────────────────────

    def _williams_r(self, df: pd.DataFrame) -> WilliamsRResult:
        period = self.cfg.williams_r_period
        mid    = self.cfg.williams_r_mid

        if len(df) < period + 1:
            return WilliamsRResult(-50.0, 'NEUTRAL')

        high_max = df['high'].rolling(period).max()
        low_min  = df['low'].rolling(period).min()
        wr       = -100 * (high_max - df['close']) / (high_max - low_min)

        # Handle zero denominator (high_max == low_min → flat price)
        wr = wr.replace([np.inf, -np.inf], -50.0)
        wr = wr.fillna(-50.0)

        curr = round(float(wr.iloc[-1]), 2)
        prev = round(float(wr.iloc[-2]), 2)

        signal = (
            'CROSS_UP'   if prev < mid <= curr else
            'CROSS_DOWN' if prev > mid >= curr else
            'ABOVE'      if curr >= mid else
            'BELOW'
        )
        return WilliamsRResult(value=curr, signal=signal)

    # ── Private: ADX ──────────────────────────────────────────

    def _adx(self, df: pd.DataFrame) -> ADXResult:
        period = self.cfg.adx_period if hasattr(self.cfg, 'adx_period') else 14
        threshold = self.cfg.adx_threshold if hasattr(self.cfg, 'adx_threshold') else 20

        if len(df) < period * 2:
            return ADXResult(0.0, 'NONE')

        high  = df['high']
        low   = df['low']
        close = df['close']

        plus_dm  = high.diff()
        minus_dm = -low.diff()
        plus_dm  = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr      = tr.rolling(period).mean()

        # Handle zero ATR (no price movement)
        if atr.iloc[-1] == 0 or pd.isna(atr.iloc[-1]):
            return ADXResult(0.0, 'NONE')

        plus_di  = 100 * (plus_dm.rolling(period).mean() / atr).fillna(0)
        minus_di = 100 * (minus_dm.rolling(period).mean() / atr).fillna(0)

        di_sum = plus_di + minus_di
        dx = (100 * (plus_di - minus_di).abs() / di_sum).replace([np.inf, -np.inf], 0).fillna(0)
        adx = dx.rolling(period).mean()

        val = round(float(adx.iloc[-1]), 2) if not pd.isna(adx.iloc[-1]) else 0.0
        strength = 'STRONG' if val >= threshold else 'WEAK'

        return ADXResult(value=val, trend_strength=strength)

    # ── Private: RSI ──────────────────────────────────────────

    def _rsi(self, df: pd.DataFrame) -> float:
        period = self.cfg.rsi_period
        if len(df) < period + 1:
            return 50.0
        delta = df['close'].diff()
        gain  = delta.where(delta > 0, 0.0).rolling(period).mean()
        loss  = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
        rs    = gain / loss

        # Handle all-same-price: gain=0, loss=0 → rs=NaN → RSI=50
        rs = rs.replace([np.inf, -np.inf], 0)
        rs = rs.fillna(0)

        rsi   = 100 - (100 / (1 + rs))

        val = float(rsi.iloc[-1])
        if pd.isna(val) or np.isinf(val):
            return 50.0
        return round(val, 2)
