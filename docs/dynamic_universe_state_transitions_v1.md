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
EXIT_ONLY        position AUTHORITATIVELY open but entries suppressed (exits continue)
POSITION_RECONCILIATION  position ownership UNRESOLVED — blocks entry, asserts nothing (R1.2)
COOLDOWN         post-exit cooldown (3 completed sessions)
ADMIN_PAUSED     operator off/paused, flat (blocks entries; not an open position)
```

`POSITION_RECONCILIATION` (R1.2 / P2-C) is a dedicated state for position *uncertainty*.
It replaces the R1.1 overloading of `EXIT_ONLY` for this case. It is entered when either:
the last authoritative status was `POSITION_OPEN` and the position is now reported flat
without durable closure evidence (the durable `position_reconciliation_required` flag, reason
`position_reconciliation_required`); OR the current observation is non-authoritative —
`UNKNOWN` / error / stale / future / missing provider (reason `position_status_unknown`). It
blocks all new entry, NEVER asserts a position exists or is flat, never forces liquidation,
never decrements cooldown, and (for the durable flag) persists until an authoritative
`POSITION_OPEN` or an evidence-bearing close clears it. `EXIT_ONLY` is now reserved strictly
for a position that AUTHORITATIVELY exists (current authoritative snapshot is `POSITION_OPEN`)
with entries suppressed — it never represents uncertainty.

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

## Position status seam (task §3 / P3-8 / P3-9, R1.1)

POSITION_OPEN / EXIT_ONLY / COOLDOWN are driven by an INJECTED, broker-free
`PositionSnapshotProvider` returning a `PositionStatus` **or** a richer `PositionSnapshot`
(status + durable exit evidence: `position_id`, `opened_/closed_trading_date`,
`close_event_id`, `explicitly_closed`, `observed_at`, `source_version` — never account ids /
quantities / prices). The provider never calls a broker.

**Authoritative vs latest (P3-8, R1.1).** Only `POSITION_OPEN` / `NO_POSITION` /
`POSITION_EXITED` (+ honoured-deprecated `POSITION_EXITED_TODAY`) from a FRESH, in-order,
non-future snapshot are *authoritative*. `UNKNOWN`, a provider exception/timeout, a malformed
/ unrecognised value, a stale (> `MAX_POSITION_SNAPSHOT_STALENESS_DAYS`) or out-of-order
observation, and a missing provider are *non-authoritative* → resolved as `UNKNOWN`
(fail-safe). The evaluator persists the **latest** observation (`latest_observed_*`, which a
non-authoritative observation MAY overwrite) separately from the **last authoritative**
evidence (`last_authoritative_*`, which a non-authoritative observation NEVER erases). There
is no legacy prior-state fallback.

```text
POSITION_OPEN          → POSITION_OPEN (or EXIT_ONLY if eligibility lost / admin paused);
                         clears any reconciliation block; refreshes authoritative evidence
NO_POSITION (auth.)    → if last authoritative was OPEN and durable closure evidence present
                         → COOLDOWN (exit on E); if OPEN without evidence → reconciliation;
                         else ordinary flat branch (WATCHLIST/ENTRY_ELIGIBLE/…)
POSITION_EXITED        → durable exit signal → COOLDOWN (exit on E)
POSITION_EXITED_TODAY  → DEPRECATED transient exit signal (still honoured) → COOLDOWN
UNKNOWN / non-auth.    → POSITION_RECONCILIATION (R1.2), reason position_status_unknown;
                         never entry-eligible, never forced liquidation, cooldown held;
                         authoritative evidence kept
reconciliation block   → POSITION_RECONCILIATION (R1.2), reason
                         position_reconciliation_required (durable)
```

> **R1.2 (P2-C) change:** position *uncertainty* — both the `UNKNOWN`/non-authoritative case
> and the durable `position_reconciliation_required` block — now resolves to the dedicated
> `POSITION_RECONCILIATION` state, not `EXIT_ONLY`. `EXIT_ONLY` is reserved for a position
> that authoritatively exists. Consumers must NOT read `EXIT_ONLY` as "a position may not
> exist"; uncertainty is `POSITION_RECONCILIATION`.

**Durable, exactly-once exit (P3-9, R1.1).** Cooldown does NOT depend on the transient
`POSITION_EXITED_TODAY`. It starts when the evaluator sees an explicit durable exit signal,
OR an authoritative `last_authoritative_position_status == POSITION_OPEN` → current
`NO_POSITION` transition **with EXPLICIT closure evidence** (`closed_trading_date` /
`close_event_id` / `explicitly_closed`; a bare `position_id` is **not** evidence). A
no-evidence open→flat sets `position_reconciliation_required` (no cooldown, no flat
assumption). `UNKNOWN → NO_POSITION` and provider errors never start cooldown. Because the
authoritative anchor survives a `UNKNOWN` outage, an exit during the outage
(`OPEN → UNKNOWN → NO_POSITION+evidence`) IS detected — exactly once, de-duplicated by
`last_processed_position_event_id` (with a stale-close guard so an older close never restarts
a newer lifecycle); a genuinely new later close starts a fresh cooldown.

**Strict lifecycle-qualified close identity + cooldown sufficiency (R2A-0.1; supersedes R1.3 /
Finding 1).** Recognising the exit is necessary but NOT sufficient to start cooldown — the
evaluator must also form a lifecycle-qualified close key so a provider that reuses a
`close_event_id` (or a `position_id`) across distinct lifecycles cannot mask a genuine second
exit. **A close is processed ONLY with COMPLETE, VALID lifecycle evidence: a `position_id` (→
hash), a valid `opened_trading_date` AND a valid `closed_trading_date`, with `opened <= closed <=
evaluation date` and `opened` not in the future, plus the `close_event_id` when supplied.** An
explicit `close_event_id` is PREFERRED but is NOT by itself sufficient (R2A-0.1 operator ruling —
this supersedes the earlier R1.3 premise that an explicit id alone could start cooldown). The
qualified key is deterministic and versioned:
`close-key:v2:<canonical_id>|<position_id_hash>|<opened_iso>|<closed_iso>|<close_event_id or '->'`,
persisted in `universe_state.last_close_event_key`. Outcomes:

* same explicit id + same complete lifecycle → replay (no cooldown reset);
* same explicit id + a DIFFERENT opened/closed date or position hash → **provider-contract
  violation** → `POSITION_RECONCILIATION`;
* any missing or malformed required field (no position id, no/invalid opened, no/invalid closed,
  `closed` before `opened`, future `opened`/`closed`) → `POSITION_RECONCILIATION`.

A violation/insufficient close does NOT start cooldown, mark the event processed, overwrite the
authoritative OPEN anchor, or permit entry. Lifecycle dates are strictly validated (only a
`date`/`datetime` or strict ISO `YYYY-MM-DD`); no permissive `str()` coercion of malformed
values. The position id is hashed and the key version-prefixed; no raw account id or unredacted
identifier is ever embedded.

**Complete-content replay integrity (R1.3 / Finding 2).** Each persisted transition stores a
deterministic `transition_snapshot_json` + `transition_snapshot_hash` (in the append-only
history row) covering ALL material outputs — prior/new state, sorted reason codes, feature
hash, cooldown bookkeeping, lifecycle/authoritative/reconciliation markers. The content-aware
idempotency check compares that hash, so a divergent replay (e.g. a different
`cooldown_sessions_remaining`) raises `TransitionConflictError` EVEN when the current state has
legitimately advanced past the replayed date; an exact replay remains an idempotent no-op.

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

## R2A-1 pre-entry identity / mapping gate reason codes (P3-6 / P3-7)

The default-off `enforce_verified_identity` gate adds deterministic, FAIL-CLOSED pre-entry
checks (identity verified? active listing verified? IBKR mapping `VERIFIED_REFERENCE_MATCH`
and fresh?). A failing gate folds its blocking reason code(s) into structural eligibility so
the instrument cannot advance to `ENTRY_ELIGIBLE`. The gate **only blocks NEW entry** — it
never forces liquidation, never alters an existing position, and never calls a broker; the
`HARD_DISABLED` → `POSITION_RECONCILIATION` → `POSITION_STATUS_UNKNOWN` position-safety
precedence still dominates the gate.

Reason codes (all in `BLOCKING_REASONS`):

| code | meaning |
|------|---------|
| `identity_unverified` | no verified ISIN/FIGI anchor / unresolved canonical row |
| `identity_ambiguous` | provider returned an ambiguous/unclear match |
| `identity_conflict` | asserted anchor conflicts with stored identity (never merged) |
| `listing_unverified` | no active verified venue/currency listing |
| `ibkr_mapping_unverified` | mapping status `UNVERIFIED` / missing |
| `ibkr_mapping_not_reference_verified` | status `VERIFIED_CONFIGURED` (not reference-matched) |
| `ibkr_mapping_stale` | reverify deadline passed, future `verified_at`, or no freshness window |
| `ibkr_mapping_rejected` | status `REJECTED` |
| `ibkr_mapping_ambiguous` | status `AMBIGUOUS` |
| `ibkr_mapping_mismatch` | missing conId, or currency/MIC/exchange disagree with the listing |
| `ig_order_routing_blocked` | IG routing is unconditionally blocked in this tranche |
