"""P3-9 — durable, exactly-once open→flat exit detection.

Cooldown no longer depends on observing the transient POSITION_EXITED_TODAY. An exit is
started from an authoritative OPEN→NO_POSITION transition WITH durable evidence
(closed_trading_date / position_id) — or an explicit durable POSITION_EXITED signal —
and is de-duplicated by a durable position_event_id so a replay never restarts cooldown.
UNKNOWN→flat, a no-evidence open→flat, and OPEN→UNKNOWN never start cooldown.

These tests assert the persisted markers directly (not just the resulting state), since
the marker bookkeeping is the exactly-once linchpin.
"""
from datetime import date

from bot.universe.evaluator import ShadowEvaluator
from bot.universe.models import PositionSnapshot, PositionStatus, Reason, State
from bot.universe.registry import Registry
from bot.universe.seed import canonical_id, seed_registry
from tests.universe._fixtures import (
    ON, SpyProvider, StubPositionProvider, inst, make_bars, write_configs,
)

CID = canonical_id("AAPL", "USD", "NASDAQ")
COOLDOWN_FULL = 3


def _seed(tmp_path):
    p1, p2 = write_configs(tmp_path, [inst("AAPL")], [])
    db = str(tmp_path / "universe.db")
    seed_registry(db, p1, p2)
    return db


def _src():
    return {"bars": make_bars(), "corp_action_status": "ok", "sector": "Tech",
            "spread": 0.01}


def _run_day(reg, snap, day):
    """Run a single evaluation for CID with the given position snapshot/status."""
    pos = StubPositionProvider({CID: snap})
    ev = ShadowEvaluator(reg, SpyProvider({CID: _src()}), ON, equity=100_000,
                         position_provider=pos)
    r = ev.maybe_run(day, only_ids={CID})
    o = [x for x in r["outcomes"] if x["canonical_instrument_id"] == CID][0]
    return o, reg.get_state(CID)


def _open(reg, day="2026-06-12"):
    """Drive CID to an authoritatively-open position; assert the observed marker."""
    o, st = _run_day(reg, PositionStatus.POSITION_OPEN, day)
    assert o["new_state"] == State.POSITION_OPEN.value
    assert st["last_observed_position_status"] == "POSITION_OPEN"
    return st


# ── the core P3-9 fix: durable open→flat with evidence, no EXITED_TODAY ─────────
def test_open_to_flat_with_evidence_starts_cooldown_once(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg)
    snap = PositionSnapshot(status=PositionStatus.NO_POSITION,
                            position_id="p1", closed_trading_date=date(2026, 6, 15))
    o, st = _run_day(reg, snap, "2026-06-15")
    assert o["new_state"] == State.COOLDOWN.value
    assert st["cooldown_sessions_remaining"] == COOLDOWN_FULL   # E not counted
    assert st["cooldown_started_trading_date"] == "2026-06-15"
    assert st["last_processed_position_event_id"] is not None    # event recorded
    assert st["last_position_close_trading_date"] == "2026-06-15"
    # after an exit the persisted observed status is NO_POSITION (so the next flat day is
    # not a fresh open→flat).
    assert st["last_observed_position_status"] == "NO_POSITION"


def test_missed_transient_exited_still_detected_by_durable_evidence(tmp_path):
    # The provider NEVER emits POSITION_EXITED_TODAY — it goes straight OPEN → NO_POSITION
    # (with a close date). Cooldown must still start (the masking dependency is gone).
    reg = Registry(_seed(tmp_path))
    _open(reg)
    o, st = _run_day(reg, PositionSnapshot(status=PositionStatus.NO_POSITION,
                                           closed_trading_date=date(2026, 6, 15)),
                     "2026-06-15")
    assert o["new_state"] == State.COOLDOWN.value
    assert st["cooldown_sessions_remaining"] == COOLDOWN_FULL


# ── things that must NOT start cooldown ────────────────────────────────────────
def test_open_to_no_position_without_evidence_does_not_start_cooldown(tmp_path):
    # A bare OPEN→NO_POSITION (no close date, no position_id) is indistinguishable from a
    # provider glitch → cooldown must NOT start (spec: distinguish exit from failure).
    reg = Registry(_seed(tmp_path))
    _open(reg)
    o, st = _run_day(reg, PositionStatus.NO_POSITION, "2026-06-15")
    assert o["new_state"] != State.COOLDOWN.value
    assert (st["cooldown_sessions_remaining"] or 0) == 0
    assert st["last_processed_position_event_id"] is None         # no exit event recorded


def test_open_to_unknown_does_not_start_cooldown(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg)
    o, st = _run_day(reg, PositionStatus.UNKNOWN, "2026-06-15")
    assert o["new_state"] == State.EXIT_ONLY.value                # UNKNOWN safe hold
    assert (st["cooldown_sessions_remaining"] or 0) == 0
    assert st["last_processed_position_event_id"] is None
    # OPEN→UNKNOWN: the stored observed status is UNKNOWN (so a later NO_POSITION is NOT a
    # fresh open→flat — prevents a false trigger via UNKNOWN).
    assert st["last_observed_position_status"] == "UNKNOWN"


def test_unknown_to_flat_does_not_start_cooldown(tmp_path):
    reg = Registry(_seed(tmp_path))
    # never authoritatively open: UNKNOWN then NO_POSITION (even with a close date) is not
    # an authoritative open→flat exit.
    _run_day(reg, PositionStatus.UNKNOWN, "2026-06-12")
    o, st = _run_day(reg, PositionSnapshot(status=PositionStatus.NO_POSITION,
                                           closed_trading_date=date(2026, 6, 13)),
                     "2026-06-13")
    assert o["new_state"] != State.COOLDOWN.value
    assert (st["cooldown_sessions_remaining"] or 0) == 0
    assert st["last_processed_position_event_id"] is None


# ── exactly-once dedup + genuinely-new close ───────────────────────────────────
def test_replayed_close_event_does_not_restart_cooldown(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg)
    # explicit durable exit signal for a specific close.
    snap = PositionSnapshot(status=PositionStatus.POSITION_EXITED,
                            position_id="p1", closed_trading_date=date(2026, 6, 15))
    o1, st1 = _run_day(reg, snap, "2026-06-15")
    assert o1["new_state"] == State.COOLDOWN.value
    assert st1["cooldown_sessions_remaining"] == COOLDOWN_FULL
    event1 = st1["last_processed_position_event_id"]
    # the SAME close re-observed on a later session: must NOT reset the counter to 3.
    o2, st2 = _run_day(reg, snap, "2026-06-16")
    assert st2["last_processed_position_event_id"] == event1       # same event, no reprocess
    assert st2["cooldown_sessions_remaining"] < COOLDOWN_FULL       # not restarted


def test_new_separate_close_starts_new_cooldown(tmp_path):
    reg = Registry(_seed(tmp_path))
    _open(reg, "2026-06-12")
    o1, st1 = _run_day(reg, PositionSnapshot(status=PositionStatus.NO_POSITION,
                                             position_id="p1",
                                             closed_trading_date=date(2026, 6, 13)),
                       "2026-06-13")
    assert o1["new_state"] == State.COOLDOWN.value
    event1 = st1["last_processed_position_event_id"]
    # drain the first cooldown to completion (E+1..E+3 blocked, released at E+4).
    for d in ("2026-06-14", "2026-06-15", "2026-06-16"):
        o, _ = _run_day(reg, PositionStatus.NO_POSITION, d)
        assert o["new_state"] == State.COOLDOWN.value
    o4, _ = _run_day(reg, PositionStatus.NO_POSITION, "2026-06-17")
    assert o4["new_state"] != State.COOLDOWN.value          # cooldown released
    # a NEW position opens after cooldown, then a separate later close → new lifecycle.
    _open(reg, "2026-06-18")
    o2, st2 = _run_day(reg, PositionSnapshot(status=PositionStatus.NO_POSITION,
                                             position_id="p2",
                                             closed_trading_date=date(2026, 6, 19)),
                       "2026-06-19")
    assert o2["new_state"] == State.COOLDOWN.value
    assert st2["last_processed_position_event_id"] != event1        # distinct close event
    assert st2["cooldown_sessions_remaining"] == COOLDOWN_FULL      # fresh 3-session cooldown
