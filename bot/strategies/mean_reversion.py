"""
MeanReversionEngine skeleton — §8.2 of CLAUDE_STRATEGY_SPEC_v3.

UNVALIDATED — gated by enable_mean_reversion_live flag.
Strategy-discovery research happens after shadow data accumulates.
"""
from typing import Optional

from bot.strategies.base import (
    StrategyEngine, MarketState, Signal, ExitDecision
)
from bot.regime.models import PositionMetadata


class MeanReversionEngine(StrategyEngine):
    name = "MeanReversionEngine"

    def generate_candidate(self, state: MarketState) -> Optional[Signal]:
        # Placeholder:
        # - Bollinger Bands (20, 2.0), RSI(14)
        # - BUY if close < lower band AND RSI < 30
        # - Long-only
        return None

    def manage_exit(self, position: PositionMetadata, state: MarketState) -> ExitDecision:
        # CLOSE at middle band or max_hold_days
        return ExitDecision(
            action="HOLD",
            new_stop=None,
            reason="MeanReversionEngine: skeleton, no exit logic yet",
        )
