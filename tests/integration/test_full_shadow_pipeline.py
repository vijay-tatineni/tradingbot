"""
§11.4: End-to-end shadow pipeline contamination test.

Runs both shadow and live operations through the full pipeline
and verifies zero cross-contamination between table sets.
"""
import sqlite3
from datetime import datetime, timezone

import pytest

from bot.shadow.counterfactual_logger import CounterfactualLogger
from bot.shadow.position_metadata_store import PositionMetadataStore, CREATE_TABLE as PM_CREATE
from bot.regime.models import PositionMetadata


SHADOW_TABLES = {"shadow_decisions", "shadow_hypothetical_trades"}
LIVE_TABLES = {
    "position_metadata",
    "regime_classification_cache",
    "instrument_entry_pauses",
    "degradation_events",
    "macro_events",
}


def _create_all_tables(db_path: str) -> None:
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
    conn.execute("""CREATE TABLE IF NOT EXISTS regime_classification_cache (
        instrument TEXT NOT NULL, trading_date TEXT NOT NULL,
        input_hash TEXT NOT NULL, classification_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (instrument, trading_date, input_hash)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS instrument_entry_pauses (
        instrument TEXT PRIMARY KEY, paused_at TEXT NOT NULL,
        paused_by_overlay TEXT NOT NULL, reason TEXT NOT NULL,
        cleared_at TEXT, cleared_by TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS degradation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
        component TEXT NOT NULL, severity TEXT NOT NULL,
        trigger_reason TEXT NOT NULL, action_taken TEXT NOT NULL,
        flag_disabled TEXT, instruments_paused TEXT, recovery_instructions TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS macro_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, event_date TEXT NOT NULL,
        event_time TEXT, name TEXT NOT NULL, source TEXT NOT NULL,
        impact TEXT DEFAULT 'medium', created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    conn.commit()
    conn.close()


def _count_rows(db_path: str, table: str) -> int:
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def test_full_shadow_pipeline_no_contamination(tmp_path):
    """Run shadow + live operations. Verify zero cross-contamination."""
    db_path = str(tmp_path / "pipeline.db")
    _create_all_tables(db_path)

    cf_logger = CounterfactualLogger(db_path)
    pm_store = PositionMetadataStore(db_path)

    cf_logger.log_decision(
        instrument="AAPL", bar_time="2026-05-18T14:00:00",
        shadow_regime="TRENDING", shadow_engine="TripleConfirmationEngine",
        live_engine="TripleConfirmationEngine", live_action="BUY",
        shadow_action="BUY", disagreement_type=None,
    )

    cf_logger.open_hypothetical(
        trade_id="SH-001", instrument="AAPL",
        bar_time="2026-05-18T14:00:00",
        engine="TripleConfirmationEngine", regime="TRENDING",
        price=150.0, quantity=10.0,
    )

    cf_logger.log_decision(
        instrument="MSFT", bar_time="2026-05-18T14:00:00",
        shadow_regime="RANGING", shadow_engine="MeanReversionEngine",
        live_engine="TripleConfirmationEngine", live_action="HOLD",
        shadow_action="BUY", disagreement_type="ENGINE_MISMATCH",
        hypothetical_trade_id="SH-002",
    )

    cf_logger.open_hypothetical(
        trade_id="SH-002", instrument="MSFT",
        bar_time="2026-05-18T14:00:00",
        engine="MeanReversionEngine", regime="RANGING",
        price=400.0, quantity=5.0,
    )

    pm_store.persist(PositionMetadata(
        position_id="P1", fill_id="F1", instrument="AAPL",
        entry_time=datetime(2026, 5, 18, tzinfo=timezone.utc),
        entry_price=150.0, entry_quantity=10.0,
        entry_strategy="TripleConfirmationEngine",
        entry_regime="TRENDING",
        exit_policy="use_entry_strategy_rules",
    ))

    assert _count_rows(db_path, "shadow_decisions") == 2
    assert _count_rows(db_path, "shadow_hypothetical_trades") == 2
    assert _count_rows(db_path, "position_metadata") == 1

    for table in LIVE_TABLES - {"position_metadata"}:
        assert _count_rows(db_path, table) == 0, \
            f"Shadow code contaminated live table: {table}"

    for table in SHADOW_TABLES:
        pass

    cf_logger.close_hypothetical(
        trade_id="SH-001", bar_time="2026-05-19T14:00:00",
        exit_price=155.0, exit_reason="TRAILING_STOP",
        pnl=50.0, pnl_pct=3.33,
    )

    assert _count_rows(db_path, "shadow_hypothetical_trades") == 2
    closed = [h for h in cf_logger.get_open_hypotheticals("AAPL")]
    assert len(closed) == 0

    open_msft = cf_logger.get_open_hypotheticals("MSFT")
    assert len(open_msft) == 1

    for table in LIVE_TABLES - {"position_metadata"}:
        assert _count_rows(db_path, table) == 0, \
            f"Post-close: shadow contaminated {table}"
