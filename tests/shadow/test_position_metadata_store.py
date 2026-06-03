"""Tests for position metadata store — §12.1."""
import pytest
from datetime import datetime, timezone

from bot.regime.models import PositionMetadata
from bot.shadow.position_metadata_store import PositionMetadataStore


@pytest.fixture
def store(tmp_path):
    return PositionMetadataStore(str(tmp_path / "test.db"))


def _make_metadata(position_id="P1", fill_id="F1", instrument="AAPL",
                   quantity=10.0, strategy="TripleConfirmationEngine"):
    return PositionMetadata(
        position_id=position_id,
        fill_id=fill_id,
        instrument=instrument,
        entry_time=datetime(2026, 5, 18, 14, 0, tzinfo=timezone.utc),
        entry_price=150.0,
        entry_quantity=quantity,
        entry_strategy=strategy,
        entry_regime="TRENDING",
        entry_overlays_active=[],
        entry_prompt_version="v1",
        exit_policy="use_entry_strategy_rules",
    )


def test_persist_and_retrieve(store):
    meta = _make_metadata()
    store.persist(meta)
    fills = store.get_fills("P1")
    assert len(fills) == 1
    assert fills[0].position_id == "P1"
    assert fills[0].fill_id == "F1"
    assert fills[0].entry_strategy == "TripleConfirmationEngine"


def test_composite_key_two_fills(store):
    store.persist(_make_metadata(fill_id="EX-001", quantity=5.0))
    store.persist(_make_metadata(fill_id="EX-002", quantity=5.0))
    fills = store.get_fills("P1")
    assert len(fills) == 2
    assert fills[0].fill_id == "EX-001"
    assert fills[1].fill_id == "EX-002"


def test_aggregate_quantity(store):
    store.persist(_make_metadata(fill_id="EX-001", quantity=5.0))
    store.persist(_make_metadata(fill_id="EX-002", quantity=3.0))
    assert store.aggregate_quantity("P1") == 8.0


def test_aggregate_quantity_empty(store):
    assert store.aggregate_quantity("NONEXISTENT") == 0.0


def test_generate_fill_id_with_broker_id(store):
    fid = store.generate_fill_id("P1", broker_execution_id="EX-12345")
    assert fid == "EX-12345"


def test_generate_fill_id_fallback(store):
    fid1 = store.generate_fill_id("P1")
    fid2 = store.generate_fill_id("P1")
    assert fid1 == "P1-0000"
    assert fid2 == "P1-0001"


def test_generate_fill_id_per_position(store):
    fid_a = store.generate_fill_id("A")
    fid_b = store.generate_fill_id("B")
    assert fid_a == "A-0000"
    assert fid_b == "B-0000"


def test_overlays_round_trip(store):
    meta = _make_metadata()
    meta = PositionMetadata(
        position_id="P2", fill_id="F1", instrument="AAPL",
        entry_time=datetime(2026, 5, 18, tzinfo=timezone.utc),
        entry_price=150.0, entry_quantity=10.0,
        entry_strategy="TripleConfirmationEngine",
        entry_regime="TRENDING",
        entry_overlays_active=["MACRO_LOCKOUT", "LOW_LIQUIDITY"],
        entry_prompt_version="v1",
        exit_policy="use_entry_strategy_rules",
    )
    store.persist(meta)
    fills = store.get_fills("P2")
    assert fills[0].entry_overlays_active == ["MACRO_LOCKOUT", "LOW_LIQUIDITY"]


def test_persistence_across_instances(tmp_path):
    db_path = str(tmp_path / "persist.db")
    store1 = PositionMetadataStore(db_path)
    store1.persist(_make_metadata())
    store2 = PositionMetadataStore(db_path)
    fills = store2.get_fills("P1")
    assert len(fills) == 1
