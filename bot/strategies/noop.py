"""
NoOpEngine — §8.3 of CLAUDE_STRATEGY_SPEC_v3.

Generates no candidates and holds all positions.
Used when regime is UNCLEAR or mean-reversion is not enabled.
"""
from typing import Optional

from bot.strategies.base import (
    StrategyEngine, MarketState, Signal, ExitDecision
)
from bot.regime.models import PositionMetadata


class NoOpEngine(StrategyEngine):
    name = "NoOpEngine"

    def generate_candidate(self, state: MarketState) -> Optional[Signal]:
        return None

    def manage_exit(self, position: PositionMetadata, state: MarketState) -> ExitDecision:
        return ExitDecision(
            action="HOLD",
            new_stop=None,
            reason="NoOpEngine manages nothing",
        )
