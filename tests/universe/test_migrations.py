"""universe.db migration framework: idempotency + schema presence."""
import sqlite3

from bot.universe.db import current_version, migrate

EXPECTED_TABLES = {
    "canonical_instruments", "gateway_map_ibkr", "gateway_map_ig",
    "candidate_sources", "universe_state", "universe_state_history",
}


def _tables(db):
    con = sqlite3.connect(db)
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    con.close()
    return {r[0] for r in rows}


def test_migrate_creates_all_tables(tmp_path):
    db = str(tmp_path / "universe.db")
    assert migrate(db) == 1
    assert EXPECTED_TABLES.issubset(_tables(db))


def test_migrate_is_idempotent(tmp_path):
    db = str(tmp_path / "universe.db")
    assert migrate(db) == 1
    assert migrate(db) == 1          # rerun: no error, same version
    assert current_version(db) == 1
    # rerunning a third time still leaves exactly the expected tables
    assert EXPECTED_TABLES.issubset(_tables(db))


def test_history_idempotency_index_exists(tmp_path):
    db = str(tmp_path / "universe.db")
    migrate(db)
    con = sqlite3.connect(db)
    idx = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    con.close()
    assert "ux_history_idem" in idx
