"""universe.db connection + additive PRAGMA user_version migration framework.

Idempotent: ``migrate(db_path)`` applies only migrations newer than the DB's
current ``user_version`` and is safe to rerun (a fully-migrated DB is a no-op).

This DB is the Dynamic Universe research/shadow store. It is deliberately separate
from regime.db and backtest.db; operational execution state is never merged in.
"""
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from bot.universe.migrations import MIGRATIONS

logger = logging.getLogger("universe.db")

# Repo root = three parents up from this file (bot/universe/db.py).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def default_db_path() -> str:
    """Default universe.db location (gitignored). Callers may override."""
    return str(REPO_ROOT / "universe.db")


@contextmanager
def connect(db_path: str) -> Iterator[sqlite3.Connection]:
    """Connection context manager (foreign keys on; commit on clean exit)."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def current_version(db_path: str) -> int:
    with connect(db_path) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(db_path: str, timeout: float = 5.0) -> int:
    """Apply pending additive migrations atomically. Returns the resulting user_version.

    Each migration is applied inside one explicit ``BEGIN IMMEDIATE`` transaction:
    every schema/index statement runs, then ``PRAGMA user_version`` is bumped, then
    ``COMMIT``. On any error the transaction is rolled back (leaving NO partial schema
    and ``user_version`` unchanged) and the original exception is re-raised. This is
    genuine all-or-nothing atomicity — verified empirically (SQLite DDL *and*
    ``PRAGMA user_version`` are transactional and roll back together; see
    tests/universe/test_migrations.py).

    Implementation notes (why this is actually atomic, unlike the previous version):
      * ``isolation_level = None`` puts the driver in autocommit mode so it issues NO
        implicit BEGIN/COMMIT around our statements — WE own the transaction boundary.
      * Each DDL statement is executed individually via ``conn.execute`` so it stays
        INSIDE our ``BEGIN IMMEDIATE``. We deliberately do NOT use
        ``executescript()``, which forces an implicit COMMIT and would defeat the
        explicit transaction boundary.
      * ``BEGIN IMMEDIATE`` takes the write lock up front, so two concurrent migration
        attempts cannot both proceed: the second blocks up to ``timeout`` then raises
        ``sqlite3.OperationalError`` (database is locked) without creating any partial
        schema. ``timeout`` is forwarded to ``sqlite3.connect`` (default 5s).

    Safe to rerun: a fully-migrated DB applies nothing (a no-op).
    """
    conn = sqlite3.connect(db_path, timeout=timeout)
    # Explicit transaction control: the driver must NOT inject implicit BEGIN/COMMIT.
    conn.isolation_level = None
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        applied = 0
        for target, statements in sorted(MIGRATIONS, key=lambda m: m[0]):
            if target <= version:
                continue
            conn.execute("BEGIN IMMEDIATE")
            try:
                for stmt in statements:
                    conn.execute(stmt)
                # PRAGMA user_version cannot be parameterised; target is an int literal
                # we control (never user input). It IS transactional and rolls back
                # with the DDL above if the COMMIT is never reached.
                conn.execute(f"PRAGMA user_version = {int(target)}")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            version = target
            applied += 1
        if applied:
            logger.info("universe.db migrated to user_version=%d (%d applied)",
                        version, applied)
        return version
    finally:
        conn.close()
