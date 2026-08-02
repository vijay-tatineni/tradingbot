"""
§12.2 invariant: Entry-regime exit contract.

TripleConfirmation-tagged position exits via TripleConfirmation engine
even when current routing for that instrument is MeanReversion.

Exit dispatches via entry strategy, not current router state.
"""
from datetime import datetime, timezone

import pandas as pd
import pytest

from bot.regime.models import PositionMetadata
from bot.shadow.exit_policy import get_exit_engine, compute_exit
from bot.strategies.base import MarketState, ExitDecision
from bot.strategies.triple_confirmation import TripleConfirmationEngine
from bot.strategies.mean_reversion import MeanReversionEngine


def _make_fills(strategy="TripleConfirmationEngine"):
    return [
        PositionMetadata(
            position_id="P1", fill_id="EX-001",
            instrument="AAPL",
            entry_time=datetime(2026, 5, 18, 14, 0, tzinfo=timezone.utc),
            entry_price=150.0, entry_quantity=5.0,
            entry_strategy=strategy, entry_regime="TRENDING",
            exit_policy="use_entry_strategy_rules",
        ),
        PositionMetadata(
            position_id="P1", fill_id="EX-002",
            instrument="AAPL",
            entry_time=datetime(2026, 5, 18, 14, 5, tzinfo=timezone.utc),
            entry_price=151.0, entry_quantity=5.0,
            entry_strategy=strategy, entry_regime="TRENDING",
            exit_policy="use_entry_strategy_rules",
        ),
    ]


def _make_state():
    return MarketState(
        symbol="AAPL",
        bar_time=pd.Timestamp("2026-05-19 14:00:00", tz="UTC"),
        ohlcv=pd.DataFrame(),
        indicators={},
        open_position=None,
        recent_trades=[],
        news=[],
        account={},
    )


def test_triple_confirmation_tagged_exits_via_triple_confirmation():
    """Even if current routing would select MeanReversion, the position
    exits via its entry strategy (TripleConfirmation)."""
    fills = _make_fills("TripleConfirmationEngine")
    engine = get_exit_engine(fills)
    assert isinstance(engine, TripleConfirmationEngine)
    assert engine.name == "TripleConfirmationEngine"


def test_mean_reversion_tagged_exits_via_mean_reversion():
    fills = _make_fills("MeanReversionEngine")
    engine = get_exit_engine(fills)
    assert isinstance(engine, MeanReversionEngine)
    assert engine.name == "MeanReversionEngine"


def test_exit_contract_produces_valid_decision():
    fills = _make_fills("TripleConfirmationEngine")
    state = _make_state()
    decision = compute_exit(fills, state)
    assert isinstance(decision, ExitDecision)
    assert decision.action in ("HOLD", "CLOSE", "ADJUST_STOP")


def test_mixed_strategy_fills_raise_on_exit():
    """Mixed-strategy fills are a data integrity error. Must raise, not
    silently pick one engine."""
    fills = [
        PositionMetadata(
            position_id="P1", fill_id="F1", instrument="AAPL",
            entry_time=datetime(2026, 5, 18, tzinfo=timezone.utc),
            entry_price=150.0, entry_quantity=5.0,
            entry_strategy="TripleConfirmationEngine",
            entry_regime="TRENDING",
            exit_policy="use_entry_strategy_rules",
        ),
        PositionMetadata(
            position_id="P1", fill_id="F2", instrument="AAPL",
            entry_time=datetime(2026, 5, 18, tzinfo=timezone.utc),
            entry_price=151.0, entry_quantity=5.0,
            entry_strategy="MeanReversionEngine",
            entry_regime="RANGING",
            exit_policy="use_entry_strategy_rules",
        ),
    ]
    with pytest.raises(AssertionError, match="multiple strategies"):
        get_exit_engine(fills)


def test_exit_engine_is_independent_of_current_routing():
    """The exit engine depends ONLY on the fills' entry_strategy,
    never on a passed-in routing decision or current regime."""
    fills = _make_fills("TripleConfirmationEngine")
    engine = get_exit_engine(fills)
    # No routing decision or current regime was passed — the engine
    # is determined solely by the fills' metadata
    assert engine.name == "TripleConfirmationEngine"
