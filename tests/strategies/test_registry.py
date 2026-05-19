"""Unit tests for strategy registry — §8.4."""
import pytest
from bot.strategies.registry import get_engine
from bot.strategies.triple_confirmation import TripleConfirmationEngine
from bot.strategies.mean_reversion import MeanReversionEngine
from bot.strategies.noop import NoOpEngine


class TestRegistry:
    def test_get_triple_confirmation(self):
        engine = get_engine("TripleConfirmationEngine")
        assert isinstance(engine, TripleConfirmationEngine)

    def test_get_mean_reversion(self):
        engine = get_engine("MeanReversionEngine")
        assert isinstance(engine, MeanReversionEngine)

    def test_get_noop(self):
        engine = get_engine("NoOpEngine")
        assert isinstance(engine, NoOpEngine)

    def test_unknown_engine_raises(self):
        with pytest.raises(ValueError, match="Unknown engine"):
            get_engine("FakeEngine")

    def test_each_engine_has_name(self):
        for name in ("TripleConfirmationEngine", "MeanReversionEngine", "NoOpEngine"):
            engine = get_engine(name)
            assert engine.name == name
