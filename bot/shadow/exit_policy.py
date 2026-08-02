"""
Exit policy enforcement — §12.2 of CLAUDE_STRATEGY_SPEC_v3.

Entry-regime exit contract: positions exit via the engine that entered them,
not the current routing. Mixed-strategy fills raise AssertionError on exit.
"""
from bot.regime.models import PositionMetadata
from bot.strategies.base import MarketState, ExitDecision
from bot.strategies.registry import get_engine


def get_exit_engine(rows: list[PositionMetadata]):
    """Return the strategy engine that should manage exit for this position.

    Asserts all fills share the same entry_strategy. Mixed-strategy fills
    are a data integrity error and must be caught at exit time, not silently
    resolved by picking one engine.
    """
    strategies = {m.entry_strategy for m in rows}
    assert len(strategies) == 1, \
        f"Position has fills tagged with multiple strategies: {strategies}"
    policies = {m.exit_policy for m in rows}
    assert policies == {"use_entry_strategy_rules"}, \
        f"Unknown or mixed exit_policy: {policies}"
    return get_engine(strategies.pop())


def aggregate_position(rows: list[PositionMetadata]) -> PositionMetadata:
    """Create a synthetic PositionMetadata representing the aggregate position.

    Uses the first fill's metadata as base, with aggregated quantity.
    """
    assert len(rows) > 0, "Cannot aggregate empty fills list"
    base = rows[0]
    total_qty = sum(m.entry_quantity for m in rows)
    avg_price = (sum(m.entry_price * m.entry_quantity for m in rows)
                 / total_qty) if total_qty > 0 else base.entry_price
    return PositionMetadata(
        position_id=base.position_id,
        fill_id=base.fill_id,
        instrument=base.instrument,
        entry_time=base.entry_time,
        entry_price=avg_price,
        entry_quantity=total_qty,
        entry_strategy=base.entry_strategy,
        entry_regime=base.entry_regime,
        entry_overlays_active=base.entry_overlays_active,
        entry_prompt_version=base.entry_prompt_version,
        exit_policy=base.exit_policy,
    )


def compute_exit(rows: list[PositionMetadata], state: MarketState) -> ExitDecision:
    """Dispatch exit decision via the entry strategy engine."""
    engine = get_exit_engine(rows)
    return engine.manage_exit(aggregate_position(rows), state)
