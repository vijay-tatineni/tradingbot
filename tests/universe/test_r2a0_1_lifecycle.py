"""R2A-0.1 — strict lifecycle evidence, date validation, fail-closed v3→v4 migration.

Operator ruling (supersedes the earlier R1.3 premise that an explicit close_event_id alone may
start cooldown): a close is processed ONLY when a valid lifecycle-qualified close key can be
formed from a position_id (→ hash) + a VALID opened_trading_date + a VALID closed_trading_date
(opened <= closed <= evaluation date, opened not future), plus the close_event_id when supplied.
Any missing/malformed field, or the same explicit id under a different lifecycle, fails closed to
POSITION_RECONCILIATION: entry blocked, no cooldown, no processed marker, authoritative OPEN
anchor retained.

Default-off / un-wired / broker-free / provider-injected: all providers are stubs; no broker,
data provider, or live DB is touched.
"""
from datetime import date

import pytest

import bot.universe.db as dbmod
from bot.universe.db import connect, current_version, migrate
from bot.universe.evaluator import ShadowEvaluator, _pid_hash
from bot.universe.models import PositionSnapshot, PositionStatus, Reason, State
from bot.universe.registry import (
    Registry, TransitionConflictError, _transition_json_and_hash,
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


def _continuity(reg, prior, snap, td):
    ev = ShadowEvaluator(reg, bars_provider=lambda r: None, flags={})
    return ev._position_continuity(CID, prior, snap, td)


def _open_prior(pid="X", at="2026-06-10"):
    return {"last_authoritative_position_status": "POSITION_OPEN",
            "last_authoritative_position_id_hash": _pid_hash(pid),
            "last_authoritative_observed_at": at, "position_reconciliation_required": 0}


# ── 1. explicit ID + valid full lifecycle → cooldown once (persisted) ───────────
def test_explicit_id_full_lifecycle_starts_cooldown_once(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg)
    o, st = _run_day(reg, PositionSnapshot(
        status=PositionStatus.NO_POSITION, position_id="p1", close_event_id="E1",
        opened_trading_date=date(2026, 6, 12), closed_trading_date=date(2026, 6, 15)),
        "2026-06-15")
    assert o["new_state"] == State.COOLDOWN.value
    assert Reason.IN_COOLDOWN in o["reason_codes"]
    assert st["cooldown_sessions_remaining"] == COOLDOWN_FULL          # exit session not counted
    assert st["last_processed_position_event_id"] == "E1"
    assert st["last_close_event_key"] is not None
    assert st["last_close_event_key"].startswith("close-key:v2:")
    assert st["position_reconciliation_required"] == 0
    assert st["last_authoritative_position_status"] == "NO_POSITION"


# ── 2/3. explicit ID + missing opened / closed → reconciliation ─────────────────
def test_explicit_id_missing_opened_date_is_reconciliation(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg)
    o, st = _run_day(reg, PositionSnapshot(status=PositionStatus.NO_POSITION, position_id="p1",
                                           close_event_id="E1",
                                           closed_trading_date=date(2026, 6, 15)), "2026-06-15")
    assert o["new_state"] == State.POSITION_RECONCILIATION.value
    assert Reason.POSITION_RECONCILIATION_REQUIRED in o["reason_codes"]
    assert st["position_reconciliation_required"] == 1
    assert (st["cooldown_sessions_remaining"] or 0) == 0
    assert st["last_processed_position_event_id"] is None
    assert st["last_authoritative_position_status"] == "POSITION_OPEN"  # OPEN anchor retained


def test_explicit_id_missing_closed_date_is_reconciliation(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg)
    o, st = _run_day(reg, PositionSnapshot(status=PositionStatus.NO_POSITION, position_id="p1",
                                           close_event_id="E1",
                                           opened_trading_date=date(2026, 6, 12)), "2026-06-15")
    assert o["new_state"] == State.POSITION_RECONCILIATION.value
    assert Reason.POSITION_RECONCILIATION_REQUIRED in o["reason_codes"]
    assert st["position_reconciliation_required"] == 1
    assert (st["cooldown_sessions_remaining"] or 0) == 0
    assert st["last_processed_position_event_id"] is None


def test_missing_position_id_is_reconciliation(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg)
    o, st = _run_day(reg, PositionSnapshot(status=PositionStatus.NO_POSITION, close_event_id="E1",
                                           opened_trading_date=date(2026, 6, 12),
                                           closed_trading_date=date(2026, 6, 15)), "2026-06-15")
    assert o["new_state"] == State.POSITION_RECONCILIATION.value
    assert Reason.POSITION_RECONCILIATION_REQUIRED in o["reason_codes"]
    assert st["position_reconciliation_required"] == 1
    assert st["last_processed_position_event_id"] is None


# ── 4/5/6. same explicit ID + different opened / closed / pid → contract violation ──
def _violation_case(diff):
    px = _pid_hash("X")
    base_opened, base_closed = date(2026, 6, 10), date(2026, 6, 20)
    key1 = ShadowEvaluator._qualified_close_key(CID, px, base_opened, base_closed, "E1")
    prior2 = {"last_authoritative_position_status": "POSITION_OPEN",
              "last_authoritative_position_id_hash": px,
              "last_authoritative_observed_at": "2026-06-30",
              "last_processed_position_event_id": "E1",
              "last_position_close_trading_date": "2026-06-20",
              "last_close_event_key": key1, "position_reconciliation_required": 0}
    snap = dict(status=PositionStatus.NO_POSITION, position_id="X", close_event_id="E1",
                opened_trading_date=base_opened, closed_trading_date=base_closed)
    snap.update(diff)
    return prior2, PositionSnapshot(**snap)


@pytest.mark.parametrize("name,diff", [
    ("different_opened", {"opened_trading_date": date(2026, 6, 13)}),
    ("different_closed", {"closed_trading_date": date(2026, 6, 21)}),
    ("different_pid", {"position_id": "Y"}),
])
def test_same_explicit_id_different_lifecycle_is_contract_violation(tmp_path, name, diff):
    reg = Registry(_seed(tmp_path))
    prior2, snap = _violation_case(diff)
    c = _continuity(reg, prior2, snap, date(2026, 6, 30))
    assert c["reconciliation_required"] is True, name          # provider-contract violation
    assert c["exit_detected"] is False, name                   # second close NOT manufactured
    assert c["last_authoritative_position_status"] == "POSITION_OPEN", name   # anchor retained
    assert c["last_processed_position_event_id"] == "E1", name                # not reprocessed


def test_same_explicit_id_same_lifecycle_is_replay_not_violation(tmp_path):
    # guard against false positives: identical lifecycle ⇒ idempotent replay, not a violation
    reg = Registry(_seed(tmp_path))
    prior2, snap = _violation_case({})                         # no diff = same lifecycle
    c = _continuity(reg, prior2, snap, date(2026, 6, 30))
    assert c["reconciliation_required"] is False
    assert c["exit_detected"] is False                         # already processed (replay)


# ── 7/8/9/10. malformed / impossible / future dates → reconciliation ────────────
@pytest.mark.parametrize("name,snap_kwargs", [
    ("malformed_opened", dict(opened_trading_date="not-a-date",
                              closed_trading_date=date(2026, 6, 15))),
    ("malformed_closed", dict(opened_trading_date=date(2026, 6, 12),
                              closed_trading_date="garbage")),
    ("empty_opened", dict(opened_trading_date="", closed_trading_date=date(2026, 6, 15))),
    ("closed_before_opened", dict(opened_trading_date=date(2026, 6, 15),
                                  closed_trading_date=date(2026, 6, 12))),
    ("future_opened", dict(opened_trading_date=date(2026, 6, 20),
                           closed_trading_date=date(2026, 6, 21))),
    ("future_closed", dict(opened_trading_date=date(2026, 6, 12),
                           closed_trading_date=date(2026, 6, 20))),
])
def test_invalid_lifecycle_date_is_reconciliation(tmp_path, name, snap_kwargs):
    reg = Registry(_seed(tmp_path))
    _open(reg)
    o, st = _run_day(reg, PositionSnapshot(status=PositionStatus.NO_POSITION, position_id="p1",
                                           **snap_kwargs), "2026-06-15")
    assert o["new_state"] == State.POSITION_RECONCILIATION.value, name
    assert Reason.POSITION_RECONCILIATION_REQUIRED in o["reason_codes"], name
    assert st["position_reconciliation_required"] == 1, name
    assert (st["cooldown_sessions_remaining"] or 0) == 0, name
    assert st["last_processed_position_event_id"] is None, name
    assert st["last_authoritative_position_status"] == "POSITION_OPEN", name   # anchor retained


# ── v3 → v4 fail-closed migration ───────────────────────────────────────────────
def _build_v3_with_row(tmp_path, row_sql_cols, row_values):
    """Build a schema-v3 universe.db, insert one universe_state row, then upgrade to head (v4)."""
    db = str(tmp_path / "universe.db")
    orig = dbmod.MIGRATIONS
    try:
        dbmod.MIGRATIONS = [m for m in orig if m[0] <= 3]
        migrate(db)
        assert current_version(db) == 3
        with connect(db) as conn:
            # a fully-mapped instrument so structural eligibility can pass (research + IBKR
            # gateway present) — otherwise an authoritative OPEN would resolve to EXIT_ONLY.
            conn.execute("INSERT INTO canonical_instruments (canonical_instrument_id, "
                         "display_symbol, research_symbol, currency, primary_gateway, "
                         "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                         (CID, "AAPL", "AAPL", "USD", "IBKR", "t", "t"))
            conn.execute("INSERT INTO gateway_map_ibkr (canonical_instrument_id, symbol, "
                         "verification_status) VALUES (?,?,?)", (CID, "AAPL", "CONFIG_DERIVED"))
            conn.execute(f"INSERT INTO universe_state ({row_sql_cols}) "
                         f"VALUES ({','.join('?' for _ in row_values)})", row_values)
        dbmod.MIGRATIONS = orig
        migrate(db)                                            # → v4 + fail-closed back-fill
        assert current_version(db) == 5
    finally:
        dbmod.MIGRATIONS = orig
    return db


def test_v3_processed_event_row_migrates_to_reconciliation_required(tmp_path):
    db = _build_v3_with_row(
        tmp_path,
        "canonical_instrument_id, current_state, evaluated_trading_date, evaluator_version, "
        "last_processed_position_event_id, last_position_close_trading_date, "
        "last_authoritative_position_status, last_authoritative_position_id_hash, "
        "last_authoritative_observed_at, position_reconciliation_required",
        (CID, "POSITION_OPEN", "2026-06-24", VER, "E1", "2026-06-20", "POSITION_OPEN",
         _pid_hash("X"), "2026-06-24", 0))
    with connect(db) as conn:
        recon, key = conn.execute(
            "SELECT position_reconciliation_required, last_close_event_key "
            "FROM universe_state WHERE canonical_instrument_id=?", (CID,)).fetchone()
    assert recon == 1                       # fail-closed: blocked for reconciliation
    assert key is None                      # no key inferred/reconstructed from legacy data


def test_v3_row_without_processed_event_is_not_blocked_by_v4(tmp_path):
    # a clean flat anchor with no processed event must NOT be blocked by the v4 back-fill.
    db = _build_v3_with_row(
        tmp_path,
        "canonical_instrument_id, current_state, evaluated_trading_date, evaluator_version, "
        "last_authoritative_position_status, position_reconciliation_required",
        (CID, "WATCHLIST", "2026-06-24", VER, "NO_POSITION", 0))
    with connect(db) as conn:
        recon = conn.execute("SELECT position_reconciliation_required FROM universe_state "
                             "WHERE canonical_instrument_id=?", (CID,)).fetchone()[0]
    assert recon == 0


def test_migrated_ambiguous_row_cannot_enter(tmp_path):
    # the migrated (blocked) row evaluates to POSITION_RECONCILIATION while flat → entry blocked.
    db = _build_v3_with_row(
        tmp_path,
        "canonical_instrument_id, current_state, evaluated_trading_date, evaluator_version, "
        "last_processed_position_event_id, last_authoritative_position_status, "
        "last_authoritative_position_id_hash, last_authoritative_observed_at, "
        "position_reconciliation_required",
        (CID, "POSITION_OPEN", "2026-06-24", VER, "E1", "POSITION_OPEN", _pid_hash("X"),
         "2026-06-24", 0))
    reg = Registry(db)
    o, st = _run_day(reg, PositionStatus.NO_POSITION, "2026-06-25")
    assert o["new_state"] == State.POSITION_RECONCILIATION.value
    assert st["position_reconciliation_required"] == 1
    # never offered as a new-entry candidate
    ev = ShadowEvaluator(reg, SpyProvider({CID: _src()}), ON, equity=100_000,
                         position_provider=StubPositionProvider({CID: PositionStatus.NO_POSITION}))
    r = ev.maybe_run("2026-06-26", only_ids={CID})
    assert CID not in [s["canonical_instrument_id"] for s in r["selected"]]


def test_migrated_row_cleared_by_authoritative_open(tmp_path):
    db = _build_v3_with_row(
        tmp_path,
        "canonical_instrument_id, current_state, evaluated_trading_date, evaluator_version, "
        "last_processed_position_event_id, last_authoritative_position_status, "
        "last_authoritative_position_id_hash, last_authoritative_observed_at, "
        "position_reconciliation_required",
        (CID, "POSITION_OPEN", "2026-06-24", VER, "E1", "POSITION_OPEN", _pid_hash("X"),
         "2026-06-24", 0))
    reg = Registry(db)
    o, st = _run_day(reg, PositionStatus.POSITION_OPEN, "2026-06-25")
    assert o["new_state"] == State.POSITION_OPEN.value
    assert st["position_reconciliation_required"] == 0          # cleared by confirmed OPEN


def test_migrated_row_reconciled_into_cooldown_by_valid_close(tmp_path):
    db = _build_v3_with_row(
        tmp_path,
        "canonical_instrument_id, current_state, evaluated_trading_date, evaluator_version, "
        "last_processed_position_event_id, last_authoritative_position_status, "
        "last_authoritative_position_id_hash, last_authoritative_observed_at, "
        "position_reconciliation_required",
        (CID, "POSITION_OPEN", "2026-06-24", VER, "E1", "POSITION_OPEN", _pid_hash("X"),
         "2026-06-24", 0))
    reg = Registry(db)
    # a NEW, distinct, fully-qualified close (different explicit id + full lifecycle) reconciles
    # the row into COOLDOWN.
    o, st = _run_day(reg, PositionSnapshot(
        status=PositionStatus.NO_POSITION, position_id="p9", close_event_id="E2",
        opened_trading_date=date(2026, 6, 25), closed_trading_date=date(2026, 6, 26)),
        "2026-06-26")
    assert o["new_state"] == State.COOLDOWN.value
    assert st["position_reconciliation_required"] == 0
    assert st["cooldown_sessions_remaining"] == COOLDOWN_FULL
    assert st["last_processed_position_event_id"] == "E2"


# ── P3 advisory: persist-level conflict on a divergent last_close_event_key ──────
def test_persist_conflict_on_different_last_close_event_key(tmp_path):
    reg = Registry(_seed(tmp_path))

    def state(key):
        return {"canonical_instrument_id": CID, "current_state": "COOLDOWN",
                "evaluated_trading_date": "2026-06-15", "evaluator_version": VER,
                "feature_snapshot_hash": "fh", "last_close_event_key": key}

    def history():
        return {"canonical_instrument_id": CID, "trading_date": "2026-06-15",
                "prior_state": "POSITION_OPEN", "new_state": "COOLDOWN",
                "evaluator_version": VER, "feature_snapshot_hash": "fh"}

    assert reg.persist_transition_atomic(state("close-key:v2:A"), history()) is True
    # same idempotency key (cid, date, version) but a DIFFERENT last_close_event_key → conflict.
    with pytest.raises(TransitionConflictError):
        reg.persist_transition_atomic(state("close-key:v2:B"), history())
    # the field genuinely participates in the immutable transition hash.
    _, h_a = _transition_json_and_hash(state("close-key:v2:A"), history())
    _, h_b = _transition_json_and_hash(state("close-key:v2:B"), history())
    assert h_a != h_b
