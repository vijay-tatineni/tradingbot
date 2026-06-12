"""P3-8 — authoritative position-state contract.

The shadow evaluator NEVER uses stale prior universe state as a substitute for
authoritative position knowledge. A missing provider, a provider exception, a malformed /
unrecognised return, or a stale/absent observation all resolve to UNKNOWN — which blocks
new entry, never forces liquidation, never assumes flat, and never decrements cooldown.
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


def _src():
    return {"bars": make_bars(), "corp_action_status": "ok", "sector": "Tech",
            "spread": 0.01}


def _preset(reg, state, **extra):
    reg.upsert_state({"canonical_instrument_id": CID, "current_state": state,
                      "consecutive_passes": 2, "consecutive_failures": 0,
                      "cooldown_sessions_remaining": 0,
                      "evaluator_version": "dyn_universe_shadow_v1", **extra})


def _run(reg, provider):
    ev = ShadowEvaluator(reg, SpyProvider({CID: _src()}), ON, equity=100_000,
                         position_provider=provider)
    r = ev.maybe_run("2026-06-10", only_ids={CID})
    o = [x for x in r["outcomes"] if x["canonical_instrument_id"] == CID][0]
    return r, o, reg.get_state(CID)


class _Boom:
    def get_position_status(self, *a):
        raise TimeoutError("provider timeout")


class _Garbage:
    def __init__(self, value):
        self.value = value

    def get_position_status(self, *a):
        return self.value


def _assert_unknown_safe(o, r, st):
    assert o["new_state"] == State.EXIT_ONLY.value                 # safe non-entry hold
    assert Reason.POSITION_STATUS_UNKNOWN in o["reason_codes"]
    assert CID not in [s["canonical_instrument_id"] for s in r["selected"]]  # blocks entry
    assert st["last_observed_position_status"] == "UNKNOWN"


def test_missing_provider_is_unknown(tmp_path):
    reg = Registry(_seed(tmp_path))
    _preset(reg, State.ENTRY_ELIGIBLE.value)
    ev = ShadowEvaluator(reg, SpyProvider({CID: _src()}), ON, equity=100_000)  # no provider
    r = ev.maybe_run("2026-06-10", only_ids={CID})
    o = [x for x in r["outcomes"] if x["canonical_instrument_id"] == CID][0]
    _assert_unknown_safe(o, r, reg.get_state(CID))


def test_provider_exception_or_timeout_is_unknown(tmp_path):
    reg = Registry(_seed(tmp_path))
    _preset(reg, State.ENTRY_ELIGIBLE.value)
    r, o, st = _run(reg, _Boom())
    _assert_unknown_safe(o, r, st)


def test_malformed_or_unrecognised_value_is_unknown(tmp_path):
    for i, bad in enumerate((12345, "NOT_A_STATUS", object(), {"status": "open"})):
        d = tmp_path / f"case{i}"
        d.mkdir()
        reg = Registry(_seed(d))
        _preset(reg, State.ENTRY_ELIGIBLE.value)
        r, o, st = _run(reg, _Garbage(bad))
        _assert_unknown_safe(o, r, st)


def test_stale_or_absent_observation_is_unknown(tmp_path):
    # A provider with no current observation returns None (stale/absent) → UNKNOWN.
    reg = Registry(_seed(tmp_path))
    _preset(reg, State.ENTRY_ELIGIBLE.value)
    r, o, st = _run(reg, _Garbage(None))
    _assert_unknown_safe(o, r, st)


def test_unknown_never_forces_liquidation(tmp_path):
    # An open position that becomes UNKNOWN is HELD (EXIT_ONLY), never liquidated/flattened.
    reg = Registry(_seed(tmp_path))
    _preset(reg, State.POSITION_OPEN.value)
    r, o, st = _run(reg, StubPositionProvider({CID: PositionStatus.UNKNOWN}))
    assert o["new_state"] == State.EXIT_ONLY.value                 # held, not liquidated
    assert Reason.POSITION_STATUS_UNKNOWN in o["reason_codes"]


def test_unknown_never_assumes_flat_and_holds_cooldown(tmp_path):
    # UNKNOWN must not be treated as flat: it neither becomes entry-eligible nor advances
    # a running cooldown clock.
    reg = Registry(_seed(tmp_path))
    _preset(reg, State.COOLDOWN.value, cooldown_sessions_remaining=2)
    r, o, st = _run(reg, StubPositionProvider({CID: PositionStatus.UNKNOWN}))
    assert o["new_state"] == State.EXIT_ONLY.value
    assert st["cooldown_sessions_remaining"] == 2                  # not decremented (not flat)


def test_stale_prior_open_is_not_treated_as_authoritative(tmp_path):
    # The P3-8 scenario: a prior POSITION_OPEN state with NO provider must NOT be retained
    # as a known-open position — it resolves UNKNOWN-safe, not POSITION_OPEN.
    reg = Registry(_seed(tmp_path))
    _preset(reg, State.POSITION_OPEN.value)
    ev = ShadowEvaluator(reg, SpyProvider({CID: _src()}), ON, equity=100_000)  # provider removed
    r = ev.maybe_run("2026-06-10", only_ids={CID})
    o = [x for x in r["outcomes"] if x["canonical_instrument_id"] == CID][0]
    assert o["new_state"] == State.EXIT_ONLY.value
    assert Reason.POSITION_STATUS_UNKNOWN in o["reason_codes"]
    assert reg.get_state(CID)["last_observed_position_status"] == "UNKNOWN"
