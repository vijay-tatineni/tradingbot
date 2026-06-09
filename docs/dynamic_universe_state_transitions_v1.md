# Dynamic Universe v1 — State Transitions

> Implemented as pure functions in `bot/universe/state_machine.py` (no I/O, no broker).
> Broker-neutral even though v1 routing is IBKR-only. **POSITION_OPEN / EXIT_ONLY refer
> to a HYPOTHETICAL shadow position tracked by the evaluator — never a live broker
> position; the engine never reads or manages positions.db.**

## States

```text
HARD_DISABLED    dominant safety state (XAUUSD/XAGUSD); overrides every automatic state
DATA_INELIGIBLE  structurally failing (history/price/ADV/indicators/mapping/corp-action)
WATCHLIST        structurally eligible, still accruing the 2-pass entry hysteresis
ENTRY_ELIGIBLE   structurally eligible AND ≥2 consecutive passing sessions
POSITION_OPEN    hypothetical position open and still fully eligible
EXIT_ONLY        hypothetical position open but entries suppressed (exits continue)
COOLDOWN         post-exit cooldown (3 completed sessions)
ADMIN_PAUSED     operator off/paused, flat (blocks entries; not an open position)
```

## Dominance / rules (explicit precedence)

```text
1. HARD_DISABLED overrides everything (counters frozen at 0).
2. Open hypothetical position (and not exited this session):
     - admin paused/inactive OR loses structural eligibility → EXIT_ONLY
       (no entry, no reversal, no pyramiding; deterministic exit mgmt continues)
     - otherwise → POSITION_OPEN
   Ordinary eligibility failure NEVER forces liquidation (stays EXIT_ONLY).
3. Flat (no open position):
     - admin paused / not administratively_active → ADMIN_PAUSED
     - in cooldown (remaining > 0) → COOLDOWN
     - structurally eligible: ≥2 consecutive passes → ENTRY_ELIGIBLE, else WATCHLIST
     - structurally failing: DATA_INELIGIBLE
       (sticky: an ENTRY_ELIGIBLE instrument survives ONE failing session; removed on
        the 2nd consecutive failure)
```

## Hysteresis

```text
Entry:   2 consecutive structurally-passing completed sessions → ENTRY_ELIGIBLE.
Removal: 2 consecutive structurally-failing completed sessions → leave ENTRY_ELIGIBLE.
consecutive_passes increments on a pass (resets to 0 on a fail); consecutive_failures
the converse.
```

## Cooldown

A hypothetical exit (trend-break / stop) closes the position **this session** and sets
the post-exit cooldown to 3 sessions; the exit session counts as the first cooldown
session. The remaining count decrements each subsequent flat session; the instrument is
COOLDOWN while remaining > 0 and re-eligible when it reaches 0. (v1 counts in completed
evaluated sessions — no trading-calendar dependency.)

## Structural eligibility (inputs to the transition)

`bot/universe/eligibility.py` evaluates (task §7): ≥250 valid completed daily bars,
fresh completed bar, valid OHLC, required indicators available (SMA50/200, ATR14, ADX14,
20-day high), price ≥ $10, ADV20 ≥ $20M, valid research mapping, valid IBKR mapping, not
in cooldown, and corporate-action status. **If corporate-action data is unavailable a
reason code (`corp_action_data_unavailable`) is recorded and eligibility FAILS — never a
silent pass.** Unknown sector records `sector_unknown` (non-blocking) so the sector cap
reasons about it explicitly downstream.

## Reason codes

Every transition records reason codes (e.g. `hard_disabled`, `insufficient_history`,
`in_cooldown`, `sector_unknown`, `slot_cap_reached`, `sector_cap_reached`,
`portfolio_heat_exceeded`, `eligible`, `passed_entry_hysteresis`) into
`universe_state.reason_codes` and the append-only `universe_state_history`.
