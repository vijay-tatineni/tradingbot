"""
tests/test_signals.py
Unit tests for bot/signals.py — pure signal logic, no IBKR needed.
"""

import pytest

from bot.indicators import (
    AlligatorResult, MA200Result, WilliamsRResult, ADXResult, IndicatorBundle,
)
from bot.signals import SignalEngine, SignalResult


def make_bundle(
    al_state='EATING', al_dir='BULL',
    ma_trend='BULL', wr_signal='ABOVE',
    adx_strength='STRONG', adx_value=30.0,
    price=100.0,
):
    """Build an IndicatorBundle with controllable fields."""
    return IndicatorBundle(
        price=price,
        alligator=AlligatorResult(
            jaw=98.0, teeth=99.0, lips=100.0,
            state=al_state, direction=al_dir,
        ),
        ma200=MA200Result(value=95.0, trend=ma_trend),
        wr=WilliamsRResult(value=-30.0, signal=wr_signal),
        rsi=55.0,
        adx=ADXResult(value=adx_value, trend_strength=adx_strength),
    )


@pytest.fixture
def engine():
    return SignalEngine()


# ── Signal direction tests ───────────────────────────────────

def test_buy_signal_all_bull(engine):
    """All 3 indicators bull + EATING + STRONG ADX → signal=1."""
    bundle = make_bundle(
        al_state='EATING', al_dir='BULL',
        ma_trend='BULL', wr_signal='ABOVE',
        adx_strength='STRONG',
    )
    result = engine.evaluate(bundle)
    assert result.signal == 1, f"Expected BUY (1), got {result.signal}: {result.reason}"


def test_sell_signal_all_bear(engine):
    """All 3 indicators bear + EATING + STRONG ADX → signal=-1."""
    bundle = make_bundle(
        al_state='EATING', al_dir='BEAR',
        ma_trend='BEAR', wr_signal='BELOW',
        adx_strength='STRONG',
    )
    result = engine.evaluate(bundle)
    assert result.signal == -1, f"Expected SELL (-1), got {result.signal}: {result.reason}"


def test_hold_sleeping_alligator(engine):
    """Alligator SLEEPING → signal=0 regardless of other indicators."""
    bundle = make_bundle(
        al_state='SLEEPING', al_dir='BULL',
        ma_trend='BULL', wr_signal='ABOVE',
        adx_strength='STRONG',
    )
    result = engine.evaluate(bundle)
    assert result.signal == 0, f"Expected HOLD (0), got {result.signal}: {result.reason}"
    assert 'SLEEPING' in result.reason


def test_hold_partial_bull(engine):
    """Only 2/3 bull indicators → signal=0."""
    bundle = make_bundle(
        al_state='EATING', al_dir='BULL',
        ma_trend='BULL', wr_signal='BELOW',  # WR disagrees
        adx_strength='STRONG',
    )
    result = engine.evaluate(bundle)
    assert result.signal == 0, f"Expected HOLD (0), got {result.signal}: {result.reason}"


def test_hold_weak_adx(engine):
    """All 3 bull but ADX WEAK → signal=0 (filtered out)."""
    bundle = make_bundle(
        al_state='EATING', al_dir='BULL',
        ma_trend='BULL', wr_signal='ABOVE',
        adx_strength='WEAK', adx_value=12.0,
    )
    result = engine.evaluate(bundle)
    assert result.signal == 0, f"Expected HOLD (0) due to weak ADX, got {result.signal}: {result.reason}"
    assert 'ADX weak' in result.reason


# ── Confidence tests ─────────────────────────────────────────

def test_confidence_high_when_eating(engine):
    """All bull + EATING → confidence='HIGH'."""
    bundle = make_bundle(
        al_state='EATING', al_dir='BULL',
        ma_trend='BULL', wr_signal='ABOVE',
        adx_strength='STRONG',
    )
    result = engine.evaluate(bundle)
    assert result.confidence == 'HIGH', f"Expected HIGH, got {result.confidence}"


def test_confidence_medium_when_waking(engine):
    """All bull + WAKING → confidence='MEDIUM'."""
    bundle = make_bundle(
        al_state='WAKING', al_dir='BULL',
        ma_trend='BULL', wr_signal='ABOVE',
        adx_strength='STRONG',
    )
    result = engine.evaluate(bundle)
    assert result.confidence == 'MEDIUM', f"Expected MEDIUM, got {result.confidence}"
