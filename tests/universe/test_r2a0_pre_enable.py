"""R2A-0 pre-enable residuals — P3-R1-A / P3-R1-B / P3-R1-C.

P3-R1-A: a provider ``close_event_id`` MUST be globally unique per close lifecycle. The SAME
explicit id observed under a DIFFERENT lifecycle discriminator (canonical id + hashed
position id + opened trading date) is a provider-contract violation routed to
POSITION_RECONCILIATION — never an idempotent replay that masks a genuine second close.

P3-R1-B: advanced replay of a history row with a NULL ``transition_snapshot_hash`` fails
closed (StateHistoryConsistencyError); the complete transition snapshot/hash is now written
by every supported history-writing API (no new NULL-hash rows).

P3-R1-C: explicit coverage of the close_event_id reuse, NULL-hash replay, every
transition-hash material field, and the bare POSITION_EXITED / POSITION_EXITED_TODAY
ambiguity cases.

Default-off / un-wired / broker-free / provider-injected: all providers here are stubs; no
broker, data provider, or live DB is touched.
"""
from datetime import date

import pytest

from bot.universe.db import connect
from bot.universe.evaluator import ShadowEvaluator, _pid_hash
from bot.universe.models import PositionSnapshot, PositionStatus, State
from bot.universe.registry import (
    Registry, StateHistoryConsistencyError, TransitionConflictError,
    _TRANSITION_STATE_FIELDS, _transition_json_and_hash,
)
from bot.universe.seed import canonical_id, seed_registry
from tests.universe._fixtures import (
    ON, SpyProvider, StubPositionProvider, inst, make_bars, write_configs,
)

CID = canonical_id("AAPL", "USD", "NASDAQ")
VER = "dyn_universe_shadow_v1"
COOLDOWN_FULL = 3


def _seed(tmp_path):
    p1, p2 = write_configs(tmp_path, [inst("AAPL")], [])
    db = str(tmp_path / "universe.db")
    seed_registry(db, p1, p2)
    return db


def _src():
    return {"bars": make_bars(), "corp_action_status": "ok", "sector": "Tech", "spread": 0.01}


def _run_day(reg, snap, day):
    pos = StubPositionProvider({CID: snap})
    ev = ShadowEvaluator(reg, SpyProvider({CID: _src()}), ON, equity=100_000,
                         position_provider=pos)
    r = ev.maybe_run(day, only_ids={CID})
    o = [x for x in r["outcomes"] if x["canonical_instrument_id"] == CID][0]
    return o, reg.get_state(CID)


def _open(reg, day="2026-06-12"):
    o, st = _run_day(reg, PositionStatus.POSITION_OPEN, day)
    assert o["new_state"] == State.POSITION_OPEN.value
    return st


# ── P3-R1-A: explicit close_event_id reuse across distinct lifecycles ───────────
def test_explicit_close_event_id_reused_across_lifecycles_is_contract_violation(tmp_path):
    reg = Registry(_seed(tmp_path))
    ev = ShadowEvaluator(reg, bars_provider=lambda r: None, flags={})
    px = _pid_hash("X")
    # lifecycle 1: open 06-10, explicit close id E1, closed 06-20 → genuine exit.
    prior1 = {"last_authoritative_position_status": "POSITION_OPEN",
              "last_authoritative_position_id_hash": px,
              "last_authoritative_observed_at": "2026-06-10",
              "position_reconciliation_required": 0}
    c1 = ev._position_continuity(CID, prior1, PositionSnapshot(
        status=PositionStatus.NO_POSITION, position_id="X", close_event_id="E1",
        opened_trading_date=date(2026, 6, 10), closed_trading_date=date(2026, 6, 20)),
        date(2026, 6, 20))
    assert c1["exit_detected"] is True
    assert c1["last_processed_position_event_id"] == "E1"
    assert c1["last_close_event_key"] is not None
    # lifecycle 2 (re-opened then closed): SAME explicit id E1 but DIFFERENT opened date.
    prior2 = {"last_authoritative_position_status": "POSITION_OPEN",
              "last_authoritative_position_id_hash": px,
              "last_authoritative_observed_at": "2026-06-30",
              "last_processed_position_event_id": c1["last_processed_position_event_id"],
              "last_position_close_trading_date": c1["last_position_close_trading_date"],
              "last_close_event_key": c1["last_close_event_key"],
              "position_reconciliation_required": 0}
    c2 = ev._position_continuity(CID, prior2, PositionSnapshot(
        status=PositionStatus.NO_POSITION, position_id="X", close_event_id="E1",
        opened_trading_date=date(2026, 6, 25), closed_trading_date=date(2026, 6, 30)),
        date(2026, 6, 30))
    assert c2["reconciliation_required"] is True       # provider-contract violation
    assert c2["exit_detected"] is False                # second close NOT manufactured/masked
    # authoritative anchor is NOT advanced to flat on a violation; cooldown not started.
    assert c2["last_authoritative_position_status"] == "POSITION_OPEN"


def test_explicit_close_event_id_reuse_routes_state_to_reconciliation(tmp_path):
    # Integration: seed the stored markers from lifecycle 1, then a reused-id close at a new
    # open date lands the universe state in POSITION_RECONCILIATION with no cooldown.
    reg = Registry(_seed(tmp_path))
    px = _pid_hash("X")
    key1 = ShadowEvaluator._close_event_key(CID, px, date(2026, 6, 10), "E1")
    reg.upsert_state({
        "canonical_instrument_id": CID, "current_state": "POSITION_OPEN",
        "evaluated_trading_date": "2026-06-24", "evaluator_version": VER,
        "last_authoritative_position_status": "POSITION_OPEN",
        "last_authoritative_position_id_hash": px,
        "last_authoritative_observed_at": "2026-06-24",
        "last_processed_position_event_id": "E1",
        "last_position_close_trading_date": "2026-06-20",
        "last_close_event_key": key1, "position_reconciliation_required": 0})
    o, st = _run_day(reg, PositionSnapshot(
        status=PositionStatus.NO_POSITION, position_id="X", close_event_id="E1",
        opened_trading_date=date(2026, 6, 25), closed_trading_date=date(2026, 6, 26)),
        "2026-06-26")
    assert o["new_state"] == State.POSITION_RECONCILIATION.value
    assert st["position_reconciliation_required"] == 1
    assert (st["cooldown_sessions_remaining"] or 0) == 0
    assert st["last_processed_position_event_id"] == "E1"   # not reprocessed


def test_explicit_close_event_id_same_lifecycle_replay_is_idempotent(tmp_path):
    # Guard against false positives: the SAME explicit id with the SAME discriminator is a
    # genuine replay — no violation, no second exit.
    reg = Registry(_seed(tmp_path))
    ev = ShadowEvaluator(reg, bars_provider=lambda r: None, flags={})
    px = _pid_hash("X")
    snap = PositionSnapshot(status=PositionStatus.POSITION_EXITED, position_id="X",
                            close_event_id="E1", opened_trading_date=date(2026, 6, 10),
                            closed_trading_date=date(2026, 6, 20))
    prior1 = {"last_authoritative_position_status": "POSITION_OPEN",
              "last_authoritative_position_id_hash": px,
              "last_authoritative_observed_at": "2026-06-10",
              "position_reconciliation_required": 0}
    c1 = ev._position_continuity(CID, prior1, snap, date(2026, 6, 20))
    assert c1["exit_detected"] is True
    prior2 = {"last_authoritative_position_status": "NO_POSITION",
              "last_authoritative_position_id_hash": px,
              "last_authoritative_observed_at": "2026-06-20",
              "last_processed_position_event_id": "E1",
              "last_position_close_trading_date": c1["last_position_close_trading_date"],
              "last_close_event_key": c1["last_close_event_key"],
              "position_reconciliation_required": 0}
    c2 = ev._position_continuity(CID, prior2, snap, date(2026, 6, 21))
    assert c2["reconciliation_required"] is False
    assert c2["exit_detected"] is False                    # already processed, not restarted
    assert c2["last_processed_position_event_id"] == "E1"


# ── P3-R1-C: bare durable-exit statuses with no lifecycle-safe identity ──────────
def test_bare_position_exited_without_identity_is_reconciliation(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg)
    # POSITION_EXITED with NO close_event_id and NO opened_trading_date → ambiguous identity.
    o, st = _run_day(reg, PositionSnapshot(status=PositionStatus.POSITION_EXITED,
                                           position_id="p1"), "2026-06-15")
    assert o["new_state"] == State.POSITION_RECONCILIATION.value
    assert st["position_reconciliation_required"] == 1
    assert (st["cooldown_sessions_remaining"] or 0) == 0
    assert st["last_processed_position_event_id"] is None


def test_bare_position_exited_today_without_identity_is_reconciliation(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg)
    # the DEPRECATED transient marker, bare (no open date / no close id) → also ambiguous.
    o, st = _run_day(reg, PositionSnapshot(status=PositionStatus.POSITION_EXITED_TODAY,
                                           position_id="p1"), "2026-06-15")
    assert o["new_state"] == State.POSITION_RECONCILIATION.value
    assert st["position_reconciliation_required"] == 1
    assert (st["cooldown_sessions_remaining"] or 0) == 0
    assert st["last_processed_position_event_id"] is None


# ── P3-R1-B: NULL transition_snapshot_hash handling ─────────────────────────────
def _insert_legacy_history(db, trading_date, new_state="ENTRY_ELIGIBLE",
                           prior_state="WATCHLIST", feature_hash="fh"):
    """Insert a raw legacy history row with NULL transition_snapshot_hash (simulating a
    pre-v3 / raw insert that the hardened append_history would no longer produce)."""
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO universe_state_history "
            "(canonical_instrument_id, trading_date, prior_state, new_state, reason_codes, "
            " feature_snapshot_json, feature_snapshot_hash, evaluator_version, created_at, "
            " transition_snapshot_json, transition_snapshot_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (CID, trading_date, prior_state, new_state, None, None, feature_hash, VER,
             "t", None, None))


def test_advanced_replay_of_null_transition_hash_fails_closed(tmp_path):
    reg = Registry(_seed(tmp_path))
    _insert_legacy_history(reg.db_path, "2026-06-10")
    # current state has legitimately ADVANCED past the legacy date.
    reg.upsert_state({"canonical_instrument_id": CID, "current_state": "WATCHLIST",
                      "evaluated_trading_date": "2026-06-12", "evaluator_version": VER})
    # advanced replay over a NULL-hash row → fail closed (do not trust incomplete content).
    with pytest.raises(StateHistoryConsistencyError):
        reg.persist_transition_atomic(
            {"canonical_instrument_id": CID, "current_state": "ENTRY_ELIGIBLE",
             "evaluated_trading_date": "2026-06-10", "evaluator_version": VER,
             "feature_snapshot_hash": "fh"},
            {"canonical_instrument_id": CID, "trading_date": "2026-06-10",
             "prior_state": "WATCHLIST", "new_state": "ENTRY_ELIGIBLE",
             "evaluator_version": VER, "feature_snapshot_hash": "fh"})


def test_same_date_replay_of_null_transition_hash_uses_full_comparison(tmp_path):
    reg = Registry(_seed(tmp_path))
    _insert_legacy_history(reg.db_path, "2026-06-10")
    # current state still reflects the SAME date → full constituent comparison is available.
    reg.upsert_state({"canonical_instrument_id": CID, "current_state": "ENTRY_ELIGIBLE",
                      "evaluated_trading_date": "2026-06-10", "evaluator_version": VER,
                      "feature_snapshot_hash": "fh"})
    # exact same-date replay → idempotent no-op (NOT an error, NOT fail-closed).
    assert reg.persist_transition_atomic(
        {"canonical_instrument_id": CID, "current_state": "ENTRY_ELIGIBLE",
         "evaluated_trading_date": "2026-06-10", "evaluator_version": VER,
         "feature_snapshot_hash": "fh"},
        {"canonical_instrument_id": CID, "trading_date": "2026-06-10",
         "prior_state": "WATCHLIST", "new_state": "ENTRY_ELIGIBLE",
         "evaluator_version": VER, "feature_snapshot_hash": "fh"}) is False
    # a DIVERGENT same-date replay is still a conflict (not silently accepted).
    with pytest.raises(TransitionConflictError):
        reg.persist_transition_atomic(
            {"canonical_instrument_id": CID, "current_state": "ENTRY_ELIGIBLE",
             "evaluated_trading_date": "2026-06-10", "evaluator_version": VER,
             "feature_snapshot_hash": "DIFFERENT"},
            {"canonical_instrument_id": CID, "trading_date": "2026-06-10",
             "prior_state": "WATCHLIST", "new_state": "ENTRY_ELIGIBLE",
             "evaluator_version": VER, "feature_snapshot_hash": "DIFFERENT"})


def test_append_history_writes_complete_transition_hash(tmp_path):
    # Every supported history-writing API now stores a non-NULL transition_snapshot_hash.
    reg = Registry(_seed(tmp_path))
    assert reg.append_history({
        "canonical_instrument_id": CID, "trading_date": "2026-06-10",
        "prior_state": "WATCHLIST", "new_state": "ENTRY_ELIGIBLE",
        "evaluator_version": VER, "feature_snapshot_hash": "fh"}) is True
    with connect(reg.db_path) as conn:
        thash = conn.execute(
            "SELECT transition_snapshot_hash FROM universe_state_history "
            "WHERE canonical_instrument_id=? AND trading_date=?",
            (CID, "2026-06-10")).fetchone()[0]
    assert thash is not None


# ── P3-R1-C: every transition-hash material field is covered ────────────────────
def test_transition_snapshot_hash_covers_every_material_field():
    # Base transition: each material field has a definite value; mutating ANY one must change
    # the hash (so no material field — including the new last_close_event_key — is dropped).
    base_history = {"prior_state": "WATCHLIST", "new_state": "ENTRY_ELIGIBLE",
                    "reason_codes": ["a"], "feature_snapshot_hash": "fh"}

    def base_state_value(field):
        if field == "cooldown_sessions_remaining":
            return 3
        if field == "position_reconciliation_required":
            return 0
        return "2026-06-10"          # ISO-string-safe for date and plain string fields alike

    base_state = {f: base_state_value(f) for f in _TRANSITION_STATE_FIELDS}
    _, h0 = _transition_json_and_hash(base_state, base_history)

    # every HISTORY-derived material field
    for hf, alt in (("prior_state", "DATA_INELIGIBLE"), ("new_state", "WATCHLIST"),
                    ("reason_codes", ["b"]), ("feature_snapshot_hash", "fh2")):
        _, h = _transition_json_and_hash(base_state, {**base_history, hf: alt})
        assert h != h0, f"transition hash ignores history field {hf}"

    # every STATE-derived material field in the registry tuple
    for sf in _TRANSITION_STATE_FIELDS:
        if sf == "cooldown_sessions_remaining":
            alt = 4
        elif sf == "position_reconciliation_required":
            alt = 1
        else:
            alt = "2026-06-11"
        _, h = _transition_json_and_hash({**base_state, sf: alt}, base_history)
        assert h != h0, f"transition hash ignores state field {sf}"

    # explicit: the new P3-R1-A close-event key is one of the covered fields.
    assert "last_close_event_key" in _TRANSITION_STATE_FIELDS
