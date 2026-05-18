"""Unit tests for TripleConfirmationEngine wrapper — §8.1."""
import pytest
from bot.strategies.triple_confirmation import TripleConfirmationEngine
from bot.strategies.base import MarketState, Signal, ExitDecision
from bot.indicators import (
    IndicatorBundle, AlligatorResult, MA200Result, WilliamsRResult, ADXResult,
)
from bot.regime.models import PositionMetadata
from datetime import datetime, timezone
import pandas as pd


def _make_state(bundle):
    return MarketState(
        symbol="BARC.L",
        bar_time=pd.Timestamp("2026-01-01"),
        ohlcv=pd.DataFrame(),
        indicators={"bundle": bundle},
        open_position=None,
        recent_trades=[],
        news=[],
        account={},
    )


def _bull_bundle():
    return IndicatorBundle(
        price=150.0,
        alligator=AlligatorResult(jaw=140.0, teeth=145.0, lips=148.0,
                                  state="EATING", direction="BULL"),
        ma200=MA200Result(value=130.0, trend="BULL"),
        wr=WilliamsRResult(value=-25.0, signal="ABOVE"),
        rsi=55.0,
        adx=ADXResult(value=30.0, trend_strength="STRONG"),
    )


def _bear_bundle():
    return IndicatorBundle(
        price=150.0,
        alligator=AlligatorResult(jaw=160.0, teeth=155.0, lips=152.0,
                                  state="EATING", direction="BEAR"),
        ma200=MA200Result(value=170.0, trend="BEAR"),
        wr=WilliamsRResult(value=-75.0, signal="BELOW"),
        rsi=35.0,
        adx=ADXResult(value=30.0, trend_strength="STRONG"),
    )


def _sleeping_bundle():
    return IndicatorBundle(
        price=150.0,
        alligator=AlligatorResult(jaw=150.0, teeth=150.0, lips=150.0,
                                  state="SLEEPING", direction="NONE"),
        ma200=MA200Result(value=150.0, trend="BULL"),
        wr=WilliamsRResult(value=-50.0, signal="ABOVE"),
        rsi=50.0,
        adx=ADXResult(value=15.0, trend_strength="WEAK"),
    )


class TestTripleConfirmationEngine:
    def test_name(self):
        engine = TripleConfirmationEngine()
        assert engine.name == "TripleConfirmationEngine"

    def test_buy_signal(self):
        engine = TripleConfirmationEngine()
        state = _make_state(_bull_bundle())
        signal = engine.generate_candidate(state)
        assert signal is not None
        assert signal.action == "BUY"
        assert signal.confidence == 0.9  # HIGH → 0.9

    def test_sell_signal(self):
        engine = TripleConfirmationEngine()
        state = _make_state(_bear_bundle())
        signal = engine.generate_candidate(state)
        assert signal is not None
        assert signal.action == "SELL"

    def test_hold_when_sleeping(self):
        engine = TripleConfirmationEngine()
        state = _make_state(_sleeping_bundle())
        signal = engine.generate_candidate(state)
        assert signal is not None
        assert signal.action == "HOLD"

    def test_none_when_no_bundle(self):
        engine = TripleConfirmationEngine()
        state = _make_state(None)
        signal = engine.generate_candidate(state)
        assert signal is None

    def test_manage_exit_returns_hold(self):
        engine = TripleConfirmationEngine()
        pos = PositionMetadata(
            position_id="pos-001", fill_id="fill-001",
            instrument="BARC.L",
            entry_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            entry_price=150.0, entry_quantity=100.0,
            entry_strategy="TripleConfirmationEngine",
            entry_regime="TRENDING",
        )
        state = _make_state(_bull_bundle())
        exit_dec = engine.manage_exit(pos, state)
        assert exit_dec.action == "HOLD"
