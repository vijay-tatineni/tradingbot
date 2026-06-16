"""R1.1 — authoritative position continuity across provider outages (P3-8/P3-9 corrections).

The last AUTHORITATIVE position evidence survives non-authoritative (UNKNOWN/stale/future/
missing) observations, so an exit that happens during an outage is still detected exactly
once when an evidence-bearing close arrives. An open→flat WITHOUT explicit closure evidence
enters a durable position_reconciliation_required block (never assume flat / never
manufacture an exit). A bare position_id is not closure evidence. Asserts the persisted
authoritative markers directly.
"""
from datetime import date, datetime, timezone

from bot.universe.evaluator import ShadowEvaluator
from bot.universe.models import PositionSnapshot, PositionStatus, Reason, State
from bot.universe.registry import Registry
from bot.universe.seed import canonical_id, seed_registry
from tests.universe._fixtures import (
    ON, SpyProvider, StubPositionProvider, inst, make_bars, write_configs,
)

CID = canonical_id("AAPL", "USD", "NASDAQ")


def _seed(tmp_path):
    p1, p2 = write_configs(tmp_path, [inst("AAPL")], [])
    db = str(tmp_path / "universe.db")
    seed_registry(db, p1, p2)
    return db


def _src():
    return {"bars": make_bars(), "corp_action_status": "ok", "sector": "Tech",
            "spread": 0.01}


def _run(reg, snap, day):
    ev = ShadowEvaluator(reg, SpyProvider({CID: _src()}), ON, equity=100_000,
                         position_provider=StubPositionProvider({CID: snap}))
    r = ev.maybe_run(day, only_ids={CID})
    o = [x for x in r["outcomes"] if x["canonical_instrument_id"] == CID][0]
    return o, reg.get_state(CID), r


def _open(reg, day="2026-06-12"):
    o, st, _ = _run(reg, PositionStatus.POSITION_OPEN, day)
    assert o["new_state"] == State.POSITION_OPEN.value
    assert st["last_authoritative_position_status"] == "POSITION_OPEN"
    return st


# ── outage continuity ─────────────────────────────────────────────────────────
def test_open_unknown_open_no_exit_authoritative_preserved(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg, "2026-06-12")
    o, st, _ = _run(reg, PositionStatus.UNKNOWN, "2026-06-13")
    assert o["new_state"] == State.POSITION_RECONCILIATION.value         # R1.2 (P2-C): uncertainty
    assert st["latest_observed_position_status"] == "UNKNOWN"
    assert st["last_authoritative_position_status"] == "POSITION_OPEN"   # preserved through outage
    o, st, _ = _run(reg, PositionStatus.POSITION_OPEN, "2026-06-14")
    assert o["new_state"] == State.POSITION_OPEN.value
    assert (st["cooldown_sessions_remaining"] or 0) == 0                 # no cooldown
    assert st["last_processed_position_event_id"] is None


def test_open_unknown_flat_with_evidence_starts_cooldown_once(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg, "2026-06-12")
    _run(reg, PositionStatus.UNKNOWN, "2026-06-13")
    o, st, _ = _run(reg, PositionSnapshot(status=PositionStatus.NO_POSITION,
                                          opened_trading_date=date(2026, 6, 12),  # R1.3 discriminator
                                          closed_trading_date=date(2026, 6, 14)), "2026-06-14")
    assert o["new_state"] == State.COOLDOWN.value
    assert st["cooldown_sessions_remaining"] == 3
    assert st["last_processed_position_event_id"] is not None
    assert Reason.IN_COOLDOWN in o["reason_codes"]                       # no immediate re-entry


def test_open_unknown_flat_without_evidence_requires_reconciliation(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg, "2026-06-12")
    _run(reg, PositionStatus.UNKNOWN, "2026-06-13")
    o, st, r = _run(reg, PositionStatus.NO_POSITION, "2026-06-14")     # bare flat, no evidence
    assert o["new_state"] == State.POSITION_RECONCILIATION.value       # R1.2 (P2-C)
    assert Reason.POSITION_RECONCILIATION_REQUIRED in o["reason_codes"]
    assert st["position_reconciliation_required"] == 1
    assert (st["cooldown_sessions_remaining"] or 0) == 0               # no manufactured cooldown
    assert st["last_authoritative_position_status"] == "POSITION_OPEN"  # anchor kept
    assert CID not in [s["canonical_instrument_id"] for s in r["selected"]]  # entry blocked


def test_unknown_then_flat_with_no_prior_open_no_manufactured_exit(tmp_path):
    reg = Registry(_seed(tmp_path))
    _run(reg, PositionStatus.UNKNOWN, "2026-06-12")
    o, st, _ = _run(reg, PositionSnapshot(status=PositionStatus.NO_POSITION,
                                          closed_trading_date=date(2026, 6, 13)), "2026-06-13")
    assert o["new_state"] != State.COOLDOWN.value
    assert (st["cooldown_sessions_remaining"] or 0) == 0
    assert st["last_processed_position_event_id"] is None
    assert st["position_reconciliation_required"] == 0


# ── closure evidence contract ─────────────────────────────────────────────────
def test_bare_position_id_is_not_closure_evidence(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg, "2026-06-12")
    o, st, _ = _run(reg, PositionSnapshot(status=PositionStatus.NO_POSITION,
                                          position_id="p1"), "2026-06-13")  # id only, no closure
    assert o["new_state"] == State.POSITION_RECONCILIATION.value       # R1.2 (P2-C)
    assert Reason.POSITION_RECONCILIATION_REQUIRED in o["reason_codes"]
    assert (st["cooldown_sessions_remaining"] or 0) == 0


def test_closure_with_lifecycle_discriminator_starts_cooldown_once(tmp_path):
    # R1.3 (Finding 1): cooldown starts only when a COLLISION-SAFE close identity can be
    # formed — an explicit close_event_id, OR opened_trading_date + closed_trading_date.
    for i, ev_snap in enumerate((
        PositionSnapshot(status=PositionStatus.NO_POSITION, close_event_id="close-7"),
        PositionSnapshot(status=PositionStatus.NO_POSITION,
                         opened_trading_date=date(2026, 6, 12),
                         closed_trading_date=date(2026, 6, 13)),
        # explicit id wins even when a (reused) position_id is also present.
        PositionSnapshot(status=PositionStatus.NO_POSITION, position_id="reused",
                         close_event_id="close-9"),
    )):
        d = tmp_path / f"ev{i}"
        d.mkdir()
        reg = Registry(_seed(d))
        _open(reg, "2026-06-12")
        o, st, _ = _run(reg, ev_snap, "2026-06-13")
        assert o["new_state"] == State.COOLDOWN.value, ev_snap
        assert st["cooldown_sessions_remaining"] == 3


def test_closure_without_lifecycle_discriminator_requires_reconciliation(tmp_path):
    # R1.3 (Finding 1): a close with closure evidence but NO collision-safe identity
    # (closed_trading_date alone, or explicitly_closed alone, or a bare position_id) is
    # AMBIGUOUS → POSITION_RECONCILIATION, never a collision-prone cooldown bypass.
    for i, ambiguous_snap in enumerate((
        PositionSnapshot(status=PositionStatus.NO_POSITION, closed_trading_date=date(2026, 6, 13)),
        PositionSnapshot(status=PositionStatus.NO_POSITION, explicitly_closed=True),
        PositionSnapshot(status=PositionStatus.NO_POSITION, position_id="p1",
                         closed_trading_date=date(2026, 6, 13)),
    )):
        d = tmp_path / f"amb{i}"
        d.mkdir()
        reg = Registry(_seed(d))
        _open(reg, "2026-06-12")
        o, st, _ = _run(reg, ambiguous_snap, "2026-06-13")
        assert o["new_state"] == State.POSITION_RECONCILIATION.value, ambiguous_snap
        assert st["position_reconciliation_required"] == 1
        assert (st["cooldown_sessions_remaining"] or 0) == 0
        assert st["last_processed_position_event_id"] is None
        assert st["last_authoritative_position_status"] == "POSITION_OPEN"   # anchor kept


# ── observation freshness / ordering ──────────────────────────────────────────
def test_older_snapshot_cannot_override_newer_authoritative(tmp_path):
    reg = Registry(_seed(tmp_path))
    # authoritative OPEN observed on 06-14
    _run(reg, PositionSnapshot(status=PositionStatus.POSITION_OPEN,
                               observed_at=datetime(2026, 6, 14, tzinfo=timezone.utc)), "2026-06-14")
    # an OLDER (06-13) flat snapshot must not override the newer OPEN evidence
    o, st, _ = _run(reg, PositionSnapshot(status=PositionStatus.NO_POSITION,
                                          observed_at=datetime(2026, 6, 13, tzinfo=timezone.utc),
                                          closed_trading_date=date(2026, 6, 13)), "2026-06-15")
    assert o["new_state"] != State.COOLDOWN.value
    assert (st["cooldown_sessions_remaining"] or 0) == 0
    assert st["last_authoritative_position_status"] == "POSITION_OPEN"   # not overridden


def test_future_dated_snapshot_is_non_authoritative(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg, "2026-06-12")
    o, st, _ = _run(reg, PositionSnapshot(status=PositionStatus.NO_POSITION,
                                          observed_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
                                          closed_trading_date=date(2026, 6, 13)), "2026-06-13")
    # future-dated → treated as UNKNOWN/fail-safe; authoritative OPEN preserved, no exit.
    assert o["new_state"] == State.POSITION_RECONCILIATION.value      # R1.2 (P2-C): non-authoritative
    assert Reason.POSITION_STATUS_UNKNOWN in o["reason_codes"]
    assert st["last_authoritative_position_status"] == "POSITION_OPEN"
    assert (st["cooldown_sessions_remaining"] or 0) == 0


# ── event idempotency ─────────────────────────────────────────────────────────
def test_replayed_close_event_does_not_reset_cooldown(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg, "2026-06-12")
    snap = PositionSnapshot(status=PositionStatus.POSITION_EXITED, close_event_id="close-1",
                            closed_trading_date=date(2026, 6, 13))
    o1, st1, _ = _run(reg, snap, "2026-06-13")
    assert o1["new_state"] == State.COOLDOWN.value and st1["cooldown_sessions_remaining"] == 3
    ev1 = st1["last_processed_position_event_id"]
    o2, st2, _ = _run(reg, snap, "2026-06-14")            # same close re-observed
    assert st2["last_processed_position_event_id"] == ev1
    assert st2["cooldown_sessions_remaining"] < 3          # decremented, NOT reset to 3


def test_reconciliation_persists_across_unknown_gap(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg, "2026-06-12")
    o, st, _ = _run(reg, PositionStatus.NO_POSITION, "2026-06-13")   # open->flat no evidence
    assert st["position_reconciliation_required"] == 1
    for d in ("2026-06-14", "2026-06-15"):                          # outage gap
        o, st, _ = _run(reg, PositionStatus.UNKNOWN, d)
        assert o["new_state"] == State.POSITION_RECONCILIATION.value  # R1.2 (P2-C): remains blocked
        assert st["position_reconciliation_required"] == 1          # still blocked


def test_reconciliation_cleared_by_authoritative_open(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg, "2026-06-12")
    _run(reg, PositionStatus.NO_POSITION, "2026-06-13")            # reconciliation set
    o, st, _ = _run(reg, PositionStatus.POSITION_OPEN, "2026-06-14")
    assert o["new_state"] == State.POSITION_OPEN.value
    assert st["position_reconciliation_required"] == 0             # cleared by confirmed open


def test_reconciliation_cleared_by_evidence_close_starts_cooldown(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg, "2026-06-12")
    _run(reg, PositionStatus.NO_POSITION, "2026-06-13")            # reconciliation set
    o, st, _ = _run(reg, PositionSnapshot(status=PositionStatus.NO_POSITION,
                                          close_event_id="c-9"), "2026-06-14")
    assert o["new_state"] == State.COOLDOWN.value
    assert st["position_reconciliation_required"] == 0
    assert st["cooldown_sessions_remaining"] == 3


def test_exited_today_backcompat_with_discriminator_starts_cooldown_once(tmp_path):
    # The deprecated transient signal is still honoured WHEN it carries a collision-safe
    # identity (R1.3 / Finding 1 — here an opened_trading_date lifecycle discriminator).
    reg = Registry(_seed(tmp_path))
    _open(reg, "2026-06-12")
    o, st, _ = _run(reg, PositionSnapshot(status=PositionStatus.POSITION_EXITED_TODAY,
                                          opened_trading_date=date(2026, 6, 12),
                                          closed_trading_date=date(2026, 6, 13)), "2026-06-13")
    assert o["new_state"] == State.COOLDOWN.value
    assert st["cooldown_sessions_remaining"] == 3


def test_exited_today_backcompat_bare_is_ambiguous_reconciliation(tmp_path):
    # R1.3 (Finding 1): a BARE POSITION_EXITED_TODAY (no explicit id, no opened date) cannot
    # form a collision-safe identity → POSITION_RECONCILIATION, not a cooldown bypass.
    reg = Registry(_seed(tmp_path))
    _open(reg, "2026-06-12")
    o, st, _ = _run(reg, PositionStatus.POSITION_EXITED_TODAY, "2026-06-13")
    assert o["new_state"] == State.POSITION_RECONCILIATION.value
    assert st["position_reconciliation_required"] == 1
    assert (st["cooldown_sessions_remaining"] or 0) == 0


# ── R1.2 (P2-C): the dedicated POSITION_RECONCILIATION state ────────────────────
def test_open_unknown_unsupported_flat_yields_position_reconciliation(tmp_path):
    # OPEN → UNKNOWN → unsupported NO_POSITION resolves to the dedicated reconciliation
    # state at every uncertain step (never EXIT_ONLY, which would imply a position exists),
    # blocks entry, and never manufactures a cooldown.
    reg = Registry(_seed(tmp_path))
    _open(reg, "2026-06-12")
    o, _, _ = _run(reg, PositionStatus.UNKNOWN, "2026-06-13")
    assert o["new_state"] == State.POSITION_RECONCILIATION.value
    o, st, r = _run(reg, PositionStatus.NO_POSITION, "2026-06-14")          # unsupported flat
    assert o["new_state"] == State.POSITION_RECONCILIATION.value
    assert st["position_reconciliation_required"] == 1
    assert (st["cooldown_sessions_remaining"] or 0) == 0
    assert CID not in [s["canonical_instrument_id"] for s in r["selected"]]  # entry blocked


def test_stale_no_position_remains_position_reconciliation(tmp_path):
    # Once reconciliation is required, a STALE NO_POSITION snapshot (observed > 3 trading days
    # before the evaluation date) is non-authoritative and must NOT clear the block.
    reg = Registry(_seed(tmp_path))
    _open(reg, "2026-06-12")
    o, st, _ = _run(reg, PositionStatus.NO_POSITION, "2026-06-13")          # reconciliation set
    assert st["position_reconciliation_required"] == 1
    o, st, _ = _run(reg, PositionSnapshot(status=PositionStatus.NO_POSITION,
                                          observed_at=date(2026, 6, 13),
                                          closed_trading_date=date(2026, 6, 13)), "2026-06-20")
    assert o["new_state"] == State.POSITION_RECONCILIATION.value            # stale → still blocked
    assert st["position_reconciliation_required"] == 1
    assert st["last_authoritative_position_status"] == "POSITION_OPEN"      # anchor retained
