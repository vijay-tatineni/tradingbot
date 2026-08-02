"""P3-3 — current-state + history persistence is ATOMIC.

`Registry.persist_transition_atomic` writes the mutable `universe_state` row and the
append-only `universe_state_history` row inside ONE explicit BEGIN IMMEDIATE transaction:
they commit together or roll back together. The history row is written FIRST so its UNIQUE
idempotency key gates duplicates (a replay is a no-op that touches neither table), and a
fault injected at any seam rolls BOTH writes back, leaving the user-visible state at its
prior value and the connection usable. Temporary DBs only — no production DB is touched.
"""
import sqlite3

import pytest

from bot.universe.db import connect, migrate
from bot.universe.registry import (
    Registry, StateHistoryConsistencyError, TransitionConflictError,
)

CID = "US_AAPL"
VER = "dyn_universe_shadow_v1"


def _reg(tmp_path):
    db = str(tmp_path / "universe.db")
    migrate(db)
    reg = Registry(db)
    # FK target for universe_state.
    reg.upsert_canonical({"canonical_instrument_id": CID, "display_symbol": "AAPL"})
    return reg, db


def _state(state="ENTRY_ELIGIBLE", remaining=0, **extra):
    rec = {"canonical_instrument_id": CID, "current_state": state,
           "consecutive_passes": 2, "consecutive_failures": 0,
           "cooldown_sessions_remaining": remaining,
           "evaluated_trading_date": "2026-06-10",
           "evaluator_version": VER}
    rec.update(extra)
    return rec


def _history(date="2026-06-10", new_state="ENTRY_ELIGIBLE", **extra):
    rec = {"canonical_instrument_id": CID, "trading_date": date,
           "prior_state": "WATCHLIST", "new_state": new_state,
           "reason_codes": ["x"], "feature_snapshot": {"k": 1},
           "feature_snapshot_hash": "h", "evaluator_version": VER}
    rec.update(extra)
    return rec


def _counts(db):
    with connect(db) as conn:
        s = conn.execute("SELECT COUNT(*) FROM universe_state").fetchone()[0]
        h = conn.execute("SELECT COUNT(*) FROM universe_state_history").fetchone()[0]
    return s, h


def _state_value(db):
    with connect(db) as conn:
        row = conn.execute(
            "SELECT current_state FROM universe_state WHERE canonical_instrument_id=?",
            (CID,)).fetchone()
    return row[0] if row else None


# ── happy path ────────────────────────────────────────────────────────────────
def test_persist_writes_both_and_returns_true(tmp_path):
    reg, db = _reg(tmp_path)
    assert reg.persist_transition_atomic(_state(), _history()) is True
    assert _counts(db) == (1, 1)             # exactly one of each
    assert _state_value(db) == "ENTRY_ELIGIBLE"


def test_exactly_one_history_row_after_success(tmp_path):
    reg, db = _reg(tmp_path)
    reg.persist_transition_atomic(_state(), _history())
    assert _counts(db)[1] == 1


# ── fault injection: a failure at ANY seam rolls BOTH writes back ───────────────
@pytest.mark.parametrize("seam", ["after_history", "after_state", "at_commit"])
def test_fault_at_each_seam_rolls_back_both(tmp_path, seam):
    reg, db = _reg(tmp_path)
    # establish a prior committed state so we can prove it is preserved on rollback.
    reg.persist_transition_atomic(_state(state="WATCHLIST"),
                                  _history(date="2026-06-09", new_state="WATCHLIST"))
    s0, h0 = _counts(db)
    assert (s0, h0) == (1, 1) and _state_value(db) == "WATCHLIST"

    def hook(s):
        if s == seam:
            raise RuntimeError(f"injected at {s}")

    with pytest.raises(RuntimeError):
        reg.persist_transition_atomic(
            _state(state="ENTRY_ELIGIBLE"), _history(), _fault_hook=hook)

    # both tables unchanged; user-visible state remains the PRIOR state.
    assert _counts(db) == (s0, h0)
    assert _state_value(db) == "WATCHLIST"
    # connection pool is not wedged: a clean write still works afterwards.
    assert reg.persist_transition_atomic(
        _state(state="ENTRY_ELIGIBLE"), _history()) is True
    assert _state_value(db) == "ENTRY_ELIGIBLE"


def test_state_write_failure_rolls_back_history(tmp_path):
    # history is written first; a fault before the state write commits must remove the
    # already-inserted history row too (no orphan history).
    reg, db = _reg(tmp_path)

    def hook(s):
        if s == "after_history":
            raise RuntimeError("state write would fail here")

    with pytest.raises(RuntimeError):
        reg.persist_transition_atomic(_state(), _history(), _fault_hook=hook)
    assert _counts(db) == (0, 0)             # neither table advanced


def test_retry_after_rollback_succeeds(tmp_path):
    reg, db = _reg(tmp_path)

    def hook(s):
        if s == "at_commit":
            raise RuntimeError("commit failed")

    with pytest.raises(RuntimeError):
        reg.persist_transition_atomic(_state(), _history(), _fault_hook=hook)
    assert _counts(db) == (0, 0)
    # same transition, clean retry → succeeds, exactly one row each.
    assert reg.persist_transition_atomic(_state(), _history()) is True
    assert _counts(db) == (1, 1)


# ── content-aware idempotency (R1.1 §6) ────────────────────────────────────────
def test_identical_replay_is_idempotent_no_op(tmp_path):
    reg, db = _reg(tmp_path)
    assert reg.persist_transition_atomic(
        _state(state="COOLDOWN", remaining=2), _history(new_state="COOLDOWN")) is True
    # byte-identical replay of the SAME transition → idempotent no-op (no duplicate row).
    assert reg.persist_transition_atomic(
        _state(state="COOLDOWN", remaining=2), _history(new_state="COOLDOWN")) is False
    assert _counts(db) == (1, 1)


def test_conflicting_cooldown_result_raises_not_silent_no_op(tmp_path):
    # R1.1: the same key with a DIFFERENT cooldown result is a conflict (the R1 silent
    # no-op was Finding 3) — fail closed and leave the prior state untouched.
    reg, db = _reg(tmp_path)
    reg.persist_transition_atomic(
        _state(state="COOLDOWN", remaining=2), _history(new_state="COOLDOWN"))
    with pytest.raises(TransitionConflictError):
        reg.persist_transition_atomic(
            _state(state="COOLDOWN", remaining=1), _history(new_state="COOLDOWN"))
    with connect(db) as conn:
        remaining = conn.execute(
            "SELECT cooldown_sessions_remaining FROM universe_state WHERE canonical_instrument_id=?",
            (CID,)).fetchone()[0]
    assert remaining == 2 and _counts(db) == (1, 1)   # untouched, no double-advance


def test_conflicting_new_state_raises(tmp_path):
    reg, db = _reg(tmp_path)
    reg.persist_transition_atomic(_state(state="WATCHLIST"), _history(new_state="WATCHLIST"))
    before = _state_value(db)
    with pytest.raises(TransitionConflictError):
        reg.persist_transition_atomic(
            _state(state="ENTRY_ELIGIBLE"), _history(new_state="ENTRY_ELIGIBLE"))
    assert _state_value(db) == before        # unchanged (still WATCHLIST)
    assert _counts(db) == (1, 1)


def test_conflicting_snapshot_hash_raises(tmp_path):
    reg, db = _reg(tmp_path)
    reg.persist_transition_atomic(
        _state(), _history(feature_snapshot_hash="HASH_A"))
    with pytest.raises(TransitionConflictError):
        reg.persist_transition_atomic(
            _state(), _history(feature_snapshot_hash="HASH_B"))


def test_conflicting_close_event_raises(tmp_path):
    reg, db = _reg(tmp_path)
    reg.persist_transition_atomic(
        _state(last_processed_position_event_id="ev-1"), _history())
    with pytest.raises(TransitionConflictError):
        reg.persist_transition_atomic(
            _state(last_processed_position_event_id="ev-2"), _history())


def test_history_without_current_state_raises_consistency(tmp_path):
    # a history row with NO matching current-state row is a broken pair → consistency error.
    reg, db = _reg(tmp_path)
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO universe_state_history (canonical_instrument_id, trading_date, "
            "new_state, evaluator_version, created_at) VALUES (?,?,?,?,?)",
            (CID, "2026-06-10", "WATCHLIST", VER, "t"))
    with pytest.raises(StateHistoryConsistencyError):
        reg.persist_transition_atomic(_state(state="WATCHLIST"), _history(new_state="WATCHLIST"))


def test_state_history_divergence_raises_consistency(tmp_path):
    # history.new_state and the current-state row disagree for the same date → consistency.
    reg, db = _reg(tmp_path)
    reg.persist_transition_atomic(_state(state="WATCHLIST"), _history(new_state="WATCHLIST"))
    # corrupt the current-state row to disagree with its history (bypassing append-only).
    with connect(db) as conn:
        conn.execute(
            "UPDATE universe_state SET current_state='ENTRY_ELIGIBLE' WHERE canonical_instrument_id=?",
            (CID,))
    with pytest.raises(StateHistoryConsistencyError):
        reg.persist_transition_atomic(_state(state="WATCHLIST"), _history(new_state="WATCHLIST"))


# ── R1.2 (P2-A): historical replay after the current state legitimately advanced ──
def test_exact_replay_after_state_advanced_is_idempotent_no_op(tmp_path):
    # Day D persists; D+1 and D+2 advance the current-state row; then the EXACT Day-D
    # transition is replayed → idempotent no-op (NOT a consistency error). The current-state
    # row is allowed to represent a legitimately later trading date.
    reg, db = _reg(tmp_path)
    assert reg.persist_transition_atomic(
        _state(state="WATCHLIST", evaluated_trading_date="2026-06-10"),
        _history(date="2026-06-10", new_state="WATCHLIST")) is True
    assert reg.persist_transition_atomic(
        _state(state="WATCHLIST", evaluated_trading_date="2026-06-11"),
        _history(date="2026-06-11", new_state="WATCHLIST")) is True
    assert reg.persist_transition_atomic(
        _state(state="ENTRY_ELIGIBLE", evaluated_trading_date="2026-06-12"),
        _history(date="2026-06-12", new_state="ENTRY_ELIGIBLE")) is True
    # exact Day-D replay → no-op even though current state advanced to 2026-06-12.
    assert reg.persist_transition_atomic(
        _state(state="WATCHLIST", evaluated_trading_date="2026-06-10"),
        _history(date="2026-06-10", new_state="WATCHLIST")) is False
    assert _counts(db) == (1, 3)                          # nothing duplicated
    with connect(db) as conn:
        row = conn.execute("SELECT current_state, evaluated_trading_date FROM universe_state "
                           "WHERE canonical_instrument_id=?", (CID,)).fetchone()
    assert row == ("ENTRY_ELIGIBLE", "2026-06-12")        # advanced state untouched


def test_divergent_historical_replay_after_advance_is_conflict(tmp_path):
    # Same key, current state advanced, but the replayed Day-D history content DIVERGES from
    # what was recorded → TransitionConflictError (history is immutable/authoritative).
    reg, db = _reg(tmp_path)
    reg.persist_transition_atomic(
        _state(state="WATCHLIST", evaluated_trading_date="2026-06-10"),
        _history(date="2026-06-10", new_state="WATCHLIST"))
    reg.persist_transition_atomic(
        _state(state="ENTRY_ELIGIBLE", evaluated_trading_date="2026-06-12"),
        _history(date="2026-06-12", new_state="ENTRY_ELIGIBLE"))
    with pytest.raises(TransitionConflictError):
        reg.persist_transition_atomic(
            _state(state="ENTRY_ELIGIBLE", evaluated_trading_date="2026-06-10"),
            _history(date="2026-06-10", new_state="ENTRY_ELIGIBLE"))   # different new_state for D
    assert _counts(db) == (1, 2)


def test_current_state_older_than_history_raises_consistency(tmp_path):
    # A history row exists for a date the current state has NOT reached (current older than
    # history) → impossible ordering → StateHistoryConsistencyError.
    reg, db = _reg(tmp_path)
    reg.persist_transition_atomic(
        _state(state="WATCHLIST", evaluated_trading_date="2026-06-10"),
        _history(date="2026-06-10", new_state="WATCHLIST"))
    # append a history row for a LATER date directly (append-only) without advancing state.
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO universe_state_history (canonical_instrument_id, trading_date, "
            "new_state, evaluator_version, created_at) VALUES (?,?,?,?,?)",
            (CID, "2026-06-12", "WATCHLIST", VER, "t"))
    # current state is still at 2026-06-10 (older than the 2026-06-12 history row).
    with pytest.raises(StateHistoryConsistencyError):
        reg.persist_transition_atomic(
            _state(state="WATCHLIST", evaluated_trading_date="2026-06-12"),
            _history(date="2026-06-12", new_state="WATCHLIST"))


def test_position_reconciliation_transition_rolls_back_together(tmp_path):
    # R1.2 (§5): a fault while persisting a POSITION_RECONCILIATION transition rolls back the
    # state, history AND the reconciliation marker together — no partially-applied block.
    reg, db = _reg(tmp_path)
    reg.persist_transition_atomic(
        _state(state="POSITION_OPEN", evaluated_trading_date="2026-06-09",
               last_authoritative_position_status="POSITION_OPEN",
               position_reconciliation_required=0),
        _history(date="2026-06-09", new_state="POSITION_OPEN"))

    def hook(seam):
        if seam == "after_state":           # markers written, COMMIT not yet reached
            raise RuntimeError("fault during reconciliation transition")

    with pytest.raises(RuntimeError):
        reg.persist_transition_atomic(
            _state(state="POSITION_RECONCILIATION", evaluated_trading_date="2026-06-10",
                   last_authoritative_position_status="POSITION_OPEN",
                   position_reconciliation_required=1),
            _history(date="2026-06-10", new_state="POSITION_RECONCILIATION"),
            _fault_hook=hook)

    with connect(db) as conn:
        row = conn.execute(
            "SELECT current_state, evaluated_trading_date, position_reconciliation_required, "
            "last_authoritative_position_status FROM universe_state "
            "WHERE canonical_instrument_id=?", (CID,)).fetchone()
    # rolled back to the prior committed row — the reconciliation block never landed.
    assert row == ("POSITION_OPEN", "2026-06-09", 0, "POSITION_OPEN")
    assert _counts(db) == (1, 1)            # the failed transition's history rolled back too


def test_authoritative_and_close_markers_roll_back_together(tmp_path):
    # R1.1 §10: the authoritative-marker and close-event updates are columns in the single
    # state upsert, so a fault at any seam rolls them back together with state + history —
    # there is no partially-updated authoritative evidence or half-processed close event.
    reg, db = _reg(tmp_path)
    reg.persist_transition_atomic(
        _state(state="POSITION_OPEN",
               last_authoritative_position_status="POSITION_OPEN",
               last_processed_position_event_id="ev-old",
               last_position_close_trading_date="2026-06-09",
               position_reconciliation_required=0),
        _history(date="2026-06-09", new_state="POSITION_OPEN"))

    def hook(seam):
        if seam == "after_state":          # markers written, COMMIT not yet reached
            raise RuntimeError("fault after marker update")

    with pytest.raises(RuntimeError):
        reg.persist_transition_atomic(
            _state(state="COOLDOWN",
                   last_authoritative_position_status="NO_POSITION",
                   last_processed_position_event_id="ev-new",
                   last_position_close_trading_date="2026-06-10",
                   position_reconciliation_required=1),
            _history(date="2026-06-10", new_state="COOLDOWN"), _fault_hook=hook)

    with connect(db) as conn:
        row = conn.execute(
            "SELECT current_state, last_authoritative_position_status, "
            "last_processed_position_event_id, last_position_close_trading_date, "
            "position_reconciliation_required FROM universe_state "
            "WHERE canonical_instrument_id=?", (CID,)).fetchone()
    # every marker remains at the PRIOR committed value — none partially advanced.
    assert row == ("POSITION_OPEN", "POSITION_OPEN", "ev-old", "2026-06-09", 0)
    assert _counts(db) == (1, 1)           # the failed transition's history rolled back too


# ── concurrency: BEGIN IMMEDIATE serialises writers ────────────────────────────
def test_concurrent_writer_cannot_diverge(tmp_path):
    reg, db = _reg(tmp_path)
    # another writer holds the write lock; persist cannot acquire BEGIN IMMEDIATE.
    blocker = sqlite3.connect(db, timeout=0.1)
    blocker.isolation_level = None
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute(
        "INSERT INTO universe_state_history (canonical_instrument_id, trading_date, "
        "new_state, evaluator_version, created_at) VALUES (?,?,?,?,?)",
        (CID, "2026-06-10", "ENTRY_ELIGIBLE", VER, "t"))
    try:
        # use a short timeout so the contended write fails fast rather than hanging.
        r = Registry(db)
        with pytest.raises(sqlite3.OperationalError):
            _persist_with_timeout(r, _state(), _history(), timeout=0.2)
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    # the blocked attempt wrote nothing.
    assert _counts(db) == (0, 0)
    # lock released → a clean write fully succeeds.
    assert reg.persist_transition_atomic(_state(), _history()) is True
    assert _counts(db) == (1, 1)


def _persist_with_timeout(reg, state, history, timeout):
    """Invoke persist with a short sqlite busy-timeout by monkeypatching connect timeout
    via a thin wrapper (the method opens its own connection with timeout=5.0; here we want
    a short one to prove fast-fail under contention)."""
    import bot.universe.registry as regmod
    orig = sqlite3.connect

    def fast_connect(path, *a, **k):
        k["timeout"] = timeout
        return orig(path, *a, **k)

    regmod.sqlite3.connect = fast_connect
    try:
        return reg.persist_transition_atomic(state, history)
    finally:
        regmod.sqlite3.connect = orig


# ── append-only history is physically enforced (defence in depth) ──────────────
def test_history_rows_cannot_be_updated_or_deleted(tmp_path):
    reg, db = _reg(tmp_path)
    reg.persist_transition_atomic(_state(), _history())
    with connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE universe_state_history SET new_state='HACKED'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM universe_state_history")
    # the row is intact and unchanged.
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT new_state FROM universe_state_history").fetchall()
    assert [r[0] for r in rows] == ["ENTRY_ELIGIBLE"]


# ── R1.3 (Finding 2): advanced replay compares the COMPLETE immutable transition ──
# (incl. cooldown bookkeeping the feature hash omits), even after the state advanced.
@pytest.mark.parametrize("field,baseline,variant", [
    ("cooldown_sessions_remaining", 3, 1),
    ("cooldown_started_trading_date", "2026-06-10", "2026-06-09"),
    ("cooldown_last_counted_trading_date", "2026-06-10", "2026-06-09"),
    ("last_processed_position_event_id", "ev-A", "ev-B"),
    ("last_position_close_trading_date", "2026-06-10", "2026-06-09"),
    ("position_reconciliation_required", 0, 1),
    ("last_authoritative_position_status", "NO_POSITION", "POSITION_OPEN"),
])
def test_advanced_replay_detects_divergent_transition_content(tmp_path, field, baseline, variant):
    reg, db = _reg(tmp_path)
    # Day D persisted with a baseline transition.
    reg.persist_transition_atomic(
        _state(state="COOLDOWN", evaluated_trading_date="2026-06-10", **{field: baseline}),
        _history(date="2026-06-10", new_state="COOLDOWN"))
    # D+1, D+2 legitimately advance the current-state row.
    reg.persist_transition_atomic(_state(state="WATCHLIST", evaluated_trading_date="2026-06-11"),
                                  _history(date="2026-06-11", new_state="WATCHLIST"))
    reg.persist_transition_atomic(_state(state="ENTRY_ELIGIBLE", evaluated_trading_date="2026-06-12"),
                                  _history(date="2026-06-12", new_state="ENTRY_ELIGIBLE"))
    # Replay Day D with ONE divergent field → conflict, even though state advanced to D+2.
    with pytest.raises(TransitionConflictError):
        reg.persist_transition_atomic(
            _state(state="COOLDOWN", evaluated_trading_date="2026-06-10", **{field: variant}),
            _history(date="2026-06-10", new_state="COOLDOWN"))


def test_advanced_exact_replay_remains_idempotent_no_op(tmp_path):
    reg, db = _reg(tmp_path)
    base = dict(state="COOLDOWN", evaluated_trading_date="2026-06-10",
                cooldown_sessions_remaining=3, cooldown_started_trading_date="2026-06-10",
                last_processed_position_event_id="ev-A", position_reconciliation_required=0)
    reg.persist_transition_atomic(_state(**base), _history(date="2026-06-10", new_state="COOLDOWN"))
    reg.persist_transition_atomic(_state(state="WATCHLIST", evaluated_trading_date="2026-06-12"),
                                  _history(date="2026-06-12", new_state="WATCHLIST"))
    # byte-identical Day-D transition replayed after advancement → idempotent no-op.
    assert reg.persist_transition_atomic(
        _state(**base), _history(date="2026-06-10", new_state="COOLDOWN")) is False
    assert _counts(db) == (1, 2)


def test_transition_snapshot_hash_is_deterministic_and_date_stable():
    from datetime import date
    from bot.universe.registry import _transition_json_and_hash
    h = {"prior_state": "WATCHLIST", "new_state": "COOLDOWN",
         "reason_codes": ["b", "a"], "feature_snapshot_hash": "fh"}
    s = {"cooldown_sessions_remaining": 3, "cooldown_started_trading_date": "2026-06-10",
         "position_reconciliation_required": 0, "last_processed_position_event_id": "ev"}
    # repeated runs → identical hash
    assert _transition_json_and_hash(s, h) == _transition_json_and_hash(s, h)
    # a date object and its ISO string hash identically (stable date serialization)
    s_date = dict(s, cooldown_started_trading_date=date(2026, 6, 10))
    assert _transition_json_and_hash(s_date, h)[1] == _transition_json_and_hash(s, h)[1]
    # reason-code ordering is canonicalized (sorted) → order-independent
    h_reordered = dict(h, reason_codes=["a", "b"])
    assert _transition_json_and_hash(s, h)[1] == _transition_json_and_hash(s, h_reordered)[1]
