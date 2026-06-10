"""§3 — broker-free position-status seam drives the POSITION_OPEN / EXIT_ONLY /
COOLDOWN lifecycle ORGANICALLY (no broker, no live position read).

The injected PositionSnapshotProvider supplies operational status only; the universe
package still imports no broker adapter and calls no broker method (asserted in
tests/universe/test_no_live_integration.py and by the recorded provider calls here).
"""
import sqlite3

from bot.universe.evaluator import ShadowEvaluator
from bot.universe.models import PositionStatus, Reason, State
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


def _src(sector="Tech", bars=True):
    s = {"corp_action_status": "ok", "sector": sector, "spread": 0.01}
    s["bars"] = make_bars() if bars else None
    return s


def _preset_state(reg, state, passes=2, cooldown="0"):
    reg.upsert_state({"canonical_instrument_id": CID, "current_state": state,
                      "consecutive_passes": passes, "consecutive_failures": 0,
                      "cooldown_until": cooldown,
                      "evaluator_version": "dyn_universe_shadow_v1"})


def _run(reg, prov, pos, date):
    ev = ShadowEvaluator(reg, prov, ON, equity=100_000, position_provider=pos)
    r = ev.maybe_run(date)
    o = [x for x in r["outcomes"] if x["canonical_instrument_id"] == CID][0]
    return r, o


def test_provider_drives_position_open(tmp_path):
    db = _seed(tmp_path)
    reg = Registry(db)
    _preset_state(reg, State.ENTRY_ELIGIBLE.value)
    pos = StubPositionProvider({CID: PositionStatus.POSITION_OPEN})
    _, o = _run(reg, SpyProvider({CID: _src()}), pos, "2026-06-10")
    assert o["new_state"] == State.POSITION_OPEN.value
    assert pos.calls == [(CID, "2026-06-10")]          # the seam WAS consulted


def test_position_open_to_exit_only_on_lost_eligibility(tmp_path):
    db = _seed(tmp_path)
    reg = Registry(db)
    _preset_state(reg, State.POSITION_OPEN.value)
    pos = StubPositionProvider({CID: PositionStatus.POSITION_OPEN})
    # eligibility lost (no bars → structural fail) but position still open → EXIT_ONLY,
    # never forced liquidation.
    _, o = _run(reg, SpyProvider({CID: _src(bars=False)}), pos, "2026-06-10")
    assert o["new_state"] == State.EXIT_ONLY.value


def test_exit_only_back_to_position_open(tmp_path):
    db = _seed(tmp_path)
    reg = Registry(db)
    _preset_state(reg, State.EXIT_ONLY.value)
    pos = StubPositionProvider({CID: PositionStatus.POSITION_OPEN})
    _, o = _run(reg, SpyProvider({CID: _src()}), pos, "2026-06-10")   # eligibility restored
    assert o["new_state"] == State.POSITION_OPEN.value


def test_position_exit_to_cooldown(tmp_path):
    db = _seed(tmp_path)
    reg = Registry(db)
    _preset_state(reg, State.POSITION_OPEN.value)
    pos = StubPositionProvider({CID: PositionStatus.POSITION_EXITED_TODAY})
    _, o = _run(reg, SpyProvider({CID: _src()}), pos, "2026-06-10")
    assert o["new_state"] == State.COOLDOWN.value
    # persisted cooldown_remaining is 3 (exit session does not count — task §2)
    st = reg.get_state(CID)
    assert st["cooldown_until"] == "3"


def test_unknown_position_blocks_entry_and_is_recorded(tmp_path):
    db = _seed(tmp_path)
    reg = Registry(db)
    _preset_state(reg, State.ENTRY_ELIGIBLE.value)
    pos = StubPositionProvider({CID: PositionStatus.UNKNOWN})
    r, o = _run(reg, SpyProvider({CID: _src()}), pos, "2026-06-10")
    assert o["new_state"] == State.EXIT_ONLY.value           # safe non-entry hold
    assert Reason.POSITION_STATUS_UNKNOWN in o["reason_codes"]
    # never offered as a new-entry candidate despite a strong breakout signal
    assert CID not in [s["canonical_instrument_id"] for s in r["selected"]]


def test_provider_error_is_treated_as_unknown(tmp_path):
    db = _seed(tmp_path)
    reg = Registry(db)
    _preset_state(reg, State.ENTRY_ELIGIBLE.value)

    class Boom:
        def get_position_status(self, *a):
            raise RuntimeError("provider down")

    r, o = _run(reg, SpyProvider({CID: _src()}), Boom(), "2026-06-10")
    assert o["new_state"] == State.EXIT_ONLY.value           # fail safe, not entry-eligible
    assert Reason.POSITION_STATUS_UNKNOWN in o["reason_codes"]


def test_legacy_derivation_without_provider_unchanged(tmp_path):
    # No provider injected → legacy prior-state derivation still works (back-compat).
    db = _seed(tmp_path)
    reg = Registry(db)
    _preset_state(reg, State.ENTRY_ELIGIBLE.value)
    ev = ShadowEvaluator(reg, SpyProvider({CID: _src()}), ON, equity=100_000)
    r = ev.maybe_run("2026-06-10")
    o = [x for x in r["outcomes"] if x["canonical_instrument_id"] == CID][0]
    # flat by derivation + passing → stays ENTRY_ELIGIBLE
    assert o["new_state"] == State.ENTRY_ELIGIBLE.value


def test_cooldown_e1_e2_e3_block_e4_release_via_provider(tmp_path):
    """End-to-end through the evaluator: exit → COOLDOWN, blocked E+1/E+2/E+3, and the
    cooldown BLOCK lifts at E+4 — proving the frozen §2 semantics organically.

    Note: because IN_COOLDOWN is a blocking structural reason, the entry-hysteresis
    passes counter resets during cooldown, so at E+4 the instrument leaves COOLDOWN
    into WATCHLIST and must re-accrue the 2-pass hysteresis (ENTRY_ELIGIBLE at E+5).
    The cooldown block itself is fully released at E+4 — that is the §2 invariant."""
    db = _seed(tmp_path)
    reg = Registry(db)
    _preset_state(reg, State.POSITION_OPEN.value)
    src = SpyProvider({CID: _src()})
    # E: exit today → COOLDOWN (remaining 3; exit session does not count)
    pos_exit = StubPositionProvider({CID: PositionStatus.POSITION_EXITED_TODAY})
    _, oE = _run(reg, src, pos_exit, "2026-06-10")
    assert oE["new_state"] == State.COOLDOWN.value
    # E+1..E+3: flat, still COOLDOWN (blocked)
    pos_flat = StubPositionProvider({CID: PositionStatus.NO_POSITION})
    states = []
    for d in ("2026-06-11", "2026-06-12", "2026-06-13"):
        _, o = _run(reg, SpyProvider({CID: _src()}), pos_flat, d)
        states.append(o["new_state"])
    assert states == [State.COOLDOWN.value] * 3
    # E+4: cooldown block released → leaves COOLDOWN (re-accruing entry hysteresis)
    _, o4 = _run(reg, SpyProvider({CID: _src()}), pos_flat, "2026-06-14")
    assert o4["new_state"] == State.WATCHLIST.value
    assert Reason.IN_COOLDOWN not in o4["reason_codes"]      # no longer cooldown-blocked
    # E+5: second post-cooldown passing session → ENTRY_ELIGIBLE
    _, o5 = _run(reg, SpyProvider({CID: _src()}), pos_flat, "2026-06-15")
    assert o5["new_state"] == State.ENTRY_ELIGIBLE.value


def test_no_broker_call_with_position_provider(tmp_path):
    db = _seed(tmp_path)
    reg = Registry(db)
    src = SpyProvider({CID: _src()})
    pos = StubPositionProvider({CID: PositionStatus.NO_POSITION})
    ev = ShadowEvaluator(reg, src, ON, equity=100_000, position_provider=pos)
    ev.maybe_run("2026-06-10")
    # The ONLY two seams touched are the injected bars + position providers.
    assert src.calls == [CID]
    assert pos.calls == [(CID, "2026-06-10")]
