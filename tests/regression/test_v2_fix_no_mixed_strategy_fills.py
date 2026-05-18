"""
Regression test: assertion fires on exit with mixed-strategy fills.

Before v2: silently picking one engine for exit when fills have different
entry strategies. This masks a data integrity error.

After v2: AssertionError raised, forcing investigation of how fills
from different strategies ended up in the same position.
"""
import pytest
from datetime import datetime, timezone

from bot.regime.models import PositionMetadata
from bot.shadow.exit_policy import get_exit_engine


def test_mixed_strategy_fills_raise_assertion():
    """Regression: mixed-strategy fills must raise, not silently resolve."""
    fills = [
        PositionMetadata(
            position_id="P1", fill_id="F1",
            instrument="AAPL",
            entry_time=datetime(2026, 5, 18, tzinfo=timezone.utc),
            entry_price=150.0, entry_quantity=5.0,
            entry_strategy="TripleConfirmationEngine",
            entry_regime="TRENDING",
            exit_policy="use_entry_strategy_rules",
        ),
        PositionMetadata(
            position_id="P1", fill_id="F2",
            instrument="AAPL",
            entry_time=datetime(2026, 5, 18, tzinfo=timezone.utc),
            entry_price=151.0, entry_quantity=5.0,
            entry_strategy="MeanReversionEngine",
            entry_regime="RANGING",
            exit_policy="use_entry_strategy_rules",
        ),
    ]
    with pytest.raises(AssertionError, match="multiple strategies"):
        get_exit_engine(fills)


def test_consistent_strategy_fills_do_not_raise():
    """Regression counterpart: same-strategy fills work normally."""
    fills = [
        PositionMetadata(
            position_id="P1", fill_id="F1",
            instrument="AAPL",
            entry_time=datetime(2026, 5, 18, tzinfo=timezone.utc),
            entry_price=150.0, entry_quantity=5.0,
            entry_strategy="TripleConfirmationEngine",
            entry_regime="TRENDING",
            exit_policy="use_entry_strategy_rules",
        ),
        PositionMetadata(
            position_id="P1", fill_id="F2",
            instrument="AAPL",
            entry_time=datetime(2026, 5, 18, tzinfo=timezone.utc),
            entry_price=151.0, entry_quantity=5.0,
            entry_strategy="TripleConfirmationEngine",
            entry_regime="TRENDING",
            exit_policy="use_entry_strategy_rules",
        ),
    ]
    engine = get_exit_engine(fills)
    assert engine.name == "TripleConfirmationEngine"
