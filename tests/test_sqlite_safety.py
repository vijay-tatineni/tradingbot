"""
tests/test_sqlite_safety.py — Verify all SQLite connections set busy_timeout and WAL mode.
"""

import sqlite3
import tempfile
import os

import pytest


def test_positions_db_has_busy_timeout():
    """PositionTracker._connect() should set PRAGMA busy_timeout."""
    from bot.position_tracker import PositionTracker
    from unittest.mock import MagicMock

    cfg = MagicMock()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "positions.db")
        tracker = PositionTracker.__new__(PositionTracker)
        tracker.cfg = cfg
        tracker.db_path = db_path
        tracker.open = {}
        tracker.watching = {}
        tracker._missing_counts = {}
        tracker._init_db()

        conn = tracker._connect()
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()

        assert timeout == 5000, f"Expected busy_timeout=5000, got {timeout}"
        assert journal == "wal", f"Expected journal_mode=wal, got {journal}"


def test_learning_loop_db_has_busy_timeout():
    """LearningLoop._connect() should set PRAGMA busy_timeout."""
    from bot.plugins.learning_loop import LearningLoop
    from unittest.mock import MagicMock
    import datetime

    cfg = MagicMock()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "learning_loop.db")
        ll = LearningLoop.__new__(LearningLoop)
        ll.cfg = cfg
        ll.db = db_path
        ll._last_retrain_time = datetime.datetime.utcnow()

        conn = ll._connect()
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()

        assert timeout == 5000, f"Expected busy_timeout=5000, got {timeout}"
        assert journal == "wal", f"Expected journal_mode=wal, got {journal}"


def test_backtest_db_has_busy_timeout():
    """backtest.database.get_connection() should set PRAGMA busy_timeout."""
    import backtest.database as bdb
    from pathlib import Path

    # Temporarily point to a temp file
    original = bdb.DB_PATH
    with tempfile.TemporaryDirectory() as tmpdir:
        bdb.DB_PATH = Path(tmpdir) / "backtest.db"
        try:
            conn = bdb.get_connection()
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
            conn.close()

            assert timeout == 5000, f"Expected busy_timeout=5000, got {timeout}"
            assert journal == "wal", f"Expected journal_mode=wal, got {journal}"
        finally:
            bdb.DB_PATH = original


def test_api_server_connect_db_has_busy_timeout():
    """api_server._connect_db() should set PRAGMA busy_timeout."""
    import api_server

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        conn = api_server._connect_db(db_path)
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()

        assert timeout == 5000, f"Expected busy_timeout=5000, got {timeout}"
        assert journal == "wal", f"Expected journal_mode=wal, got {journal}"


def test_layer3_silver_connect_has_busy_timeout():
    """layer3_silver._connect_db() should set PRAGMA busy_timeout."""
    import bot.layer3_silver as l3

    original = l3.DB_FILE
    with tempfile.TemporaryDirectory() as tmpdir:
        l3.DB_FILE = os.path.join(tmpdir, "layer3.db")
        try:
            conn = l3._connect_db()
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
            conn.close()

            assert timeout == 5000, f"Expected busy_timeout=5000, got {timeout}"
            assert journal == "wal", f"Expected journal_mode=wal, got {journal}"
        finally:
            l3.DB_FILE = original
