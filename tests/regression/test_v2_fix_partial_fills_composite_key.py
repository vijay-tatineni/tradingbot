"""
Regression test: v2 partial fills with composite key.

Two partial fills must create two rows with same position_id,
distinct fill_id. The composite PK (position_id, fill_id) enforces this.
"""
import pytest
from datetime import datetime, timezone

from bot.regime.models import PositionMetadata
from bot.shadow.position_metadata_store import PositionMetadataStore


def test_two_partial_fills_create_two_rows(tmp_path):
    """Regression: partial fills must create separate rows, not overwrite."""
    store = PositionMetadataStore(str(tmp_path / "test.db"))

    fill1 = PositionMetadata(
        position_id="P1", fill_id="EX-001",
        instrument="AAPL",
        entry_time=datetime(2026, 5, 18, 14, 0, tzinfo=timezone.utc),
        entry_price=150.0, entry_quantity=5.0,
        entry_strategy="TripleConfirmationEngine",
        entry_regime="TRENDING",
        exit_policy="use_entry_strategy_rules",
    )
    fill2 = PositionMetadata(
        position_id="P1", fill_id="EX-002",
        instrument="AAPL",
        entry_time=datetime(2026, 5, 18, 14, 1, tzinfo=timezone.utc),
        entry_price=150.50, entry_quantity=5.0,
        entry_strategy="TripleConfirmationEngine",
        entry_regime="TRENDING",
        exit_policy="use_entry_strategy_rules",
    )

    store.persist(fill1)
    store.persist(fill2)

    fills = store.get_fills("P1")
    assert len(fills) == 2, \
        f"REGRESSION: Expected 2 fills, got {len(fills)}"
    assert fills[0].fill_id != fills[1].fill_id, \
        "REGRESSION: Fill IDs must be distinct"
    assert fills[0].position_id == fills[1].position_id == "P1", \
        "REGRESSION: Both fills must share position_id"


def test_aggregate_quantity_sums_partial_fills(tmp_path):
    """Regression: aggregate quantity = SUM(entry_quantity)."""
    store = PositionMetadataStore(str(tmp_path / "test.db"))

    for i, qty in enumerate([3.0, 4.0, 3.0]):
        store.persist(PositionMetadata(
            position_id="P1", fill_id=f"EX-{i:03d}",
            instrument="AAPL",
            entry_time=datetime(2026, 5, 18, 14, i, tzinfo=timezone.utc),
            entry_price=150.0, entry_quantity=qty,
            entry_strategy="TripleConfirmationEngine",
            entry_regime="TRENDING",
            exit_policy="use_entry_strategy_rules",
        ))

    assert store.aggregate_quantity("P1") == 10.0, \
        "REGRESSION: Aggregate quantity must sum all partial fills"


def test_fallback_fill_id_format(tmp_path):
    """Regression: fallback fill_id uses f'{position_id}-{seq:04d}' format."""
    store = PositionMetadataStore(str(tmp_path / "test.db"))

    fid = store.generate_fill_id("P1")
    assert fid == "P1-0000", \
        "REGRESSION: Fallback fill_id must be position_id-NNNN"

    fid2 = store.generate_fill_id("P1")
    assert fid2 == "P1-0001", \
        "REGRESSION: Fallback fill_id must increment"
