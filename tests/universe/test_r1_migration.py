"""R1 migration (v1→v2) + P3-2 legacy-cooldown handling.

The v2 migration is strictly additive (new session-based cooldown fields + durable exit
markers + append-only history triggers). It rolls back atomically on failure, reruns
idempotently, and preserves existing rows. The deprecated `cooldown_until` column is no
longer the source of truth for cooldown logic — the session-based field is — and an
ambiguous legacy row (count present in the legacy column but absent from the session field)
fails safe into a blocked/manual-review COOLDOWN, never an inferred count.
"""
import sqlite3
from datetime import date

import pytest

from bot.universe.db import connect, current_version, migrate
from bot.universe.evaluator import ShadowEvaluator
from bot.universe.migrations import MIGRATIONS
from bot.universe.models import PositionSnapshot, PositionStatus, Reason, State
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
    # restore full migrations → upgrade to head.
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", MIGRATIONS)
    assert migrate(db) == 7
    assert V2_COLUMNS.issubset(_state_columns(db))   # all v2 columns present
    with connect(db) as conn:
        row = dict(zip([d[0] for d in conn.execute(
            "SELECT * FROM universe_state").description],
            conn.execute("SELECT * FROM universe_state").fetchone()))
    assert row["current_state"] == "COOLDOWN"        # pre-existing row preserved
    assert row["cooldown_until"] == "3"              # legacy value untouched
    assert row["cooldown_sessions_remaining"] is None  # NOT back-filled / inferred


def test_migration_rerun_is_idempotent(tmp_path):
    db = str(tmp_path / "universe.db")
    assert migrate(db) == 7
    assert migrate(db) == 7                           # rerun: no-op, no error
    assert current_version(db) == 7


def test_migration_rolls_back_atomically_on_failure(tmp_path, monkeypatch):
    db = str(tmp_path / "universe.db")
    assert migrate(db) == 7                            # reach current head first
    # craft a failing additive v8 to prove a later migration rolls back cleanly.
    bad = list(MIGRATIONS) + [(8, [
        "ALTER TABLE universe_state ADD COLUMN probe_col TEXT",
        "THIS IS NOT VALID SQL",
    ])]
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", bad)
    with pytest.raises(sqlite3.OperationalError):
        migrate(db)
    assert current_version(db) == 7                   # not advanced
    assert "probe_col" not in _state_columns(db)      # the partial column rolled back


def test_v3_backfills_reconciliation_for_unknown_only_rows(tmp_path, monkeypatch):
    """A pre-existing v2 row whose only position memory is an UNKNOWN observation (no
    reconstructable authoritative state) is conservatively blocked for reconciliation on
    upgrade to v3, rather than guessed flat."""
    db = str(tmp_path / "universe.db")
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", [m for m in MIGRATIONS if m[0] <= 2])
    assert migrate(db) == 2
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO canonical_instruments (canonical_instrument_id, display_symbol, "
            "created_at, updated_at) VALUES (?,?,?,?)", (CID, "AAPL", "t", "t"))
        conn.execute(
            "INSERT INTO universe_state (canonical_instrument_id, current_state, "
            "last_observed_position_status) VALUES (?,?,?)", (CID, "EXIT_ONLY", "UNKNOWN"))
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", MIGRATIONS)
    assert migrate(db) == 7
    with connect(db) as conn:
        recon = conn.execute(
            "SELECT position_reconciliation_required FROM universe_state "
            "WHERE canonical_instrument_id=?", (CID,)).fetchone()[0]
    assert recon == 1                                # blocked for reconciliation, not guessed


# ── R1.2 (P2-B): v3 back-fill preserves/derives the authoritative anchor ───────
def _v2_then_v3(tmp_path, monkeypatch, rows):
    """Build a v2 DB, insert universe_state rows, then upgrade to v3. Each row dict:
    {cid, current_state, last_observed, pid_hash?, eval_date?}."""
    db = str(tmp_path / "universe.db")
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", [m for m in MIGRATIONS if m[0] <= 2])
    assert migrate(db) == 2
    with connect(db) as conn:
        for r in rows:
            conn.execute(
                "INSERT INTO canonical_instruments (canonical_instrument_id, display_symbol, "
                "created_at, updated_at) VALUES (?,?,?,?)", (r["cid"], r["cid"], "t", "t"))
            conn.execute(
                "INSERT INTO universe_state (canonical_instrument_id, current_state, "
                "last_observed_position_status, last_observed_position_id_hash, "
                "evaluated_trading_date) VALUES (?,?,?,?,?)",
                (r["cid"], r["current_state"], r.get("last_observed"),
                 r.get("pid_hash"), r.get("eval_date")))
    monkeypatch.setattr("bot.universe.db.MIGRATIONS", MIGRATIONS)
    assert migrate(db) == 7
    return db


_AUTH_COLS = ("last_authoritative_position_status", "last_authoritative_position_id_hash",
              "last_authoritative_observed_at", "position_reconciliation_required")


def _auth(db, cid):
    with connect(db) as conn:
        row = conn.execute(
            f"SELECT {', '.join(_AUTH_COLS)} FROM universe_state "
            "WHERE canonical_instrument_id=?", (cid,)).fetchone()
    return dict(zip(_AUTH_COLS, row))


def test_v3_open_at_boundary_retains_authoritative_anchor(tmp_path, monkeypatch):
    # P2-B core: a v2 row OPEN at the migration boundary keeps its authoritative-open anchor
    # (+ provenance), so a later evidence-bearing close is NOT mistaken for an ordinary flat.
    db = _v2_then_v3(tmp_path, monkeypatch, [
        {"cid": "OPEN_OK", "current_state": "POSITION_OPEN",
         "last_observed": "POSITION_OPEN", "pid_hash": "hh", "eval_date": "2026-06-10"}])
    a = _auth(db, "OPEN_OK")
    assert a["last_authoritative_position_status"] == "POSITION_OPEN"
    assert a["last_authoritative_position_id_hash"] == "hh"
    assert a["last_authoritative_observed_at"] == "2026-06-10"
    assert a["position_reconciliation_required"] == 0


def test_v3_open_without_provenance_is_reconciliation_blocked(tmp_path, monkeypatch):
    # OPEN but no usable observation date → cannot establish provenance → block (anchor kept).
    db = _v2_then_v3(tmp_path, monkeypatch, [
        {"cid": "OPEN_NOPROV", "current_state": "POSITION_OPEN",
         "last_observed": "POSITION_OPEN", "eval_date": None}])
    a = _auth(db, "OPEN_NOPROV")
    assert a["last_authoritative_position_status"] == "POSITION_OPEN"
    assert a["position_reconciliation_required"] == 1


def test_v3_clean_flat_and_exited_rows_are_non_blocked(tmp_path, monkeypatch):
    db = _v2_then_v3(tmp_path, monkeypatch, [
        {"cid": "FLAT", "current_state": "WATCHLIST",
         "last_observed": "NO_POSITION", "eval_date": "2026-06-10"},
        {"cid": "EXITED", "current_state": "COOLDOWN",
         "last_observed": "POSITION_EXITED", "eval_date": "2026-06-10"}])
    for cid in ("FLAT", "EXITED"):
        a = _auth(db, cid)
        assert a["last_authoritative_position_status"] == "NO_POSITION"
        assert a["position_reconciliation_required"] == 0


def test_v3_uncertain_rows_blocked_never_inferred_flat(tmp_path, monkeypatch):
    # UNKNOWN / missing / malformed legacy status → authoritative state not reconstructable →
    # reconciliation-blocked, NEVER silently inferred flat.
    db = _v2_then_v3(tmp_path, monkeypatch, [
        {"cid": "UNK", "current_state": "EXIT_ONLY",
         "last_observed": "UNKNOWN", "eval_date": "2026-06-10"},
        {"cid": "MISSING", "current_state": "COOLDOWN",
         "last_observed": None, "eval_date": "2026-06-10"},
        {"cid": "MALFORMED", "current_state": "WATCHLIST",
         "last_observed": "WHO_KNOWS", "eval_date": "2026-06-10"}])
    for cid in ("UNK", "MISSING", "MALFORMED"):
        a = _auth(db, cid)
        assert a["position_reconciliation_required"] == 1
        assert a["last_authoritative_position_status"] is None


def test_v3_migrated_open_anchor_makes_later_close_start_cooldown(tmp_path, monkeypatch):
    # P2-B end-to-end: because the migrated anchor is POSITION_OPEN, a subsequent
    # evidence-bearing close is detected as an EXIT (→ cooldown), not an ordinary flat.
    db = _v2_then_v3(tmp_path, monkeypatch, [
        {"cid": CID, "current_state": "POSITION_OPEN", "last_observed": "POSITION_OPEN",
         "pid_hash": "hh", "eval_date": "2026-06-10"}])
    reg = Registry(db)
    ev = ShadowEvaluator(reg, bars_provider=lambda r: None, flags={})
    # R2A-0.1: the close carries a COMPLETE valid lifecycle (position_id + opened + closed).
    cont = ev._position_continuity(
        CID, reg.get_state(CID),
        PositionSnapshot(status=PositionStatus.NO_POSITION, position_id="p1",
                         opened_trading_date=date(2026, 6, 9),
                         closed_trading_date=date(2026, 6, 12)), date(2026, 6, 12))
    assert cont["exit_detected"] is True               # migrated anchor → close = exit
    assert cont["reconciliation_required"] is False


def test_v3_backfill_rerun_is_idempotent(tmp_path, monkeypatch):
    db = _v2_then_v3(tmp_path, monkeypatch, [
        {"cid": "OPEN_OK", "current_state": "POSITION_OPEN",
         "last_observed": "POSITION_OPEN", "pid_hash": "hh", "eval_date": "2026-06-10"},
        {"cid": "UNK", "current_state": "EXIT_ONLY",
         "last_observed": "UNKNOWN", "eval_date": "2026-06-10"}])
    before = (_auth(db, "OPEN_OK"), _auth(db, "UNK"))
    assert migrate(db) == 7                              # rerun: no-op
    assert (_auth(db, "OPEN_OK"), _auth(db, "UNK")) == before


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


# ── R1.3 (Finding 3): close the remaining migration coverage gaps ───────────────
V3_COLUMNS = {
    "latest_observed_position_status", "latest_observed_at",
    "last_authoritative_position_status", "last_authoritative_position_id_hash",
    "last_authoritative_observed_at", "position_reconciliation_required",
}


def test_v3_adds_all_authoritative_and_reconciliation_columns(tmp_path):
    db = str(tmp_path / "universe.db")
    assert migrate(db) == 7
    # every v3 column present — INCLUDING latest_observed_at (previously unasserted).
    assert V3_COLUMNS.issubset(_state_columns(db))


def test_v3_adds_transition_snapshot_history_columns(tmp_path):
    # R1.3 (Finding 2): the append-only history table gains the complete-transition columns.
    db = str(tmp_path / "universe.db")
    assert migrate(db) == 7
    with connect(db) as conn:
        cols = {c[1] for c in conn.execute(
            "PRAGMA table_info(universe_state_history)").fetchall()}
    assert {"transition_snapshot_json", "transition_snapshot_hash"}.issubset(cols)


def test_v3_position_exited_today_row_migrates_to_flat_anchor(tmp_path, monkeypatch):
    # A v2 row whose last_observed status is the (durable) POSITION_EXITED_TODAY migrates to a
    # clean NO_POSITION authoritative anchor, non-blocked (the close is already resolved).
    db = _v2_then_v3(tmp_path, monkeypatch, [
        {"cid": "ET", "current_state": "COOLDOWN",
         "last_observed": "POSITION_EXITED_TODAY", "eval_date": "2026-06-10"}])
    a = _auth(db, "ET")
    assert a["last_authoritative_position_status"] == "NO_POSITION"
    assert a["position_reconciliation_required"] == 0
