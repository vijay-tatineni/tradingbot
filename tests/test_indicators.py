"""
tests/test_indicators.py
Unit tests for bot/indicators.py — pure calculation logic, no IBKR needed.
"""

import pandas as pd
import numpy as np
import pytest

from bot.indicators import Indicators, IndicatorBundle


class MockConfig:
    alligator_min_gap_pct = 0.003
    ma200_period = 200
    williams_r_period = 14
    williams_r_mid = -50
    williams_r_oversold = -80
    williams_r_overbought = -20
    rsi_period = 14
    rsi_oversold = 35
    rsi_overbought = 70
    adx_period = 14
    adx_threshold = 20


def make_df(closes, n=250):
    """Create a DataFrame from close prices, generating reasonable OHLC."""
    if isinstance(closes, (int, float)):
        closes = [closes] * n
    data = []
    for c in closes:
        data.append({
            'date': '2024-01-01',
            'open': c * 0.999,
            'high': c * 1.001,
            'low': c * 0.998,
            'close': c,
            'volume': 1000,
        })
    return pd.DataFrame(data)


@pytest.fixture
def ind():
    return Indicators(MockConfig())


# ── RSI tests ────────────────────────────────────────────────

def test_rsi_uptrend(ind):
    """Rising prices with some noise should produce RSI > 50."""
    import random
    random.seed(42)
    closes = [100 + i * 0.3 + random.uniform(-0.5, 0.5) for i in range(250)]
    df = make_df(closes)
    rsi = ind._rsi(df)
    assert rsi > 50, f"RSI for uptrend should be > 50, got {rsi}"


def test_rsi_downtrend(ind):
    """Falling prices with some noise should produce RSI < 50."""
    import random
    random.seed(42)
    closes = [200 - i * 0.3 + random.uniform(-0.5, 0.5) for i in range(250)]
    df = make_df(closes)
    rsi = ind._rsi(df)
    assert rsi < 50, f"RSI for downtrend should be < 50, got {rsi}"


def test_rsi_flat_price(ind):
    """All same close price → gain=0, loss=0, rs=0 → RSI returns 0.0.
    This is a known edge case: with zero price movement, the rolling mean
    produces gain=0 and loss=0, rs becomes NaN→0, and RSI = 100-100/1 = 0."""
    df = make_df(100.0, n=250)
    rsi = ind._rsi(df)
    assert rsi == 0.0, f"RSI for flat price should be 0.0 (edge case), got {rsi}"


# ── Williams %R tests ────────────────────────────────────────

def test_williams_r_at_high(ind):
    """Close at the period high → WR near 0."""
    # Start with lower prices, end at the highest
    closes = [90.0] * 236 + [float(90 + i) for i in range(14)]
    df = make_df(closes)
    wr = ind._williams_r(df)
    assert wr.value > -10, f"WR at period high should be near 0, got {wr.value}"


def test_williams_r_at_low(ind):
    """Close at the period low → WR near -100."""
    # Start with higher prices, end at the lowest
    closes = [110.0] * 236 + [float(110 - i) for i in range(14)]
    df = make_df(closes)
    wr = ind._williams_r(df)
    assert wr.value < -90, f"WR at period low should be near -100, got {wr.value}"


def test_williams_r_flat_price(ind):
    """All identical OHLC → WR should return -50.0 (edge case, zero denominator)."""
    data = [{'date': '2024-01-01', 'open': 100, 'high': 100,
             'low': 100, 'close': 100, 'volume': 1000}] * 250
    df = pd.DataFrame(data)
    wr = ind._williams_r(df)
    assert wr.value == -50.0, f"WR for flat OHLC should be -50.0, got {wr.value}"


# ── MA200 tests ──────────────────────────────────────────────

def test_ma200_bull(ind):
    """Price above the 200 MA → trend='BULL'."""
    # Rising prices: current price well above the average
    closes = [50 + i * 0.5 for i in range(250)]
    df = make_df(closes)
    ma200 = ind._ma200(df)
    assert ma200.trend == 'BULL', f"Expected BULL, got {ma200.trend}"


def test_ma200_bear(ind):
    """Price below the 200 MA → trend='BEAR'."""
    # Falling prices: current price well below the average
    closes = [200 - i * 0.5 for i in range(250)]
    df = make_df(closes)
    ma200 = ind._ma200(df)
    assert ma200.trend == 'BEAR', f"Expected BEAR, got {ma200.trend}"


# ── ADX tests ────────────────────────────────────────────────

def test_adx_flat_market(ind):
    """Flat prices → ADX should be low or 0."""
    df = make_df(100.0, n=250)
    adx = ind._adx(df)
    assert adx.value == 0.0, f"ADX for flat market should be 0, got {adx.value}"


# ── Full calculate() test ────────────────────────────────────

def test_calculate_returns_bundle(ind):
    """Full calculate() call returns IndicatorBundle with all fields."""
    closes = [100 + i * 0.1 for i in range(250)]
    df = make_df(closes)
    bundle = ind.calculate(df)
    assert bundle is not None, "calculate() should return a bundle for 250 bars"
    assert isinstance(bundle, IndicatorBundle)
    assert bundle.price > 0
    assert bundle.alligator is not None
    assert bundle.ma200 is not None
    assert bundle.wr is not None
    assert isinstance(bundle.rsi, float)
    assert bundle.adx is not None
    assert bundle.alligator.state in ('SLEEPING', 'WAKING', 'EATING')
    assert bundle.ma200.trend in ('BULL', 'BEAR', 'UNKNOWN')
    assert bundle.wr.signal in ('CROSS_UP', 'CROSS_DOWN', 'ABOVE', 'BELOW', 'NEUTRAL')
    assert bundle.adx.trend_strength in ('STRONG', 'WEAK', 'NONE')
