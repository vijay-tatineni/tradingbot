# Dynamic Universe — Pre-Enable Remediation, Phase R1 / R1.1 Completion

> **Branch:** `feature/dynamic-universe-preenable-r1` (R1), continued on
> `feature/dynamic-universe-preenable-r1-fix1` (R1.1), from `breakout-strategy @
> 46b8f2571f32cf1deec71688cad230445bd25bdd`.
> **Scope:** the four mandatory state/position-safety pre-enable blockers
> **P3-2, P3-3, P3-8, P3-9** only. **The feature remains default-off, un-wired, broker-free,
> and not running. This work does NOT authorize enablement, wiring, deployment, or Phase R2.**
>
> **STATUS — IMPLEMENTED, NOT RESOLVED.** R1's P3-8/P3-9 attempt was found DEFECTIVE in
> independent review (an `UNKNOWN` observation erased the last authoritative open state, so
> an exit during a provider outage bypassed cooldown and re-enabled entry). **Phase R1.1**
> (below) re-implements P3-8/P3-9 with authoritative position continuity and adds content-aware
> idempotency conflict detection for P3-3. All items are IMPLEMENTED and tested but **await
> independent review** — none is marked RESOLVED on this branch. See the R1.1 section.

## Resolved blockers

### P3-2 — ambiguous `cooldown_until` semantics → explicit session-based fields
- **Schema (migration v2, additive):** `cooldown_started_trading_date`,
  `cooldown_sessions_remaining` (canonical count), `cooldown_last_counted_trading_date`,
  `cooldown_release_estimate` (DISPLAY-ONLY, left NULL — never authoritative without an
  approved exchange calendar).
- Runtime logic reads `cooldown_sessions_remaining`; the deprecated `cooldown_until` column
  is no longer read as the count (kept as compatibility metadata, inspected only to flag
  ambiguity). The state machine module contains no reference to `cooldown_until` at all.
- **Counting (no calendar dependency):** exit session E not counted; ≤1 decrement per
  COMPLETED session; weekend/holiday/missing-bar sessions, open positions, and UNKNOWN
  status never decrement; a duplicate same-date run never double-counts.
- **Ambiguous legacy row** (non-zero `cooldown_until`, NULL session field) → blocked
  manual-review `COOLDOWN` with `cooldown_legacy_ambiguous`; count is never inferred.

### P3-3 — non-atomic state + history writes → single transaction
- New `Registry.persist_transition_atomic(state, history)`: one explicit `BEGIN IMMEDIATE`
  (`isolation_level = None`, no `executescript`), history row inserted FIRST (its UNIQUE
  idempotency index gates duplicates), then the `universe_state` UPSERT, then `COMMIT`. A
  duplicate key or any error rolls BOTH writes back; a duplicate is an idempotent no-op
  (neither table changes → no double-advance). The evaluator persists every transition
  exclusively through this method.
- **Defence in depth:** v2 adds append-only `BEFORE UPDATE/DELETE` triggers on
  `universe_state_history`.

### P3-8 — missing provider preserved stale open state → authoritative UNKNOWN
- The legacy no-provider prior-state derivation is removed. Position status comes solely
  from an injected `PositionSnapshotProvider`. No provider / exception / timeout / malformed
  / unrecognised / stale → `UNKNOWN` (fail-safe): blocks new entry, never forces
  liquidation, never assumes flat, never decrements cooldown. A stale prior is kept only as
  descriptive history (`last_observed_position_status`).

### P3-9 — cooldown depended on transient `POSITION_EXITED_TODAY` → durable, exactly-once
- Exit is derived from an explicit durable `POSITION_EXITED` signal OR an authoritative
  `last_observed = POSITION_OPEN` → current `NO_POSITION` transition **with durable evidence**
  (`closed_trading_date` / `position_id`). De-duplicated by a durable
  `last_processed_position_event_id`: a replayed close never restarts cooldown; a genuinely
  new later close starts a fresh one. `UNKNOWN → NO_POSITION`, a no-evidence open→flat, and
  provider errors never start cooldown. `last_observed_position_status` is persisted every
  run (incl. UNKNOWN, and NO_POSITION after an exit) so `OPEN→UNKNOWN→NO_POSITION` cannot
  false-trigger. A new `PositionSnapshot` dataclass carries the (non-sensitive) evidence;
  the bare `PositionStatus` return is still accepted.

## Code changed (additive within `bot/universe/*`)
- `models.py` — `PositionSnapshot` dataclass; `POSITION_EXITED` status (+ deprecated
  `POSITION_EXITED_TODAY`); `StateOutcome.cooldown_started/cooldown_counted`;
  `COOLDOWN_LEGACY_AMBIGUOUS` reason.
- `migrations.py` — migration v2 (additive columns + append-only triggers).
- `state_machine.py` — `exit_detected` / `cooldown_session_countable` inputs; emits cooldown
  bookkeeping flags. No reference to `cooldown_until`.
- `registry.py` — `persist_transition_atomic`; `upsert_state` writes v2 columns.
- `evaluator.py` — §5 sequence; authoritative `PositionSnapshot` resolution; durable
  exactly-once exit derivation; session-based cooldown bookkeeping; single atomic persist.

**Deliberately untouched (Phase R2):** candidate-source selection (P3-4), portfolio-heat
seeding (P3-5), canonical-ID construction (P3-6), IBKR verification-status check (P3-7,
`ibkr_ok`), FX-normalized sizing (BLOCKER-S). Layer 1, breakout logic, risk parameters, and
broker adapters are unchanged.

## Tests
- New: `test_atomic_persistence.py` (P3-3 — fault injection at each seam, rollback leaves
  both tables unchanged, idempotent replay, concurrent-writer serialisation, exactly-one
  history row, append-only triggers); `test_exit_detection.py` (P3-9); `test_cooldown_sessions.py`
  (P3-2); `test_position_authority.py` (P3-8); `test_r1_migration.py` (v1→v2 additive upgrade,
  ambiguous-legacy fail-safe, session-field source-of-truth guard, v2 rollback atomicity).
- Updated: `test_state_machine.py`, `test_evaluator.py`, `test_position_lifecycle.py`,
  `test_migrations.py`, `test_no_live_integration.py` (flag-off zero provider calls + zero DB
  writes, no config rewrites), and the offline `rehearsal.py` (durable open→flat exit).
- **Focused:** `pytest tests/universe` → **167 passed**.
- **Full:** `pytest tests` → **1499 passed, 4 failed**; the 4 failures are exactly the
  pre-existing `tests/test_breakout_indicators.py` isolation failures (empty/ambient
  `backtest.db` → empty dataframe `iloc` `IndexError`), byte-identical to the merged base —
  no new failure outside that file.

---

# Phase R1.1 — Authoritative Position Continuity and Idempotency Conflicts

> **Branch:** `feature/dynamic-universe-preenable-r1-fix1` (from R1 head `5da3e6e`).
> Corrects the six independent-review findings against R1. Still additive within
> `bot/universe/*`; default-off, un-wired, not running.

## Root cause of the R1 defect
R1 stored a single `last_observed_position_status` and used it both as the "latest seen"
status and as the exit-detection anchor. A non-authoritative `UNKNOWN` observation
overwrote it, erasing the authoritative open state. So `OPEN → UNKNOWN → NO_POSITION`
(an exit during an outage) was missed — cooldown bypassed, instrument re-eligible. R1 also
accepted a bare `position_id` as closure evidence, and silently accepted a conflicting
duplicate transition as an idempotent no-op.

## What R1.1 changes (by review finding)
- **Finding 1 & 2 — UNKNOWN erased authoritative state / no-evidence flat assumed flat
  (schema v3, additive):** separate `latest_observed_position_status` + `latest_observed_at`
  (a non-authoritative observation may overwrite these) from `last_authoritative_position_status`
  + `last_authoritative_position_id_hash` + `last_authoritative_observed_at` (which an
  UNKNOWN/stale/future/missing observation NEVER erases). Exit detection keys off the durable
  authoritative open. An authoritative `OPEN→flat` **without** explicit closure evidence sets
  a persistent `position_reconciliation_required` block (state `EXIT_ONLY` + reason
  `position_reconciliation_required`): blocks new entry, never forces liquidation, never
  assumes flat, holds cooldown, persists across evaluations; cleared only by an authoritative
  `POSITION_OPEN` or an evidence-bearing close.
- **Authoritative classification:** only `POSITION_OPEN` / `NO_POSITION` / `POSITION_EXITED`
  (and honoured-deprecated `POSITION_EXITED_TODAY`) from a fresh, in-order snapshot are
  authoritative. UNKNOWN, exception/timeout, malformed, future-dated, stale (older than
  `MAX_POSITION_SNAPSHOT_STALENESS_DAYS`=3), or out-of-order observations are non-authoritative.
- **Finding 4 — bare `position_id` as closure evidence:** closure now requires
  `closed_trading_date` / `close_event_id` / `explicitly_closed`; a bare `position_id` is NOT
  proof. (`PositionSnapshot` gained `close_event_id`, `explicitly_closed`.)
- **Exactly-once close (P3-9):** de-duped by `last_processed_position_event_id` with a
  stale-close guard (a close older than the last processed close / authoritative observation
  is ignored, never restarts a newer lifecycle).
- **Finding 3 — silent conflicting replay (P3-3):** `persist_transition_atomic` now does
  content-aware reconciliation INSIDE the transaction — identical replay → idempotent no-op;
  divergent `new_state`/hash/cooldown/close-event/authoritative markers →
  `TransitionConflictError`; history-without-state or state/history divergence →
  `StateHistoryConsistencyError`. Never a silent no-op, never a silent repair. The
  `universe_state` write path is built from a single `_STATE_COLUMNS` registry shared with
  `upsert_state` (lockstep — no column can be silently dropped).
- **Finding 5 — coverage gap:** added `tests/universe/test_position_continuity.py` covering
  the multi-step outage sequences that R1's single-step tests missed.
- **Finding 6 — false RESOLVED labels:** documentation now marks P3-8/P3-9/P3-3 as
  **IMPLEMENTED — awaiting independent review**, never RESOLVED, on this branch.

## Schema v3 (additive; head `user_version` = 3)
`latest_observed_position_status`, `latest_observed_at`, `last_authoritative_position_status`,
`last_authoritative_position_id_hash`, `last_authoritative_observed_at`,
`position_reconciliation_required`. A conservative back-fill sets
`position_reconciliation_required=1` for any pre-existing v2 row whose only position memory is
an `UNKNOWN` observation (no reconstructable authoritative state) — block, do not guess. The
`UPDATE` is on `universe_state` only (does not touch the append-only history triggers).

## Self-check probes (final, read-back asserted)
- `OPEN → UNKNOWN → NO_POSITION (+closed_trading_date)` → **COOLDOWN once**, remaining 3,
  event recorded; `last_authoritative_position_status` stayed `POSITION_OPEN` through the
  `UNKNOWN`.
- `OPEN → UNKNOWN → NO_POSITION (no evidence)` → **EXIT_ONLY + position_reconciliation_required**,
  no cooldown, not entry-eligible.
- bare `position_id` only → **reconciliation required**, no cooldown.
- conflicting duplicate (different cooldown/new_state/hash/close-event) →
  **TransitionConflictError**; history/state divergence → **StateHistoryConsistencyError**.

## Tests
- New: `tests/universe/test_position_continuity.py` (outage continuity, closure-evidence
  contract, observation ordering/freshness, reconciliation persistence + clearing,
  EXITED_TODAY back-compat). Extended: `test_atomic_persistence.py` (conflict / consistency /
  marker-rollback), `test_r1_migration.py` (v3 reconciliation back-fill; renamed head-version
  tests). Updated for the v2→v3 head bump and the authoritative/latest split.
- **Focused:** `pytest tests/universe` → **187 passed**.
- **Full:** `pytest tests` → **1519 passed, 4 failed** — the 4 failures are exactly the
  pre-existing `tests/test_breakout_indicators.py` ambient-DB isolation failures
  (byte-identical to base; the diff touches only `bot/universe/*`, `docs/*`,
  `tests/universe/*`) — no new failure outside that file.

## Safety posture (unchanged)
`enable_dynamic_universe_shadow` is still `False` by default; the package is imported by
neither `main.py` nor `api_server.py`; no scheduler is wired; no production `universe.db` is
created and no production migration is run; no broker / IG / EODHD call occurs; no live DB,
config, or service is changed. Phase R2 and any runtime wiring remain out of scope and
require separate review and sign-off.
