"""Broker-neutral state machine: dominance, hysteresis, cooldown, EXIT_ONLY.

POSITION_OPEN / EXIT_ONLY here describe a HYPOTHETICAL shadow position driven by
test inputs — never a live broker position.
"""
from bot.universe import params
from bot.universe.models import EligibilityResult, Reason, State
from bot.universe.state_machine import transition

PASS = EligibilityResult(True, [Reason.ELIGIBLE])
FAIL_DATA = EligibilityResult(False, [Reason.INSUFFICIENT_HISTORY])


def t(prior=None, passes=0, fails=0, cd=0, elig=PASS, **ctx):
    return transition(prior, passes, fails, cd, elig, ctx)


def test_hard_disabled_dominates_everything():
    o = t(prior=State.POSITION_OPEN.value, elig=PASS,
          hard_disabled=True, has_open_position=True, admin_active=True)
    assert o.new_state == State.HARD_DISABLED
    assert Reason.HARD_DISABLED in o.reason_codes
    assert o.consecutive_passes == 0  # frozen


def test_admin_paused_flat_is_admin_paused():
    assert t(elig=PASS, admin_paused=True).new_state == State.ADMIN_PAUSED
    assert t(elig=PASS, admin_active=False).new_state == State.ADMIN_PAUSED


def test_admin_paused_with_open_position_is_exit_only_not_paused():
    # ADMIN_PAUSED blocks entries but NEVER removes exit management.
    o = t(prior=State.POSITION_OPEN.value, elig=PASS,
          has_open_position=True, admin_paused=True)
    assert o.new_state == State.EXIT_ONLY


def test_data_ineligible_when_flat_and_failing():
    o = t(elig=FAIL_DATA)
    assert o.new_state == State.DATA_INELIGIBLE
    assert Reason.INSUFFICIENT_HISTORY in o.reason_codes


def test_two_pass_entry_hysteresis():
    o1 = t(prior=None, passes=0, elig=PASS)
    assert o1.new_state == State.WATCHLIST and o1.consecutive_passes == 1
    o2 = t(prior=State.WATCHLIST.value, passes=1, elig=PASS)
    assert o2.new_state == State.ENTRY_ELIGIBLE and o2.consecutive_passes == 2


def test_two_failure_removal_hysteresis_is_sticky():
    # one transient failure keeps ENTRY_ELIGIBLE
    o1 = t(prior=State.ENTRY_ELIGIBLE.value, passes=2, fails=0, elig=FAIL_DATA)
    assert o1.new_state == State.ENTRY_ELIGIBLE and o1.consecutive_failures == 1
    # second consecutive failure removes it
    o2 = t(prior=State.ENTRY_ELIGIBLE.value, passes=0, fails=1, elig=FAIL_DATA)
    assert o2.new_state == State.DATA_INELIGIBLE and o2.consecutive_failures == 2


def test_position_open_stays_open_while_eligible():
    o = t(prior=State.POSITION_OPEN.value, passes=2, elig=PASS,
          has_open_position=True)
    assert o.new_state == State.POSITION_OPEN


def test_open_position_losing_eligibility_becomes_exit_only_not_liquidated():
    o = t(prior=State.POSITION_OPEN.value, elig=FAIL_DATA, has_open_position=True)
    # ordinary eligibility failure never forces liquidation
    assert o.new_state == State.EXIT_ONLY


def test_exit_only_continues_holding_no_forced_exit():
    # still holding (not exited), failing eligibility → remains EXIT_ONLY
    o = t(prior=State.EXIT_ONLY.value, elig=FAIL_DATA, has_open_position=True)
    assert o.new_state == State.EXIT_ONLY


def test_exit_only_returns_to_position_open_when_eligibility_restored():
    # EXIT_ONLY + eligibility restored while the position remains open → POSITION_OPEN.
    o = t(prior=State.EXIT_ONLY.value, passes=1, elig=PASS, has_open_position=True)
    assert o.new_state == State.POSITION_OPEN


def test_exit_only_exit_goes_to_cooldown():
    # EXIT_ONLY + position exited today → COOLDOWN (does not stay EXIT_ONLY).
    o = t(prior=State.EXIT_ONLY.value, elig=PASS,
          has_open_position=True, exit_detected=True)
    assert o.new_state == State.COOLDOWN and o.cooldown_remaining == params.COOLDOWN_SESSIONS


def test_cooldown_exit_session_does_not_count_then_blocks_e1_e2_e3():
    # FROZEN semantics (task §2): position exits on session E. The exit session itself
    # is NOT a completed post-exit cooldown session — the instrument is blocked for the
    # next three COMPLETED sessions (E+1, E+2, E+3) and the earliest re-eligibility
    # evaluation is E+4.
    # ── exit on E: cooldown set to 3, NOT decremented this session ──────────
    e = t(prior=State.POSITION_OPEN.value, elig=PASS,
          has_open_position=True, exit_detected=True)
    assert e.new_state == State.COOLDOWN and e.cooldown_remaining == 3
    # ── E+1: still COOLDOWN ─────────────────────────────────────────────────
    e1 = t(prior=State.COOLDOWN.value, passes=1, cd=3, elig=PASS)
    assert e1.new_state == State.COOLDOWN and e1.cooldown_remaining == 2
    # ── E+2: still COOLDOWN ─────────────────────────────────────────────────
    e2 = t(prior=State.COOLDOWN.value, passes=2, cd=2, elig=PASS)
    assert e2.new_state == State.COOLDOWN and e2.cooldown_remaining == 1
    # ── E+3: still COOLDOWN (last blocked session) ──────────────────────────
    e3 = t(prior=State.COOLDOWN.value, passes=3, cd=1, elig=PASS)
    assert e3.new_state == State.COOLDOWN and e3.cooldown_remaining == 0
    # ── E+4: cooldown elapsed → may transition out (all else passing) ───────
    e4 = t(prior=State.COOLDOWN.value, passes=4, cd=0, elig=PASS)
    assert e4.new_state == State.ENTRY_ELIGIBLE


def test_cooldown_e4_release_requires_eligibility():
    # E+4 may transition only if all OTHER conditions pass; a failing structural
    # eligibility at E+4 keeps it out of ENTRY_ELIGIBLE (here → DATA_INELIGIBLE).
    e4_fail = t(prior=State.COOLDOWN.value, passes=0, fails=1, cd=0, elig=FAIL_DATA)
    assert e4_fail.new_state == State.DATA_INELIGIBLE


def test_cooldown_does_not_advance_while_unknown_position():
    # An UNKNOWN position status must not advance the cooldown clock (we cannot
    # confirm the instrument is flat) — it is held, not decremented. R1.2 (P2-C): UNKNOWN
    # is position uncertainty → POSITION_RECONCILIATION, not EXIT_ONLY.
    o = t(prior=State.COOLDOWN.value, cd=2, elig=PASS, position_unknown=True)
    assert o.new_state == State.POSITION_RECONCILIATION and o.cooldown_remaining == 2


def test_unknown_position_status_blocks_entry_safely():
    # UNKNOWN never becomes entry-eligible and never forces liquidation; it holds the safe
    # non-entry POSITION_RECONCILIATION state (R1.2 / P2-C — uncertainty is NOT EXIT_ONLY,
    # which requires an authoritative open) and records the reason (never a silent pass).
    o = t(prior=State.WATCHLIST.value, passes=5, elig=PASS, position_unknown=True)
    assert o.new_state == State.POSITION_RECONCILIATION
    assert Reason.POSITION_STATUS_UNKNOWN in o.reason_codes


def test_hard_disabled_dominates_unknown_position():
    o = t(prior=State.POSITION_OPEN.value, elig=PASS,
          hard_disabled=True, position_unknown=True)
    assert o.new_state == State.HARD_DISABLED


# ── R1.2 (P2-C): POSITION_RECONCILIATION vs EXIT_ONLY split ─────────────────────
def test_uncertainty_maps_to_position_reconciliation_not_exit_only():
    # Both forms of position uncertainty resolve to POSITION_RECONCILIATION (never EXIT_ONLY).
    for ctx in ({"position_unknown": True}, {"reconciliation_required": True}):
        o = t(prior=State.WATCHLIST.value, passes=5, elig=PASS, **ctx)
        assert o.new_state == State.POSITION_RECONCILIATION
        assert o.new_state != State.EXIT_ONLY


def test_exit_only_requires_authoritative_open_position():
    # EXIT_ONLY is produced ONLY when a position authoritatively exists (has_open_position)
    # and entry is blocked (admin-paused or structurally failing) — never from uncertainty.
    paused = t(prior=State.POSITION_OPEN.value, elig=PASS,
               has_open_position=True, admin_paused=True)
    assert paused.new_state == State.EXIT_ONLY
    failing = t(prior=State.POSITION_OPEN.value, elig=FAIL_DATA, has_open_position=True)
    assert failing.new_state == State.EXIT_ONLY
    # without an authoritative open, the same blocked inputs never yield EXIT_ONLY.
    assert t(prior=State.WATCHLIST.value, elig=PASS,
             position_unknown=True).new_state != State.EXIT_ONLY


def test_position_reconciliation_blocks_entry_and_holds_cooldown():
    # A reconciliation block never becomes entry-eligible and never advances cooldown.
    o = t(prior=State.COOLDOWN.value, cd=2, passes=5, elig=PASS, reconciliation_required=True)
    assert o.new_state == State.POSITION_RECONCILIATION
    assert o.cooldown_remaining == 2                       # held, not decremented
    assert Reason.POSITION_RECONCILIATION_REQUIRED in o.reason_codes
