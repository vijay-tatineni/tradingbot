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


def test_three_session_cooldown_after_exit():
    # exit closes the hypothetical position this session → COOLDOWN for 3 sessions.
    o1 = t(prior=State.POSITION_OPEN.value, elig=PASS,
           has_open_position=True, exited_this_session=True)
    assert o1.new_state == State.COOLDOWN and o1.cooldown_remaining == 2
    o2 = t(prior=State.COOLDOWN.value, passes=1, cd=2, elig=PASS)
    assert o2.new_state == State.COOLDOWN and o2.cooldown_remaining == 1
    o3 = t(prior=State.COOLDOWN.value, passes=2, cd=1, elig=PASS)
    assert o3.new_state == State.COOLDOWN and o3.cooldown_remaining == 0
    # 4th session: cooldown elapsed → re-eligible (passes already accrued)
    o4 = t(prior=State.COOLDOWN.value, passes=3, cd=0, elig=PASS)
    assert o4.new_state == State.ENTRY_ELIGIBLE
