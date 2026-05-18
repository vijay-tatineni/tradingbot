"""
Regression test: exit dispatches via entry strategy regardless of
current router state.

Before v2: exit might use whatever engine the current routing selects,
which could differ from the entry engine if the regime has changed.

After v2: exit uses the strategy engine that tagged the fills, period.
Current routing state is not consulted for exit decisions.
"""
import pytest
from datetime import datetime, timezone

import pandas as pd

from bot.regime.models import PositionMetadata
from bot.shadow.exit_policy import get_exit_engine, compute_exit
from bot.strategies.base import MarketState, ExitDecision


def test_exit_via_entry_strategy_not_current_routing():
    """Regression: even if current routing would select MeanReversion (RANGING),
    a position tagged TripleConfirmation exits via TripleConfirmation."""
    fills = [
        PositionMetadata(
            position_id="P1", fill_id="F1",
            instrument="AAPL",
            entry_time=datetime(2026, 5, 18, tzinfo=timezone.utc),
            entry_price=150.0, entry_quantity=10.0,
            entry_strategy="TripleConfirmationEngine",
            entry_regime="TRENDING",
            exit_policy="use_entry_strategy_rules",
        ),
    ]
    engine = get_exit_engine(fills)
    assert engine.name == "TripleConfirmationEngine", \
        "REGRESSION: Exit must use entry strategy, not current routing"


def test_exit_produces_decision_from_entry_engine():
    """Regression: compute_exit dispatches to entry engine and returns
    a valid ExitDecision."""
    fills = [
        PositionMetadata(
            position_id="P1", fill_id="F1",
            instrument="AAPL",
            entry_time=datetime(2026, 5, 18, tzinfo=timezone.utc),
            entry_price=150.0, entry_quantity=10.0,
            entry_strategy="TripleConfirmationEngine",
            entry_regime="TRENDING",
            exit_policy="use_entry_strategy_rules",
        ),
    ]
    state = MarketState(
        symbol="AAPL",
        bar_time=pd.Timestamp("2026-05-19 14:00:00", tz="UTC"),
        ohlcv=pd.DataFrame(),
        indicators={},
        open_position=None,
        recent_trades=[],
        news=[],
        account={},
    )
    decision = compute_exit(fills, state)
    assert isinstance(decision, ExitDecision), \
        "REGRESSION: compute_exit must return ExitDecision"
    assert decision.action in ("HOLD", "CLOSE", "ADJUST_STOP")


def test_mean_reversion_tagged_exits_via_mean_reversion():
    """Regression: MR-tagged position exits via MR engine, even when
    current regime is TRENDING."""
    fills = [
        PositionMetadata(
            position_id="P2", fill_id="F1",
            instrument="MSFT",
            entry_time=datetime(2026, 5, 18, tzinfo=timezone.utc),
            entry_price=400.0, entry_quantity=5.0,
            entry_strategy="MeanReversionEngine",
            entry_regime="RANGING",
            exit_policy="use_entry_strategy_rules",
        ),
    ]
    engine = get_exit_engine(fills)
    assert engine.name == "MeanReversionEngine", \
        "REGRESSION: MR-tagged position must exit via MR engine"
