"""Dynamic Universe v1 — broker-neutral state machine (pure functions).

Implements the operator-frozen state model and dominance rules. No I/O, no broker,
no DB. The evaluator persists the returned StateOutcome.

States: HARD_DISABLED, DATA_INELIGIBLE, WATCHLIST, ENTRY_ELIGIBLE, POSITION_OPEN,
EXIT_ONLY, COOLDOWN, ADMIN_PAUSED.

Dominance / rules (explicit precedence):
  1. HARD_DISABLED overrides EVERY automatic state.
  2. ADMIN_PAUSED (operator off / paused) blocks new entries but NEVER removes
     exit management: a hypothetical open position becomes EXIT_ONLY, not paused.
  3. An open (hypothetical) position that loses eligibility becomes EXIT_ONLY —
     ordinary eligibility failure NEVER forces liquidation.
  4. EXIT_ONLY: no entry, no reversal, no pyramiding; deterministic exit
     management continues on the hypothetical position.
  5. Entry requires ENTRY_HYSTERESIS_PASSES consecutive structurally-passing
     sessions; ordinary removal requires REMOVAL_HYSTERESIS_FAILS consecutive
     failing sessions (sticky in between). Post-exit COOLDOWN lasts
     COOLDOWN_SESSIONS completed sessions.

POSITION_OPEN / EXIT_ONLY refer to a HYPOTHETICAL shadow position, never a live
broker position.
"""
from typing import Optional

from bot.universe import params
from bot.universe.models import (
    BLOCKING_REASONS, EligibilityResult, Reason, State, StateOutcome,
)

# Data/structural failure reasons that justify DATA_INELIGIBLE.
_DATA_REASONS = BLOCKING_REASONS - {Reason.IN_COOLDOWN}


def _as_state(value) -> Optional[State]:
    if value is None:
        return None
    if isinstance(value, State):
        return value
    try:
        return State(value)
    except ValueError:
        return None


def transition(
    prior_state,
    prior_passes: int,
    prior_failures: int,
    prior_cooldown_remaining: int,
    structural: EligibilityResult,
    ctx: dict,
) -> StateOutcome:
    """Compute the next state. Pure.

    ctx keys (all optional, default safe):
        hard_disabled (bool), admin_active (bool, default True),
        admin_paused (bool), has_open_position (bool),
        exited_this_session (bool)
    """
    prior = _as_state(prior_state)
    hard_disabled = bool(ctx.get("hard_disabled", False))
    admin_active = bool(ctx.get("admin_active", True))
    admin_paused = bool(ctx.get("admin_paused", False))
    has_open = bool(ctx.get("has_open_position", False))
    exited = bool(ctx.get("exited_this_session", False))

    reasons = list(structural.reason_codes)

    # A hypothetical trend-break/stop exit closes the position THIS session.
    effective_open = has_open and not exited

    # ── hysteresis counters (track structural-eligibility streaks) ────
    if structural.passes:
        passes = prior_passes + 1
        failures = 0
    else:
        passes = 0
        failures = prior_failures + 1

    # ── cooldown countdown (check-then-decrement) ─────────────────────
    # An exit sets the post-exit cooldown to COOLDOWN_SESSIONS; the exit session
    # itself counts as the first cooldown session. While cooling (and flat), the
    # remaining count is decremented for storage after the state decision.
    cooldown = params.COOLDOWN_SESSIONS if exited else int(prior_cooldown_remaining or 0)
    in_cooldown = cooldown > 0
    cooldown_out = cooldown - 1 if (in_cooldown and not effective_open) else cooldown

    # ── 1. HARD_DISABLED dominates everything ─────────────────────────
    if hard_disabled:
        if Reason.HARD_DISABLED not in reasons:
            reasons.append(Reason.HARD_DISABLED)
        # Counters frozen at 0 — a hard-disabled instrument never accrues entry passes.
        return StateOutcome(State.HARD_DISABLED, 0, 0, reasons, cooldown_out)

    # ── 2/3/4. Open hypothetical position branch ──────────────────────
    if effective_open:
        paused = admin_paused or not admin_active
        if paused or not structural.passes:
            if paused and Reason.ADMIN_PAUSED not in reasons:
                reasons.append(Reason.ADMIN_PAUSED)
            return StateOutcome(State.EXIT_ONLY, passes, failures, reasons, cooldown_out)
        return StateOutcome(State.POSITION_OPEN, passes, failures, reasons, cooldown_out)

    # ── No open position (flat) ───────────────────────────────────────
    if admin_paused or not admin_active:
        if not admin_active and Reason.NOT_ADMINISTRATIVELY_ACTIVE not in reasons:
            reasons.append(Reason.NOT_ADMINISTRATIVELY_ACTIVE)
        if admin_paused and Reason.ADMIN_PAUSED not in reasons:
            reasons.append(Reason.ADMIN_PAUSED)
        return StateOutcome(State.ADMIN_PAUSED, passes, failures, reasons, cooldown_out)

    if in_cooldown:
        if Reason.IN_COOLDOWN not in reasons:
            reasons.append(Reason.IN_COOLDOWN)
        return StateOutcome(State.COOLDOWN, passes, failures, reasons, cooldown_out)

    if structural.passes:
        if passes >= params.ENTRY_HYSTERESIS_PASSES:
            if Reason.PASSED_HYSTERESIS not in reasons:
                reasons.append(Reason.PASSED_HYSTERESIS)
            return StateOutcome(State.ENTRY_ELIGIBLE, passes, failures, reasons, cooldown_out)
        # structurally eligible but still accruing the 2-pass hysteresis
        return StateOutcome(State.WATCHLIST, passes, failures, reasons, cooldown_out)

    # structurally failing
    was_eligible = prior in (State.ENTRY_ELIGIBLE,)
    if was_eligible and failures < params.REMOVAL_HYSTERESIS_FAILS:
        # sticky: one transient failure does not remove eligibility
        return StateOutcome(State.ENTRY_ELIGIBLE, passes, failures, reasons, cooldown_out)

    has_data_reason = any(r in _DATA_REASONS for r in reasons)
    new_state = State.DATA_INELIGIBLE if has_data_reason else State.WATCHLIST
    return StateOutcome(new_state, passes, failures, reasons, cooldown_out)
