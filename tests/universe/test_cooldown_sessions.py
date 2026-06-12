"""P3-2 — session-based cooldown counting (no trading-calendar dependency).

Cooldown is counted in COMPLETED evaluated sessions via the explicit session-based fields
(cooldown_started_trading_date / cooldown_sessions_remaining /
cooldown_last_counted_trading_date) — never the deprecated `cooldown_until`. Frozen
semantics: exit on session E (E not counted), blocked E+1/E+2/E+3, released E+4, earliest
ENTRY_ELIGIBLE E+5. A session decrements at most once and only when a completed bar exists
(weekend/holiday/missing-bar sessions never count); an open position or UNKNOWN status
holds the count; a duplicate same-date run never double-counts.
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


def _seed(tmp_path):
    p1, p2 = write_configs(tmp_path, [inst("AAPL")], [])
    db = str(tmp_path / "universe.db")
    seed_registry(db, p1, p2)
    return db


def _src(bars=True):
    return {"bars": (make_bars() if bars else None), "corp_action_status": "ok",
            "sector": "Tech", "spread": 0.01}


def _run(reg, snap, day, bars=True):
    pos = StubPositionProvider({CID: snap})
    ev = ShadowEvaluator(reg, SpyProvider({CID: _src(bars=bars)}), ON, equity=100_000,
                         position_provider=pos)
    r = ev.maybe_run(day, only_ids={CID})
    o = [x for x in r["outcomes"] if x["canonical_instrument_id"] == CID][0]
    return o, reg.get_state(CID)


def _start_cooldown(reg, open_day="2026-06-11", exit_day="2026-06-12"):
    _run(reg, PositionStatus.POSITION_OPEN, open_day)
    o, st = _run(reg, PositionSnapshot(status=PositionStatus.NO_POSITION,
                                       position_id="p1",
                                       closed_trading_date=date.fromisoformat(exit_day)),
                 exit_day)
    assert o["new_state"] == State.COOLDOWN.value and st["cooldown_sessions_remaining"] == 3
    assert st["cooldown_started_trading_date"] == exit_day
    return st


def test_frozen_e_e1_e2_e3_e4_e5(tmp_path):
    reg = Registry(_seed(tmp_path))
    _start_cooldown(reg, "2026-06-11", "2026-06-12")      # E = 06-12
    flat = PositionStatus.NO_POSITION
    # E+1..E+3: blocked, count 2 → 1 → 0
    expected = [("2026-06-13", 2), ("2026-06-14", 1), ("2026-06-15", 0)]
    for d, rem in expected:
        o, st = _run(reg, flat, d)
        assert o["new_state"] == State.COOLDOWN.value
        assert st["cooldown_sessions_remaining"] == rem
        assert st["cooldown_last_counted_trading_date"] == d
    # E+4: cooldown block released (→ WATCHLIST, re-accruing entry hysteresis)
    o4, st4 = _run(reg, flat, "2026-06-16")
    assert o4["new_state"] == State.WATCHLIST.value
    assert Reason.IN_COOLDOWN not in o4["reason_codes"]
    # E+5: second post-cooldown passing session → ENTRY_ELIGIBLE (earliest possible)
    o5, _ = _run(reg, flat, "2026-06-17")
    assert o5["new_state"] == State.ENTRY_ELIGIBLE.value


def test_missing_bar_session_does_not_count(tmp_path):
    # A weekend/holiday/late-bar session has no completed bar → it must NOT decrement the
    # cooldown (modelled as a missing bar, consistent with v1's no-calendar design).
    reg = Registry(_seed(tmp_path))
    _start_cooldown(reg)
    o1, st1 = _run(reg, PositionStatus.NO_POSITION, "2026-06-13")   # real session
    assert st1["cooldown_sessions_remaining"] == 2
    # missing-bar session: held, not counted.
    o2, st2 = _run(reg, PositionStatus.NO_POSITION, "2026-06-14", bars=False)
    assert st2["cooldown_sessions_remaining"] == 2                  # unchanged
    assert st2["cooldown_last_counted_trading_date"] == "2026-06-13"
    # next real completed session resumes the count.
    o3, st3 = _run(reg, PositionStatus.NO_POSITION, "2026-06-15")
    assert st3["cooldown_sessions_remaining"] == 1


def test_unknown_status_holds_count(tmp_path):
    reg = Registry(_seed(tmp_path))
    _start_cooldown(reg)
    _run(reg, PositionStatus.NO_POSITION, "2026-06-13")            # → 2
    o, st = _run(reg, PositionStatus.UNKNOWN, "2026-06-14")        # UNKNOWN holds
    assert o["new_state"] == State.EXIT_ONLY.value
    assert st["cooldown_sessions_remaining"] == 2                  # NOT decremented
    assert st["cooldown_last_counted_trading_date"] == "2026-06-13"


def test_open_position_holds_count(tmp_path):
    # Cooldown only meaningfully applies when flat; while a position is open the count is
    # held (an open instrument is not advancing its post-exit cooldown).
    reg = Registry(_seed(tmp_path))
    _start_cooldown(reg)
    _run(reg, PositionStatus.NO_POSITION, "2026-06-13")            # → 2
    o, st = _run(reg, PositionStatus.POSITION_OPEN, "2026-06-14")  # reopened → held
    assert st["cooldown_sessions_remaining"] == 2


def test_duplicate_same_date_run_does_not_double_count(tmp_path):
    reg = Registry(_seed(tmp_path))
    _start_cooldown(reg)
    o1, st1 = _run(reg, PositionStatus.NO_POSITION, "2026-06-13")  # → 2
    assert st1["cooldown_sessions_remaining"] == 2
    # re-run the SAME session: idempotent (history key already present) → no second count.
    pos = StubPositionProvider({CID: PositionStatus.NO_POSITION})
    ev = ShadowEvaluator(reg, SpyProvider({CID: _src()}), ON, equity=100_000,
                         position_provider=pos)
    r = ev.maybe_run("2026-06-13", only_ids={CID})
    assert all("skipped" in o for o in r["outcomes"])             # idempotent-skipped
    assert reg.get_state(CID)["cooldown_sessions_remaining"] == 2  # still 2
