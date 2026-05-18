"""
§11.4 invariant: Shadow code writes to shadow_* tables ONLY.
Live code reads from live tables ONLY.

Mock-spy enforcement: assert at test time that no shadow code path
touched a live table, no live code path touched a shadow table.
"""
import sqlite3
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from bot.shadow.counterfactual_logger import CounterfactualLogger
from bot.shadow.position_metadata_store import PositionMetadataStore


SHADOW_TABLES = {"shadow_decisions", "shadow_hypothetical_trades"}
LIVE_TABLES = {"position_metadata", "regime_classification_cache",
               "instrument_entry_pauses", "degradation_events",
               "macro_events"}


class TableAccessTracker:
    """Tracks which tables are accessed via SQLite execute calls."""

    def __init__(self, real_conn):
        self._real_conn = real_conn
        self.tables_written = set()
        self.tables_read = set()

    def execute(self, sql, params=None):
        sql_upper = sql.upper().strip()
        for table in SHADOW_TABLES | LIVE_TABLES:
            if table.upper() in sql_upper:
                if any(kw in sql_upper for kw in ("INSERT", "UPDATE", "DELETE", "CREATE")):
                    self.tables_written.add(table)
                elif "SELECT" in sql_upper:
                    self.tables_read.add(table)
        if params:
            return self._real_conn.execute(sql, params)
        return self._real_conn.execute(sql)

    def commit(self):
        self._real_conn.commit()

    def close(self):
        self._real_conn.close()

    @property
    def row_factory(self):
        return self._real_conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._real_conn.row_factory = value

    def fetchone(self):
        return self._real_conn.fetchone()


def test_counterfactual_logger_writes_only_shadow_tables(tmp_path):
    """Shadow counterfactual logger must ONLY write to shadow_* tables."""
    db_path = str(tmp_path / "test.db")
    cf_logger = CounterfactualLogger(db_path)

    tracker = TableAccessTracker(sqlite3.connect(db_path))

    with patch("sqlite3.connect", return_value=tracker):
        cf_logger.log_decision(
            instrument="AAPL",
            bar_time="2026-05-18T14:00:00",
            shadow_regime="TRENDING",
            shadow_engine="TripleConfirmationEngine",
        )

    for table in tracker.tables_written:
        assert table in SHADOW_TABLES, \
            f"Shadow code wrote to non-shadow table: {table}"

    for table in tracker.tables_written:
        assert table not in LIVE_TABLES, \
            f"Shadow code wrote to live table: {table}"


def test_counterfactual_logger_hypothetical_writes_only_shadow(tmp_path):
    db_path = str(tmp_path / "test.db")
    cf_logger = CounterfactualLogger(db_path)

    tracker = TableAccessTracker(sqlite3.connect(db_path))

    with patch("sqlite3.connect", return_value=tracker):
        cf_logger.open_hypothetical(
            trade_id="SH-001", instrument="AAPL",
            bar_time="2026-05-18T14:00:00",
            engine="TripleConfirmationEngine", regime="TRENDING",
            price=150.0, quantity=10.0,
        )

    for table in tracker.tables_written:
        assert table in SHADOW_TABLES, \
            f"Shadow hypothetical code wrote to non-shadow table: {table}"


def test_position_metadata_store_writes_only_live_tables(tmp_path):
    """Live position metadata store must NOT write to shadow_* tables."""
    from bot.regime.models import PositionMetadata

    db_path = str(tmp_path / "test.db")
    store = PositionMetadataStore(db_path)

    tracker = TableAccessTracker(sqlite3.connect(db_path))

    meta = PositionMetadata(
        position_id="P1", fill_id="F1", instrument="AAPL",
        entry_time=datetime(2026, 5, 18, tzinfo=timezone.utc),
        entry_price=150.0, entry_quantity=10.0,
        entry_strategy="TripleConfirmationEngine",
        entry_regime="TRENDING",
        exit_policy="use_entry_strategy_rules",
    )

    with patch("sqlite3.connect", return_value=tracker):
        store.persist(meta)

    for table in tracker.tables_written:
        assert table not in SHADOW_TABLES, \
            f"Live code wrote to shadow table: {table}"


def test_counterfactual_logger_never_reads_live_tables(tmp_path):
    """Shadow logger must not read from live tables."""
    db_path = str(tmp_path / "test.db")
    cf_logger = CounterfactualLogger(db_path)

    tracker = TableAccessTracker(sqlite3.connect(db_path))

    with patch("sqlite3.connect", return_value=tracker):
        cf_logger.get_open_hypotheticals("AAPL")

    for table in tracker.tables_read:
        assert table not in LIVE_TABLES, \
            f"Shadow code read from live table: {table}"


def test_shadow_table_names_are_prefixed():
    """All shadow tables must be prefixed with 'shadow_'."""
    cf_logger = CounterfactualLogger.__new__(CounterfactualLogger)
    cf_logger._db_path = ":memory:"
    for table in SHADOW_TABLES:
        assert table.startswith("shadow_"), \
            f"Shadow table {table} does not start with 'shadow_'"


def test_live_tables_not_prefixed_shadow():
    """No live table should start with 'shadow_'."""
    for table in LIVE_TABLES:
        assert not table.startswith("shadow_"), \
            f"Live table {table} incorrectly prefixed with 'shadow_'"
