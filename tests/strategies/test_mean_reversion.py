"""Unit tests for MeanReversionEngine skeleton — §8.2."""
from bot.strategies.mean_reversion import MeanReversionEngine
from bot.strategies.base import MarketState
from bot.regime.models import PositionMetadata
from datetime import datetime, timezone
import pandas as pd


class TestMeanReversionEngine:
    def test_name(self):
        engine = MeanReversionEngine()
        assert engine.name == "MeanReversionEngine"

    def test_generate_candidate_returns_none(self):
        engine = MeanReversionEngine()
        state = MarketState(
            symbol="BARC.L",
            bar_time=pd.Timestamp("2026-01-01"),
            ohlcv=pd.DataFrame(),
            indicators={},
            open_position=None,
            recent_trades=[],
            news=[],
            account={},
        )
        assert engine.generate_candidate(state) is None

    def test_manage_exit_returns_hold(self):
        engine = MeanReversionEngine()
        pos = PositionMetadata(
            position_id="pos-001", fill_id="fill-001",
            instrument="BARC.L",
            entry_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            entry_price=150.0, entry_quantity=100.0,
            entry_strategy="MeanReversionEngine",
            entry_regime="RANGING",
        )
        state = MarketState(
            symbol="BARC.L",
            bar_time=pd.Timestamp("2026-01-01"),
            ohlcv=pd.DataFrame(),
            indicators={},
            open_position=None,
            recent_trades=[],
            news=[],
            account={},
        )
        exit_dec = engine.manage_exit(pos, state)
        assert exit_dec.action == "HOLD"
