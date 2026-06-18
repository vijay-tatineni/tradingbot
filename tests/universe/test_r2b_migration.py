"""R2B schema v6 migration (P3-4 §12): additive, atomic, idempotent, fail-closed.

v6 adds the `candidates` + `candidate_audit` tables (and indexes) without editing v1–v5.
Legacy v1 `candidate_sources` rows (which carry no verified instrument_uid/listing_uid) are
NEVER R2B-effective — identity is never inferred from a ticker. Temporary DBs only.
"""
import sqlite3

import pytest

from bot.universe.candidate_store import CandidateStore
from bot.universe.db import current_version, migrate
from bot.universe.migrations import MIGRATIONS

V6_TABLES = {"candidates", "candidate_audit"}


def _tables(db):
    con = sqlite3.connect(db)
    rows = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    con.close()
    return rows


def test_fresh_migrate_builds_v6(tmp_path):
    db = str(tmp_path / "universe.db")
    assert migrate(db) == 7
    assert V6_TABLES.issubset(_tables(db))


def test_v5_to_v6_is_additive(tmp_path, monkeypatch):
    db = str(tmp_path / "universe.db")
    v1_v5 = [m for m in MIGRATIONS if m[0] <= 5]
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", v1_v5)
    assert migrate(db) == 5
    assert not (V6_TABLES & _tables(db))           # no v6 tables yet
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", MIGRATIONS)
    assert migrate(db) == 7                         # additive upgrade
    assert V6_TABLES.issubset(_tables(db))
    # v5 identity tables still present
    assert {"instrument_identity", "instrument_listing"}.issubset(_tables(db))


def test_v6_rerun_idempotent(tmp_path):
    db = str(tmp_path / "universe.db")
    assert migrate(db) == 7
    assert migrate(db) == 7
    assert current_version(db) == 7


def test_v6_rolls_back_atomically(tmp_path, monkeypatch):
    db = str(tmp_path / "universe.db")
    v1_v5 = [m for m in MIGRATIONS if m[0] <= 5]
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", v1_v5)
    migrate(db)
    broken = v1_v5 + [(6, ["CREATE TABLE candidates (x INTEGER)", "BAD SQL HERE ("])]
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", broken)
    with pytest.raises(sqlite3.OperationalError):
        migrate(db)
    assert current_version(db) == 5
    assert "candidates" not in _tables(db)


def test_v1_v5_migration_definitions_unedited():
    assert [m[0] for m in MIGRATIONS] == [1, 2, 3, 4, 5, 6, 7]


def test_legacy_candidate_sources_row_never_r2b_effective(tmp_path):
    # A legacy v1 candidate_sources row (canonical_instrument_id only, no verified
    # instrument_uid/listing_uid) must never become an R2B effective candidate.
    db = str(tmp_path / "universe.db")
    migrate(db)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO canonical_instruments (canonical_instrument_id, display_symbol, "
                "created_at, updated_at) VALUES ('US_AAPL','AAPL','t','t')")
    con.execute("INSERT INTO candidate_sources (candidate_id, canonical_instrument_id, source, "
                "added_at, active, created_at, updated_at) "
                "VALUES ('legacy1','US_AAPL','MANUAL','t',1,'t','t')")
    con.commit(); con.close()
    cs = CandidateStore(db)
    # the R2B `candidates` table is empty; no effective candidate exists.
    assert cs.effective_candidates("2026-06-12").effective == {}
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
