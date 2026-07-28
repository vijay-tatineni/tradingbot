"""The test suite must never write a database into the working tree.

Guards the isolation in conftest.py. Without it, running the suite in a live
tree recreated positions.db / learning_loop.db / backtest.db / regime.db as
empty schemas -- a tree that reads as intact while holding no data.
"""

import sqlite3
from pathlib import Path

import bot.position_tracker as position_tracker
import backtest.database as backtest_database
from tests.conftest import REPO_ROOT


def _is_outside_repo_root(path) -> bool:
    return Path(str(path)).resolve().parent != REPO_ROOT


def test_position_tracker_db_is_redirected_out_of_the_tree():
    assert _is_outside_repo_root(position_tracker.DB_FILE)


def test_backtest_db_is_redirected_out_of_the_tree():
    assert _is_outside_repo_root(backtest_database.DB_PATH)


def test_a_repo_root_database_path_is_rerouted(tmp_path):
    """The safety net catches paths the constant list does not know about."""
    stray = REPO_ROOT / "definitely_not_a_real.db"
    conn = sqlite3.connect(str(stray))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()

    assert not stray.exists(), "a repo-root database was actually created"


def test_memory_and_uri_connections_are_left_alone():
    """The net must not disturb in-memory or read-only URI connections."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.close()

    real = REPO_ROOT / "tests" / "conftest.py"
    uri_conn = sqlite3.connect(f"file:{real}?mode=ro", uri=True)
    uri_conn.close()


def test_position_tracker_writes_outside_the_tree():
    """End-to-end: constructing a tracker leaves no file in the repo root."""
    before = set(REPO_ROOT.glob("*.db"))

    class _Cfg:
        _raw = {"settings": {}}

    position_tracker.PositionTracker(_Cfg())

    assert set(REPO_ROOT.glob("*.db")) == before
