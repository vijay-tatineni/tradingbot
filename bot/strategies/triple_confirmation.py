"""
TripleConfirmationEngine — §8.1 of CLAUDE_STRATEGY_SPEC_v3.

Thin wrapper around existing SignalEngine and PositionTracker exit logic.
Does NOT rewrite indicator logic. Wraps existing functions.
"""
from typing import Optional

from bot.indicators import IndicatorBundle
from bot.signals import SignalEngine, SignalResult
from bot.strategies.base import (
    StrategyEngine, MarketState, Signal, ExitDecision
)
from bot.regime.models import PositionMetadata


class TripleConfirmationEngine(StrategyEngine):
    name = "TripleConfirmationEngine"

    def __init__(self):
        self._signal_engine = SignalEngine()

    def generate_candidate(self, state: MarketState) -> Optional[Signal]:
        bundle = state.indicators.get("bundle") if isinstance(state.indicators, dict) else None
        if bundle is None:
            return None

        result: SignalResult = self._signal_engine.evaluate(bundle)

        if result.signal == 0:
            return Signal(
                action="HOLD",
                confidence=0.0,
                size_hint=None,
                stop_hint=None,
                reason=result.reason,
                raw={"signal_result": result},
            )

        action = "BUY" if result.signal == 1 else "SELL"
        confidence_map = {"HIGH": 0.9, "MEDIUM": 0.7, "LOW": 0.3}
        confidence = confidence_map.get(result.confidence, 0.5)

        return Signal(
            action=action,
            confidence=confidence,
            size_hint=None,
            stop_hint=None,
            reason=result.reason,
            raw={"signal_result": result},
        )

    def manage_exit(self, position: PositionMetadata, state: MarketState) -> ExitDecision:
        """Delegate to existing exit logic semantics.

        The actual trailing-stop / take-profit / emergency-stop logic lives
        in PositionTracker and layer1.py's two-tier system. This wrapper
        exposes the interface so the entry-regime exit contract can dispatch
        here, but the real exit checks happen in the main loop until
        enable_position_tagged_exit_policy is flipped live.
        """
        return ExitDecision(
            action="HOLD",
            new_stop=None,
            reason="TripleConfirmationEngine: exit managed by legacy path",
        )
