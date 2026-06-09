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


def migrate(db_path: str) -> int:
    """Apply pending additive migrations. Returns the resulting user_version.

    Safe to rerun. Each migration runs in a single transaction; user_version is
    bumped only after its statements succeed.
    """
    with connect(db_path) as conn:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        applied = 0
        for target, statements in sorted(MIGRATIONS, key=lambda m: m[0]):
            if target <= version:
                continue
            for stmt in statements:
                conn.execute(stmt)
            # PRAGMA user_version cannot be parameterised; target is an int literal
            # we control (never user input).
            conn.execute(f"PRAGMA user_version = {int(target)}")
            version = target
            applied += 1
        if applied:
            logger.info("universe.db migrated to user_version=%d (%d applied)",
                        version, applied)
    return version
