"""
Strategy engine registry — §8.4 of CLAUDE_STRATEGY_SPEC_v3.
"""
from bot.strategies.base import StrategyEngine
from bot.strategies.triple_confirmation import TripleConfirmationEngine
from bot.strategies.mean_reversion import MeanReversionEngine
from bot.strategies.noop import NoOpEngine


_REGISTRY: dict[str, type[StrategyEngine]] = {
    "TripleConfirmationEngine": TripleConfirmationEngine,
    "MeanReversionEngine": MeanReversionEngine,
    "NoOpEngine": NoOpEngine,
}


def get_engine(name: str) -> StrategyEngine:
    if name not in _REGISTRY:
        raise ValueError(f"Unknown engine: {name}. Known: {list(_REGISTRY)}")
    return _REGISTRY[name]()
