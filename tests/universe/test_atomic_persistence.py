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
from bot.universe.registry import Registry

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


# ── idempotency ─────────────────────────────────────────────────────────────--
def test_replay_is_idempotent_no_duplicate_no_double_advance(tmp_path):
    reg, db = _reg(tmp_path)
    assert reg.persist_transition_atomic(
        _state(state="COOLDOWN", remaining=2), _history(new_state="COOLDOWN")) is True
    # a retried-after-success run for the same (cid, date, version): the state dict even
    # carries a DIFFERENT (double-advanced) count, but the duplicate history key makes the
    # whole tx a no-op → neither the history nor the state changes.
    assert reg.persist_transition_atomic(
        _state(state="COOLDOWN", remaining=1), _history(new_state="COOLDOWN")) is False
    s, h = _counts(db)
    assert h == 1                            # no duplicate history
    with connect(db) as conn:
        remaining = conn.execute(
            "SELECT cooldown_sessions_remaining FROM universe_state WHERE canonical_instrument_id=?",
            (CID,)).fetchone()[0]
    assert remaining == 2                    # NOT double-advanced to 1


def test_idempotency_conflict_leaves_prior_state_untouched(tmp_path):
    # pre-existing history row for the key (as if a prior writer committed); a second
    # persist for the same key must roll back and not perturb the prior state.
    reg, db = _reg(tmp_path)
    reg.persist_transition_atomic(_state(state="WATCHLIST"), _history(new_state="WATCHLIST"))
    before = _state_value(db)
    assert reg.persist_transition_atomic(
        _state(state="ENTRY_ELIGIBLE"), _history(new_state="ENTRY_ELIGIBLE")) is False
    assert _state_value(db) == before        # unchanged (still WATCHLIST)
    assert _counts(db) == (1, 1)


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
