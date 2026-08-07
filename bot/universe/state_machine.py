"""Dynamic Universe v1 — broker-neutral state machine (pure functions).

Implements the operator-frozen state model and dominance rules. No I/O, no broker,
no DB. The evaluator persists the returned StateOutcome.

States: HARD_DISABLED, DATA_INELIGIBLE, WATCHLIST, ENTRY_ELIGIBLE, POSITION_OPEN,
EXIT_ONLY, POSITION_RECONCILIATION, COOLDOWN, ADMIN_PAUSED.

Dominance / rules (explicit precedence):
  1. HARD_DISABLED overrides EVERY automatic state.
  1a. POSITION_RECONCILIATION (R1.2 / P2-C): position ownership/status is UNRESOLVED —
     an authoritative open→flat without durable closure evidence, or any non-authoritative
     (UNKNOWN/stale/future/missing) observation. Blocks all new entries, does NOT assume a
     position exists or is flat, never forces liquidation, never decrements cooldown.
     Cleared only by an authoritative POSITION_OPEN or an evidence-bearing close. Dominates
     ordinary flat/open handling below; HARD_DISABLED still dominates it.
  2. ADMIN_PAUSED (operator off / paused) blocks new entries but NEVER removes
     exit management: a hypothetical open position becomes EXIT_ONLY, not paused.
  3. An open (hypothetical) position that loses eligibility becomes EXIT_ONLY —
     ordinary eligibility failure NEVER forces liquidation.
  4. EXIT_ONLY: a position AUTHORITATIVELY EXISTS (current authoritative snapshot is
     POSITION_OPEN) but no entry, reversal, or pyramiding is permitted; deterministic exit
     management continues on the hypothetical position. EXIT_ONLY NEVER represents position
     *uncertainty* — that is POSITION_RECONCILIATION (rule 1a).
  5. Entry requires ENTRY_HYSTERESIS_PASSES consecutive structurally-passing
     sessions; ordinary removal requires REMOVAL_HYSTERESIS_FAILS consecutive
     failing sessions (sticky in between). Post-exit COOLDOWN lasts
     COOLDOWN_SESSIONS completed sessions.

POSITION_OPEN / EXIT_ONLY / POSITION_RECONCILIATION refer to a HYPOTHETICAL shadow
position, never a live broker position.
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
        admin_paused (bool), has_open_position (bool — POSITION_OPEN only),
        position_unknown (bool),
        exit_detected (bool — an authoritative, exactly-once open→flat exit was
            observed THIS session; see evaluator P3-9 derivation. Replaces the old
            transient `exited_this_session`),
        cooldown_session_countable (bool, default True — this is a COMPLETED post-exit
            session not already counted for cooldown; weekends/holidays/missing-bar
            sessions and duplicate same-date runs pass False so they never decrement,
            P3-2).

    NOTE: `has_open_position` is now POSITION_OPEN-only. An exit is signalled solely via
    `exit_detected` (the evaluator no longer trusts a transient status value), so the
    state machine never needs to subtract a same-session exit from the open flag.
    """
    prior = _as_state(prior_state)
    hard_disabled = bool(ctx.get("hard_disabled", False))
    admin_active = bool(ctx.get("admin_active", True))
    admin_paused = bool(ctx.get("admin_paused", False))
    has_open = bool(ctx.get("has_open_position", False))
    exit_detected = bool(ctx.get("exit_detected", False))
    position_unknown = bool(ctx.get("position_unknown", False))
    reconciliation_required = bool(ctx.get("reconciliation_required", False))
    countable = bool(ctx.get("cooldown_session_countable", True))

    reasons = list(structural.reason_codes)

    # An authoritative exit closes the position THIS session → it is flat now.
    effective_open = has_open and not exit_detected

    # ── hysteresis counters (track structural-eligibility streaks) ────
    if structural.passes:
        passes = prior_passes + 1
        failures = 0
    else:
        passes = 0
        failures = prior_failures + 1

    # ── cooldown countdown (FROZEN semantics — task §2 / P3-2) ────────
    # The exit session E does NOT count as a completed post-exit cooldown session.
    # On exit, cooldown is set to COOLDOWN_SESSIONS and is NOT decremented that
    # session, so the instrument is blocked for the next three COMPLETED sessions
    # (E+1, E+2, E+3) and the earliest re-eligibility evaluation is E+4. On a
    # subsequent COUNTABLE flat session the remaining count is decremented (post
    # state-decision, check-then-decrement). While a position is open, the status is
    # UNKNOWN, or the session is not countable (weekend/holiday/missing bar / duplicate
    # same-date run) the count is HELD.
    cooldown_started = False
    cooldown_counted = False
    if exit_detected:
        cooldown_out = params.COOLDOWN_SESSIONS
        in_cooldown = True
        cooldown_started = True                       # exit session is not counted
    else:
        cooldown = int(prior_cooldown_remaining or 0)
        in_cooldown = cooldown > 0
        # A reconciliation block holds the cooldown clock too (we cannot confirm flat).
        advancing = (in_cooldown and not effective_open and not position_unknown
                     and not reconciliation_required and countable)
        cooldown_out = cooldown - 1 if advancing else cooldown
        cooldown_counted = advancing

    def _out(state, p, f):
        return StateOutcome(state, p, f, reasons, cooldown_out,
                            cooldown_started, cooldown_counted)

    # ── 1. HARD_DISABLED dominates everything ─────────────────────────
    if hard_disabled:
        if Reason.HARD_DISABLED not in reasons:
            reasons.append(Reason.HARD_DISABLED)
        # Counters frozen at 0 — a hard-disabled instrument never accrues entry passes.
        return _out(State.HARD_DISABLED, 0, 0)

    # ── 1a. position_reconciliation_required: durable blocked condition (R1.1/R1.2) ──
    # The last AUTHORITATIVE status was POSITION_OPEN and the position is now reported flat
    # without durable closure evidence (or only via a non-authoritative observation). We
    # CANNOT assume flat or manufacture an exit. R1.2 (P2-C): hold the dedicated
    # POSITION_RECONCILIATION state — NOT EXIT_ONLY, which would falsely imply a position
    # definitely exists. Blocks new entries, never forces liquidation, holds cooldown. It
    # persists across evaluations and is cleared by the evaluator only on authoritative
    # reconciliation (a confirmed POSITION_OPEN, or an evidence-bearing close). Dominates
    # UNKNOWN (a stronger, durable claim than a one-cycle unknown), but never HARD_DISABLED.
    if reconciliation_required:
        if Reason.POSITION_RECONCILIATION_REQUIRED not in reasons:
            reasons.append(Reason.POSITION_RECONCILIATION_REQUIRED)
        if admin_paused and Reason.ADMIN_PAUSED not in reasons:
            reasons.append(Reason.ADMIN_PAUSED)
        return _out(State.POSITION_RECONCILIATION, passes, failures)

    # ── 1b. UNKNOWN / non-authoritative position status: fail safe (task §3 / P3-8) ──
    # We could not determine whether a position is open (UNKNOWN, error, stale, future, or
    # missing provider). Do NOT assume flat, do NOT make the instrument entry-eligible, and
    # do NOT force liquidation. R1.2 (P2-C): this is position *uncertainty*, not a confirmed
    # open, so it maps to POSITION_RECONCILIATION (NOT EXIT_ONLY — which requires an
    # authoritative open). The reason is always recorded so an unknown is a loud non-entry,
    # never a silent pass. The cooldown advance guard above excludes position_unknown, so an
    # unknown never advances cooldown. NOTE: position_unknown drives the STATE only — it does
    # NOT set the durable reconciliation_required FLAG (a transient unknown must not stick);
    # the evaluator persists the flag solely from an authoritative unsupported open→flat.
    if position_unknown:
        if Reason.POSITION_STATUS_UNKNOWN not in reasons:
            reasons.append(Reason.POSITION_STATUS_UNKNOWN)
        if admin_paused and Reason.ADMIN_PAUSED not in reasons:
            reasons.append(Reason.ADMIN_PAUSED)
        return _out(State.POSITION_RECONCILIATION, passes, failures)

    # ── 2/3/4. Open position branch ───────────────────────────────────
    if effective_open:
        paused = admin_paused or not admin_active
        if paused or not structural.passes:
            if paused and Reason.ADMIN_PAUSED not in reasons:
                reasons.append(Reason.ADMIN_PAUSED)
            return _out(State.EXIT_ONLY, passes, failures)
        return _out(State.POSITION_OPEN, passes, failures)

    # ── No open position (flat) ───────────────────────────────────────
    if admin_paused or not admin_active:
        if not admin_active and Reason.NOT_ADMINISTRATIVELY_ACTIVE not in reasons:
            reasons.append(Reason.NOT_ADMINISTRATIVELY_ACTIVE)
        if admin_paused and Reason.ADMIN_PAUSED not in reasons:
            reasons.append(Reason.ADMIN_PAUSED)
        return _out(State.ADMIN_PAUSED, passes, failures)

    if in_cooldown:
        if Reason.IN_COOLDOWN not in reasons:
            reasons.append(Reason.IN_COOLDOWN)
        return _out(State.COOLDOWN, passes, failures)

    if structural.passes:
        if passes >= params.ENTRY_HYSTERESIS_PASSES:
            if Reason.PASSED_HYSTERESIS not in reasons:
                reasons.append(Reason.PASSED_HYSTERESIS)
            return _out(State.ENTRY_ELIGIBLE, passes, failures)
        # structurally eligible but still accruing the 2-pass hysteresis
        return _out(State.WATCHLIST, passes, failures)

    # structurally failing
    was_eligible = prior in (State.ENTRY_ELIGIBLE,)
    if was_eligible and failures < params.REMOVAL_HYSTERESIS_FAILS:
        # sticky: one transient failure does not remove eligibility
        return _out(State.ENTRY_ELIGIBLE, passes, failures)

    has_data_reason = any(r in _DATA_REASONS for r in reasons)
    new_state = State.DATA_INELIGIBLE if has_data_reason else State.WATCHLIST
    return _out(new_state, passes, failures)
