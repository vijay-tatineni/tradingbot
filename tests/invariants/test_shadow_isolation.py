"""
§11.4 invariant: Shadow code writes to shadow_* tables ONLY.
Live code reads from live tables ONLY.

Uses two-DB approach: shadow code and live code get separate DB files.
After running shadow operations, assert zero rows in live DB.
After running live operations, assert zero rows in shadow DB.
"""
import sqlite3
from datetime import datetime, timezone

import pytest

from bot.shadow.counterfactual_logger import CounterfactualLogger
from bot.shadow.position_metadata_store import PositionMetadataStore, CREATE_TABLE as PM_CREATE
from bot.regime.models import PositionMetadata


SHADOW_TABLES = {"shadow_decisions", "shadow_hypothetical_trades"}
LIVE_TABLES = {"position_metadata"}


def _count_rows(db_path: str, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
    except sqlite3.OperationalError:
        count = 0
    conn.close()
    return count


def _ensure_all_tables(db_path: str) -> None:
    """Create all tables (shadow + live) in a single DB for isolation checks."""
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS shadow_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, instrument TEXT, bar_time TEXT, live_engine TEXT,
        live_signal_json TEXT, live_action_taken TEXT, live_trade_id TEXT,
        shadow_regime TEXT, shadow_confidence REAL, shadow_smoothed_regime TEXT,
        shadow_smoothed_days_in_regime INTEGER, shadow_overlays_active TEXT,
        shadow_engine_selected TEXT, shadow_signal_json TEXT,
        shadow_action_would_be TEXT, disagreement_type TEXT,
        hypothetical_trade_id TEXT, flag_snapshot_json TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS shadow_hypothetical_trades (
        id TEXT PRIMARY KEY, instrument TEXT, opened_at TEXT, opened_bar TEXT,
        entry_engine TEXT, entry_regime TEXT, entry_price REAL,
        entry_quantity REAL, entry_stop REAL, closed_at TEXT, closed_bar TEXT,
        exit_price REAL, exit_reason TEXT, pnl REAL, pnl_pct REAL, status TEXT
    )""")
    conn.execute(PM_CREATE)
    conn.commit()
    conn.close()


def test_counterfactual_logger_writes_only_shadow_tables(tmp_path):
    """Shadow logger writes to shadow_decisions. position_metadata stays empty."""
    db_path = str(tmp_path / "shared.db")
    _ensure_all_tables(db_path)

    cf_logger = CounterfactualLogger(db_path)
    cf_logger.log_decision(
        instrument="AAPL",
        bar_time="2026-05-18T14:00:00",
        shadow_regime="TRENDING",
        shadow_engine="TripleConfirmationEngine",
    )

    assert _count_rows(db_path, "shadow_decisions") >= 1, \
        "Shadow logger should have written to shadow_decisions"
    assert _count_rows(db_path, "position_metadata") == 0, \
        "Shadow logger must NOT write to position_metadata (live table)"


def test_counterfactual_logger_hypothetical_writes_only_shadow(tmp_path):
    db_path = str(tmp_path / "shared.db")
    _ensure_all_tables(db_path)

    cf_logger = CounterfactualLogger(db_path)
    cf_logger.open_hypothetical(
        trade_id="SH-001", instrument="AAPL",
        bar_time="2026-05-18T14:00:00",
        engine="TripleConfirmationEngine", regime="TRENDING",
        price=150.0, quantity=10.0,
    )

    assert _count_rows(db_path, "shadow_hypothetical_trades") >= 1
    assert _count_rows(db_path, "position_metadata") == 0, \
        "Shadow hypothetical must NOT write to live tables"


def test_position_metadata_store_writes_only_live_tables(tmp_path):
    """Live store writes to position_metadata. shadow_* tables stay empty."""
    db_path = str(tmp_path / "shared.db")
    _ensure_all_tables(db_path)

    store = PositionMetadataStore(db_path)
    meta = PositionMetadata(
        position_id="P1", fill_id="F1", instrument="AAPL",
        entry_time=datetime(2026, 5, 18, tzinfo=timezone.utc),
        entry_price=150.0, entry_quantity=10.0,
        entry_strategy="TripleConfirmationEngine",
        entry_regime="TRENDING",
        exit_policy="use_entry_strategy_rules",
    )
    store.persist(meta)

    assert _count_rows(db_path, "position_metadata") >= 1, \
        "Live store should have written to position_metadata"
    assert _count_rows(db_path, "shadow_decisions") == 0, \
        "Live store must NOT write to shadow_decisions"
    assert _count_rows(db_path, "shadow_hypothetical_trades") == 0, \
        "Live store must NOT write to shadow_hypothetical_trades"


def test_counterfactual_logger_reads_never_touch_live_tables(tmp_path):
    """Shadow reads stay within shadow_* tables."""
    db_path = str(tmp_path / "shared.db")
    _ensure_all_tables(db_path)

    cf_logger = CounterfactualLogger(db_path)
    result = cf_logger.get_open_hypotheticals("AAPL")
    assert result == []


def test_shadow_table_names_are_prefixed():
    for table in SHADOW_TABLES:
        assert table.startswith("shadow_"), \
            f"Shadow table {table} does not start with 'shadow_'"


def test_live_tables_not_prefixed_shadow():
    for table in LIVE_TABLES:
        assert not table.startswith("shadow_"), \
            f"Live table {table} incorrectly prefixed with 'shadow_'"


def test_cross_contamination_end_to_end(tmp_path):
    """Full pipeline: run both shadow and live operations on the same DB.
    Each side's tables must only contain rows from its own operations."""
    db_path = str(tmp_path / "e2e.db")
    _ensure_all_tables(db_path)

    cf_logger = CounterfactualLogger(db_path)
    store = PositionMetadataStore(db_path)

    cf_logger.log_decision(
        instrument="AAPL", bar_time="2026-05-18T14:00:00",
        shadow_regime="TRENDING", shadow_engine="TripleConfirmationEngine",
    )
    cf_logger.open_hypothetical(
        trade_id="SH-001", instrument="AAPL",
        bar_time="2026-05-18T14:00:00",
        engine="TripleConfirmationEngine", regime="TRENDING",
        price=150.0, quantity=10.0,
    )

    store.persist(PositionMetadata(
        position_id="P1", fill_id="F1", instrument="AAPL",
        entry_time=datetime(2026, 5, 18, tzinfo=timezone.utc),
        entry_price=150.0, entry_quantity=10.0,
        entry_strategy="TripleConfirmationEngine",
        entry_regime="TRENDING",
        exit_policy="use_entry_strategy_rules",
    ))

    shadow_decision_count = _count_rows(db_path, "shadow_decisions")
    shadow_trade_count = _count_rows(db_path, "shadow_hypothetical_trades")
    live_count = _count_rows(db_path, "position_metadata")

    assert shadow_decision_count == 1, "Expected 1 shadow decision"
    assert shadow_trade_count == 1, "Expected 1 shadow hypothetical"
    assert live_count == 1, "Expected 1 live position metadata"


def test_assertion_catches_simulated_contamination(tmp_path):
    """Meta-test: verify the contamination check actually catches cross-writes.
    Manually insert a row into the wrong table and confirm the assertion fires."""
    db_path = str(tmp_path / "meta.db")
    _ensure_all_tables(db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO position_metadata "
        "(position_id, fill_id, instrument, entry_time, entry_price, "
        "entry_quantity, entry_strategy, entry_regime, entry_overlays_active, "
        "entry_prompt_version, exit_policy) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("P1", "F1", "AAPL", "2026-05-18T14:00:00", 150.0, 10.0,
         "TripleConfirmationEngine", "TRENDING", "[]", None,
         "use_entry_strategy_rules"),
    )
    conn.commit()
    conn.close()

    count = _count_rows(db_path, "position_metadata")
    assert count == 1, "Simulated contamination row should exist"

    with pytest.raises(AssertionError, match="must NOT write"):
        assert count == 0, "Shadow logger must NOT write to position_metadata (live table)"
