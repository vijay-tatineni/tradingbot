"""Unit tests for StrategyEngine ABC — §8."""
import pytest
from bot.strategies.base import StrategyEngine, MarketState, Signal, ExitDecision


class TestStrategyEngineABC:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            StrategyEngine()

    def test_signal_dataclass(self):
        s = Signal(
            action="BUY",
            confidence=0.9,
            size_hint=100.0,
            stop_hint=145.0,
            reason="Triple confirmation",
            raw={"signal": 1},
        )
        assert s.action == "BUY"
        assert s.confidence == 0.9

    def test_exit_decision_dataclass(self):
        e = ExitDecision(
            action="CLOSE",
            new_stop=None,
            reason="Take profit hit",
        )
        assert e.action == "CLOSE"
