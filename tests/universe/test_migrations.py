"""universe.db migration framework: idempotency + schema presence + ATOMICITY (P2-1).

Atomicity tests prove the explicit ``BEGIN IMMEDIATE`` transaction in db.migrate is
genuine all-or-nothing: an injected mid-migration failure rolls back every object AND
leaves ``user_version`` unchanged, a clean retry then fully migrates, and a re-run is a
safe no-op. A concurrent migration cannot corrupt the schema. Temporary DBs only — no
production database is ever touched."""
import sqlite3

import pytest

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


# Current schema head: v2 (R1: session-based cooldown, durable exit markers, append-only
# history triggers) + v3 (R1.1: authoritative-continuity fields + reconciliation block).
HEAD_VERSION = 3


def test_migrate_creates_all_tables(tmp_path):
    db = str(tmp_path / "universe.db")
    assert migrate(db) == HEAD_VERSION
    assert EXPECTED_TABLES.issubset(_tables(db))


def test_migrate_is_idempotent(tmp_path):
    db = str(tmp_path / "universe.db")
    assert migrate(db) == HEAD_VERSION
    assert migrate(db) == HEAD_VERSION   # rerun: no error, same version
    assert current_version(db) == HEAD_VERSION
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


# ── P2-1: explicit-transaction atomicity ──────────────────────────────────────

def _objects(db):
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','index')").fetchall()
    con.close()
    return {r[0] for r in rows}


def test_migration_rolls_back_on_injected_failure(tmp_path, monkeypatch):
    """Inject a failure BETWEEN schema statements: the partially-created object must be
    rolled back and user_version must stay unchanged (genuine atomicity)."""
    db = str(tmp_path / "universe.db")
    bad = [(1, [
        "CREATE TABLE probe_a (x INTEGER)",        # succeeds inside the tx
        "CREATE TABLE probe_b (BAD SYNTAX (",      # fails → triggers ROLLBACK
    ])]
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", bad)
    with pytest.raises(sqlite3.OperationalError):
        migrate(db)
    # 1+2+3: rollback removed EVERY object the failed migration created.
    objs = _objects(db)
    assert "probe_a" not in objs and "probe_b" not in objs
    # 4: user_version is unchanged.
    assert current_version(db) == 0
    # connection is not left in a broken transaction: a fresh write works.
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE sanity (x)")
    con.commit(); con.close()

    # 5+6: clean retry (no injected failure) fully succeeds.
    good = [(1, [
        "CREATE TABLE probe_a (x INTEGER)",
        "CREATE TABLE probe_b (y INTEGER)",
        "CREATE INDEX ix_probe ON probe_a (x)",
    ])]
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", good)
    assert migrate(db) == 1
    objs = _objects(db)
    assert {"probe_a", "probe_b", "ix_probe"}.issubset(objs)
    assert current_version(db) == 1
    # 7: rerun is a safe no-op (idempotent).
    assert migrate(db) == 1
    assert current_version(db) == 1


def test_user_version_not_bumped_when_a_later_statement_fails(tmp_path, monkeypatch):
    """The version bump happens only AFTER every statement succeeds — a failure on the
    LAST schema statement must still leave user_version at the pre-migration value."""
    db = str(tmp_path / "universe.db")
    migrate(db)                       # reach the current head with the real schema
    assert current_version(db) == HEAD_VERSION
    bad_next = [(HEAD_VERSION + 1, [
        "CREATE TABLE vnext_added (x INTEGER)",
        "THIS IS NOT VALID SQL",       # last statement fails
    ])]
    # keep the real migrations + an additive failing next version
    from bot.universe.migrations import MIGRATIONS as REAL
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", list(REAL) + bad_next)
    with pytest.raises(sqlite3.OperationalError):
        migrate(db)
    assert current_version(db) == HEAD_VERSION    # NOT advanced
    assert "vnext_added" not in _objects(db)      # its table rolled back
    assert EXPECTED_TABLES.issubset(_objects(db))  # real schema intact


def test_concurrent_migration_does_not_corrupt_schema(tmp_path):
    """One writer holds the lock; a concurrent migrate fails fast (database is locked)
    without leaving a partial schema. After the lock releases, a retry fully migrates."""
    db = str(tmp_path / "universe.db")
    blocker = sqlite3.connect(db, timeout=0.1)
    blocker.isolation_level = None
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("CREATE TABLE _hold (x)")     # take the write (RESERVED) lock
    try:
        with pytest.raises(sqlite3.OperationalError):
            migrate(db, timeout=0.2)              # cannot acquire BEGIN IMMEDIATE
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    # The blocked attempt created none of the migration objects (it never began).
    assert not (EXPECTED_TABLES & _objects(db))
    assert current_version(db) == 0
    # Lock released → a clean retry fully and atomically migrates.
    assert migrate(db) == HEAD_VERSION
    assert EXPECTED_TABLES.issubset(_objects(db))
    assert current_version(db) == HEAD_VERSION


def test_migrate_uses_no_implicit_commit_executescript(tmp_path):
    """Guard: db.migrate must not regress to executescript() (which forces an implicit
    COMMIT and would defeat the explicit transaction boundary)."""
    import ast
    import inspect
    from bot.universe import db as dbmod
    tree = ast.parse(inspect.getsource(dbmod.migrate))
    calls = [n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert "executescript" not in calls   # never an implicit-commit bulk exec
