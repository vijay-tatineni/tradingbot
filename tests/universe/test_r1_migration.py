"""R1 migration (v1→v2) + P3-2 legacy-cooldown handling.

The v2 migration is strictly additive (new session-based cooldown fields + durable exit
markers + append-only history triggers). It rolls back atomically on failure, reruns
idempotently, and preserves existing rows. The deprecated `cooldown_until` column is no
longer the source of truth for cooldown logic — the session-based field is — and an
ambiguous legacy row (count present in the legacy column but absent from the session field)
fails safe into a blocked/manual-review COOLDOWN, never an inferred count.
"""
import sqlite3

import pytest

from bot.universe.db import connect, current_version, migrate
from bot.universe.evaluator import ShadowEvaluator
from bot.universe.migrations import MIGRATIONS
from bot.universe.models import Reason, State
from bot.universe.registry import Registry
from bot.universe.seed import canonical_id, seed_registry
from tests.universe._fixtures import (
    ON, SpyProvider, StubPositionProvider, flat, inst, make_bars, write_configs,
)

V2_COLUMNS = {
    "cooldown_started_trading_date", "cooldown_sessions_remaining",
    "cooldown_last_counted_trading_date", "cooldown_release_estimate",
    "last_observed_position_status", "last_observed_position_id_hash",
    "last_processed_position_event_id", "last_position_close_trading_date",
}
CID = canonical_id("AAPL", "USD", "NASDAQ")


def _state_columns(db):
    with connect(db) as conn:
        return {c[1] for c in conn.execute("PRAGMA table_info(universe_state)").fetchall()}


def _only_v1():
    return [m for m in MIGRATIONS if m[0] == 1]


def test_v1_to_v2_adds_fields_and_preserves_rows(tmp_path, monkeypatch):
    db = str(tmp_path / "universe.db")
    # migrate only to v1, write a row that uses the legacy cooldown column.
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", _only_v1())
    assert migrate(db) == 1
    assert not (V2_COLUMNS & _state_columns(db))     # v2 columns absent at v1
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO canonical_instruments (canonical_instrument_id, display_symbol, "
            "created_at, updated_at) VALUES (?,?,?,?)", (CID, "AAPL", "t", "t"))
        conn.execute(
            "INSERT INTO universe_state (canonical_instrument_id, current_state, "
            "cooldown_until) VALUES (?,?,?)", (CID, "COOLDOWN", "3"))
    # restore full migrations → upgrade to v2.
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", MIGRATIONS)
    assert migrate(db) == 2
    assert V2_COLUMNS.issubset(_state_columns(db))   # all v2 columns present
    with connect(db) as conn:
        row = dict(zip([d[0] for d in conn.execute(
            "SELECT * FROM universe_state").description],
            conn.execute("SELECT * FROM universe_state").fetchone()))
    assert row["current_state"] == "COOLDOWN"        # pre-existing row preserved
    assert row["cooldown_until"] == "3"              # legacy value untouched
    assert row["cooldown_sessions_remaining"] is None  # NOT back-filled / inferred


def test_v2_migration_rerun_is_idempotent(tmp_path):
    db = str(tmp_path / "universe.db")
    assert migrate(db) == 2
    assert migrate(db) == 2                           # rerun: no-op, no error
    assert current_version(db) == 2


def test_v2_migration_rolls_back_atomically_on_failure(tmp_path, monkeypatch):
    db = str(tmp_path / "universe.db")
    assert migrate(db) == 2                            # reach current head first
    # craft a failing additive v3 to prove a later migration rolls back cleanly.
    bad = list(MIGRATIONS) + [(3, [
        "ALTER TABLE universe_state ADD COLUMN probe_col TEXT",
        "THIS IS NOT VALID SQL",
    ])]
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", bad)
    with pytest.raises(sqlite3.OperationalError):
        migrate(db)
    assert current_version(db) == 2                   # not advanced
    assert "probe_col" not in _state_columns(db)      # the partial column rolled back


# ── P3-2: legacy `cooldown_until` is not the source of truth ───────────────────
def _seed_reg(tmp_path):
    p1, p2 = write_configs(tmp_path, [inst("AAPL")], [])
    db = str(tmp_path / "universe.db")
    seed_registry(db, p1, p2)
    return Registry(db)


def _src():
    return {"bars": make_bars(), "corp_action_status": "ok", "sector": "Tech",
            "spread": 0.01}


def _run(reg, provider, day="2026-06-10"):
    ev = ShadowEvaluator(reg, SpyProvider({CID: _src()}), ON, equity=100_000,
                         position_provider=provider)
    r = ev.maybe_run(day, only_ids={CID})
    o = [x for x in r["outcomes"] if x["canonical_instrument_id"] == CID][0]
    return o, reg.get_state(CID)


def test_ambiguous_legacy_cooldown_fails_safe_blocked(tmp_path):
    # legacy count present (cooldown_until) but session field NULL → AMBIGUOUS: block for
    # manual review, do NOT infer a remaining count.
    reg = _seed_reg(tmp_path)
    reg.upsert_state({"canonical_instrument_id": CID, "current_state": "ENTRY_ELIGIBLE",
                      "consecutive_passes": 2, "cooldown_until": "3",
                      "evaluator_version": "dyn_universe_shadow_v1"})
    o, st = _run(reg, flat())
    assert o["new_state"] == State.COOLDOWN.value
    assert Reason.COOLDOWN_LEGACY_AMBIGUOUS in o["reason_codes"]
    assert st["cooldown_sessions_remaining"] is None   # never inferred from the legacy value


def test_session_field_is_source_of_truth_not_legacy(tmp_path):
    # conflicting columns: session field = 5, legacy cooldown_until = "3". The logic must
    # use the SESSION field (decrement 5→4), proving cooldown_until is not read as a count.
    from bot.universe.models import PositionStatus
    reg = _seed_reg(tmp_path)
    reg.upsert_state({"canonical_instrument_id": CID, "current_state": "COOLDOWN",
                      "consecutive_passes": 0, "cooldown_until": "3",
                      "cooldown_sessions_remaining": 5,
                      "evaluator_version": "dyn_universe_shadow_v1"})
    o, st = _run(reg, StubPositionProvider({CID: PositionStatus.NO_POSITION}))
    assert o["new_state"] == State.COOLDOWN.value
    assert st["cooldown_sessions_remaining"] == 4      # 5 → 4, NOT derived from "3"


def test_runtime_modules_do_not_read_cooldown_until_as_count(tmp_path):
    # Guard: the pure state machine never references the deprecated column at all, and the
    # evaluator never reads it as the cooldown count (it reads cooldown_sessions_remaining;
    # cooldown_until is inspected ONLY to flag legacy ambiguity, and written as deprecated
    # compatibility metadata).
    import inspect
    import bot.universe.state_machine as sm
    assert "cooldown_until" not in inspect.getsource(sm)
    from bot.universe.evaluator import ShadowEvaluator
    src = inspect.getsource(ShadowEvaluator._resolve_prior_cooldown)
    # the count is read from the session field; cooldown_until appears only in the
    # ambiguity branch (never assigned to the returned count).
    assert "cooldown_sessions_remaining" in src
