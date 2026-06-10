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

## Cooldown (FROZEN — task §2)

A hypothetical exit closes the position on session **E**. The exit session itself does
**NOT** count as a completed post-exit cooldown session. The instrument is blocked for
the next **three completed sessions** and the earliest re-eligibility *evaluation* is
E+4:

```text
E      position exits            → COOLDOWN, cooldown_remaining = 3 (not decremented)
E+1    still COOLDOWN            → cooldown_remaining = 2
E+2    still COOLDOWN            → cooldown_remaining = 1
E+3    still COOLDOWN (last)     → cooldown_remaining = 0
E+4    cooldown block released   → may transition out of COOLDOWN if all else passes
```

On the exit session the count is set to `COOLDOWN_SESSIONS` (3) and is **not**
decremented. On each subsequent confirmed-flat session it is decremented (check-then-
decrement). While a hypothetical position is still open — OR while the position status
is UNKNOWN (cannot confirm flat) — the count is **held**, never advanced. (v1 counts in
completed evaluated sessions — no trading-calendar dependency.)

Because `in_cooldown` is itself a blocking structural reason, the 2-pass entry
hysteresis counter resets during cooldown. So at E+4 the instrument leaves COOLDOWN into
WATCHLIST and must re-accrue two passing sessions (ENTRY_ELIGIBLE at E+5). The cooldown
*block* is fully released at E+4 — that is the §2 invariant; the re-accrual is the
ordinary entry hysteresis, applied conservatively.

## Position status seam (task §3)

POSITION_OPEN / EXIT_ONLY / COOLDOWN are driven by an INJECTED, broker-free
`PositionSnapshotProvider` returning one of `NO_POSITION`, `POSITION_OPEN`,
`POSITION_EXITED_TODAY`, `UNKNOWN`. The provider never calls a broker. When no provider
is injected the evaluator falls back to the legacy prior-state + trend-break derivation.

```text
NO_POSITION            → flat branch (WATCHLIST/ENTRY_ELIGIBLE/COOLDOWN/…)
POSITION_OPEN          → POSITION_OPEN (or EXIT_ONLY if eligibility lost / admin paused)
POSITION_EXITED_TODAY  → COOLDOWN (exit on E)
UNKNOWN                → EXIT_ONLY, reason position_status_unknown; never entry-eligible,
                         never forced liquidation, cooldown held (do not assume flat)
```

`UNKNOWN` is fail-safe: a provider error or any unrecognised value is coerced to UNKNOWN.

## Structural eligibility (inputs to the transition)

`bot/universe/eligibility.py` evaluates (task §7): ≥250 valid completed daily bars,
fresh completed bar, valid OHLC, required indicators available (SMA50/200, ATR14, ADX14,
20-day high), price ≥ $10, ADV20 ≥ $20M, valid research mapping, valid IBKR mapping, not
in cooldown, and corporate-action status. Unknown sector records `sector_unknown`
(non-blocking) so the sector cap reasons about it explicitly downstream.

### Corporate-action policy (FROZEN — task §4)

`structural_eligibility(snapshot, mode)` applies one of two distinct, never-confused
policies for an **unknown / unavailable** corporate-action status:

```text
Shadow (ELIGIBILITY_MODE_SHADOW):       WARNING, not blocking.
                                        Records `corporate_action_status_unknown`;
                                        the instrument stays structurally eligible so
                                        scheduling, state changes, contention and
                                        logging can be exercised operationally.

Paper/live (ELIGIBILITY_MODE_PAPER_LIVE, the DEFAULT / fail-closed):
                                        HARD BLOCK for new entries.
                                        Records `corp_action_data_unavailable`.
```

The default mode is the **fail-closed** paper/live policy: a forgotten or wrong `mode`
argument blocks rather than silently permits. The shadow evaluator opts in to the
shadow policy explicitly. The two reason codes are disjoint, so the policies can never
be conflated (asserted in `tests/universe/test_eligibility.py`). **Neither mode is ever
a silent pass** — the unknown is always recorded. A detected `anomaly` (a known
problem, not an unknown) BLOCKS in **both** modes. The paper/live policy is a tested
code path only; it is **not** wired or enabled in this task.

## Reason codes

Every transition records reason codes (e.g. `hard_disabled`, `insufficient_history`,
`in_cooldown`, `sector_unknown`, `corporate_action_status_unknown`,
`corp_action_data_unavailable`, `position_status_unknown`, `slot_cap_reached`,
`sector_cap_reached`, `portfolio_heat_exceeded`, `eligible`, `passed_entry_hysteresis`)
into `universe_state.reason_codes` and the append-only `universe_state_history`.
