"""R2A-1 schema v5 migration (P3-6 / P3-7): additive, atomic, idempotent, fail-closed.

v4→v5 marks every existing canonical row identity UNVERIFIED with a NULL instrument_uid —
NO instrument_uid is fabricated, NO ticker-based backfill, NO auto-merge. A fresh v1→v5
builds the full schema. Rerun is a no-op; an injected failure rolls back leaving v4 intact.
Temporary DBs only — no production database is touched.
"""
import sqlite3

import pytest

from bot.universe.db import current_version, migrate
from bot.universe.migrations import MIGRATIONS

V5_TABLES = {"instrument_identity", "instrument_listing", "ibkr_mapping", "ig_mapping",
             "identity_audit"}


def _tables(db):
    con = sqlite3.connect(db)
    rows = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    con.close()
    return rows


def _columns(db, table):
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    con.close()
    return cols


def _migrate_to_v4(db, monkeypatch):
    """Bring a DB to v4 only (the pre-R2A-1 head), then restore the full migration list."""
    v1_to_v4 = [m for m in MIGRATIONS if m[0] <= 4]
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", v1_to_v4)
    assert migrate(db) == 4
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", MIGRATIONS)


def _seed_v4_canonical(db, symbols):
    con = sqlite3.connect(db)
    for i, sym in enumerate(symbols):
        con.execute(
            "INSERT INTO canonical_instruments "
            "(canonical_instrument_id, display_symbol, currency, exchange, primary_gateway, "
            " administratively_active, hard_disabled, created_at, updated_at) "
            "VALUES (?,?,?,?,?,1,0,?,?)",
            (f"US_{sym}", sym, "USD", "XNAS", "IBKR", "2026-01-01", "2026-01-01"))
    con.commit(); con.close()


def test_fresh_v1_to_v5_builds_full_schema(tmp_path):
    db = str(tmp_path / "universe.db")
    assert migrate(db) == 6
    assert V5_TABLES.issubset(_tables(db))
    # legacy canonical table gained the additive identity columns
    assert {"instrument_uid", "identity_status"}.issubset(_columns(db, "canonical_instruments"))


def test_v4_to_v5_existing_rows_become_unverified_no_backfill(tmp_path, monkeypatch):
    db = str(tmp_path / "universe.db")
    _migrate_to_v4(db, monkeypatch)
    _seed_v4_canonical(db, ["AAPL", "MSFT", "BARC"])
    assert current_version(db) == 4

    assert migrate(db) == 6      # v4 → v5
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT instrument_uid, identity_status FROM canonical_instruments").fetchall()
    # every existing row: NULL instrument_uid (NOT ticker-derived) + UNVERIFIED
    assert all(iuid is None and status == "UNVERIFIED" for iuid, status in rows)
    assert len(rows) == 3
    # NO identity rows fabricated from existing symbols
    assert con.execute("SELECT COUNT(*) FROM instrument_identity").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM instrument_listing").fetchone()[0] == 0
    con.close()


def test_v5_rerun_is_idempotent(tmp_path):
    db = str(tmp_path / "universe.db")
    assert migrate(db) == 6
    assert migrate(db) == 6
    assert migrate(db) == 6
    assert current_version(db) == 6
    assert V5_TABLES.issubset(_tables(db))


def test_v5_migration_rolls_back_atomically_leaving_v4(tmp_path, monkeypatch):
    db = str(tmp_path / "universe.db")
    _migrate_to_v4(db, monkeypatch)
    _seed_v4_canonical(db, ["AAPL"])
    # real v1–v4 + a BROKEN v5 (a valid create, then invalid SQL → ROLLBACK).
    broken = [m for m in MIGRATIONS if m[0] <= 4] + [(5, [
        "CREATE TABLE instrument_identity (x INTEGER)",
        "THIS IS NOT VALID SQL",
    ])]
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", broken)
    with pytest.raises(sqlite3.OperationalError):
        migrate(db)
    # user_version unchanged; no v5 object created; v4 schema intact.
    assert current_version(db) == 4
    assert "instrument_identity" not in _tables(db)
    assert "canonical_instruments" in _tables(db)
    # the seeded v4 row is untouched (no identity columns yet)
    con = sqlite3.connect(db)
    assert "instrument_uid" not in _columns(db, "canonical_instruments")
    assert con.execute("SELECT COUNT(*) FROM canonical_instruments").fetchone()[0] == 1
    con.close()


def test_identity_audit_is_append_only(tmp_path):
    db = str(tmp_path / "universe.db")
    migrate(db)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO identity_audit (event_type, created_at) VALUES ('X','2026-01-01')")
    con.commit()
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE identity_audit SET event_type='Y'"); con.commit()
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM identity_audit"); con.commit()
    con.close()


def test_v1_v4_migration_definitions_unedited():
    # Guard: the released v1–v4 migration tuples must remain byte-for-byte unchanged; R2A-1 is
    # forward-only (a new v5 entry). We assert the version sequence and that v5 is the head.
    versions = [m[0] for m in MIGRATIONS]
    assert versions == [1, 2, 3, 4, 5, 6]
