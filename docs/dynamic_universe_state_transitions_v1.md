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
decremented. On each subsequent confirmed-flat **completed** session it is decremented
(check-then-decrement). While a hypothetical position is still open — OR while the position
status is UNKNOWN (cannot confirm flat) — the count is **held**, never advanced. (v1 counts
in completed evaluated sessions — no trading-calendar dependency.)

**Session-based fields (P3-2, R1).** The count lives in
`universe_state.cooldown_sessions_remaining` (canonical), with
`cooldown_started_trading_date` (= E) and `cooldown_last_counted_trading_date`. The
deprecated `cooldown_until` column is no longer read by runtime logic (it stored a count
despite its date-implying name); it remains as deprecated compatibility metadata. A session
counts at most once (`cooldown_last_counted_trading_date` guards a duplicate same-date run)
and only when a completed bar exists — a **weekend / holiday / missing-bar session never
counts** (no calendar lookup; absence of a completed bar is the signal). The display-only
`cooldown_release_estimate` is left NULL: it is never authoritative without an approved
exchange calendar. An ambiguous legacy row (non-zero `cooldown_until`, NULL session field)
fails safe to a blocked/manual-review `COOLDOWN` (`cooldown_legacy_ambiguous`); the count is
never inferred from the legacy value.

Because `in_cooldown` is itself a blocking structural reason, the 2-pass entry
hysteresis counter resets during cooldown. So at E+4 the instrument leaves COOLDOWN into
WATCHLIST and must re-accrue two passing sessions (ENTRY_ELIGIBLE at E+5). The cooldown
*block* is fully released at E+4 — that is the §2 invariant; the re-accrual is the
ordinary entry hysteresis, applied conservatively.

## Position status seam (task §3 / P3-8 / P3-9, R1)

POSITION_OPEN / EXIT_ONLY / COOLDOWN are driven by an INJECTED, broker-free
`PositionSnapshotProvider` returning a `PositionStatus` **or** a richer `PositionSnapshot`
(status + durable exit evidence: `position_id`, `opened_/closed_trading_date`,
`source_version` — never account ids / quantities / prices). The provider never calls a
broker.

**Authoritative contract (P3-8):** there is NO legacy prior-state fallback. When **no
provider** is injected — or the provider raises / times out / returns a malformed,
unrecognised, or stale/absent value — the status is `UNKNOWN` (fail-safe). A stale prior
state is preserved only as descriptive history (`last_observed_position_status`), never as
proof of a current position.

```text
NO_POSITION            → flat branch (WATCHLIST/ENTRY_ELIGIBLE/COOLDOWN/…)
POSITION_OPEN          → POSITION_OPEN (or EXIT_ONLY if eligibility lost / admin paused)
POSITION_EXITED        → durable exit signal → COOLDOWN (exit on E)
POSITION_EXITED_TODAY  → DEPRECATED transient exit signal (still honoured) → COOLDOWN
UNKNOWN                → EXIT_ONLY, reason position_status_unknown; never entry-eligible,
                         never forced liquidation, cooldown held (do not assume flat)
no provider / error    → UNKNOWN (as above) — never a retained stale open
```

**Durable, exactly-once exit (P3-9):** cooldown no longer depends on observing the transient
`POSITION_EXITED_TODAY`. The evaluator starts cooldown when it sees an explicit durable
exit signal, OR an authoritative `last_observed = POSITION_OPEN` → current `NO_POSITION`
transition **with durable evidence** (`closed_trading_date` or `position_id`). A
no-evidence open→flat, `UNKNOWN → NO_POSITION`, or a provider error does **not** start
cooldown. A durable `last_processed_position_event_id` de-duplicates a replayed close (no
restart); a genuinely new later close starts a fresh cooldown. `last_observed_position_status`
is persisted every run (including `UNKNOWN`, and `NO_POSITION` after an exit) so
`OPEN→UNKNOWN→NO_POSITION` cannot false-trigger.

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
