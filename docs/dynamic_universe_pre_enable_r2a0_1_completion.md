# Pre-Enable R2A-0.1 — completion (independent-review corrections)

> **Status:** `IMPLEMENTED — awaiting independent review`. Default-off, un-wired, broker-free,
> provider-injected, additive. Branch `feature/dynamic-universe-preenable-r2a0-fix1` from
> `feature/dynamic-universe-preenable-r2a0 @ b5445ae`. **NOT a resolved/enablement sign-off.**
> P3-R1-A/B/C remain OPEN; nothing here authorizes runtime enablement, scheduler wiring,
> production migration, shadow soak, paper/live trading, or R2A-1+.

R2A-0.1 is a narrowly-scoped corrective round closing the three P2 findings of the R2A-0
independent review. No new scope (no identity/candidate/heat/FX work).

## Operator ruling applied (Finding 1)

The R2A-0 safety requirement **supersedes** the earlier R1.3 premise that an explicit
`close_event_id` alone may start cooldown. Frozen rule:

> An explicit `close_event_id` is preferred, but it is **not** by itself sufficient lifecycle
> evidence.

A close is processed (cooldown started, event marked processed) **only** when the evaluator can
form a valid lifecycle-qualified close key containing: canonical instrument id, hashed position
id, a **valid** `opened_trading_date`, a **valid** `closed_trading_date`, and the provider
`close_event_id` when supplied. If any required lifecycle field is missing or malformed → fail
closed: `POSITION_RECONCILIATION`, entry blocked, no cooldown started, no processed-event marker
written, authoritative OPEN anchor retained. No `?`/empty/raw-malformed/guessed substitution.

## Changes

### 1. Lifecycle-qualified event handling (Finding 1)
- New deterministic, versioned key (`bot/universe/evaluator._qualified_close_key`):
  ```
  close-key:v2:<canonical_instrument_id>|<position_id_hash>|<opened_iso>|<closed_iso>|<explicit_close_event_id or '->'
  ```
  persisted in `universe_state.last_close_event_key` and folded into the immutable transition
  snapshot/hash.
- `_lifecycle_close` builds the key only from complete, valid evidence; otherwise returns `None`
  and the caller fails closed. Behavior:
  - same explicit id + same complete lifecycle → replay (no cooldown reset);
  - same explicit id + different opened **or** closed date **or** position hash → provider-contract
    violation → `POSITION_RECONCILIATION`;
  - explicit id + missing opened **or** missing closed date → reconciliation;
  - missing position id → reconciliation.
- A violation/insufficient close **never** starts cooldown, marks the event processed, overwrites
  the authoritative OPEN anchor, or permits entry; the reconciliation requirement is persisted
  atomically with the rest of the transition (`persist_transition_atomic`).
- `PositionSnapshot.close_event_id` documents the global-uniqueness contract **and** that it is
  not relied upon alone for safety.

### 2. Strict date validation (Finding 2)
- `_valid_lifecycle_date` accepts only a `datetime.date`/`datetime` or a strictly-parsed ISO
  `YYYY-MM-DD` string. Rejected (→ reconciliation): malformed/empty strings, impossible dates,
  unexpected types, future `opened`, future `closed`, and `closed` earlier than `opened`. No
  permissive `str()` coercion; `_stale_close`/parse handlers can no longer turn malformed input
  into a valid exit.

### 3. Fail-closed v3→v4 migration (Finding 3)
- The v4 migration now, after adding `last_close_event_key`, runs (atomically, in the same
  `BEGIN IMMEDIATE`):
  ```sql
  UPDATE universe_state SET position_reconciliation_required = 1
   WHERE last_processed_position_event_id IS NOT NULL AND last_close_event_key IS NULL
  ```
  A pre-v4 row that already processed a close but has no qualified key is blocked for
  reconciliation rather than risking a masked reuse. No qualified key is inferred from incomplete
  legacy data. On the next evaluation the row resolves to `POSITION_RECONCILIATION` (entry
  blocked); a fresh authoritative OPEN clears it, or a valid lifecycle-qualified close reconciles
  it into `COOLDOWN`. Additive, atomic, idempotent; a fresh v1→v4 DB has no such rows (no-op).
  **v1–v3 DDL unchanged.**

### 4. NULL-hash policy preserved (no weakening)
- P3-R1-B is intact: advanced replay of a NULL `transition_snapshot_hash` row still raises
  `StateHistoryConsistencyError`; all supported writers still write a complete non-NULL
  transition snapshot/hash.

## Tests

- New `tests/universe/test_r2a0_1_lifecycle.py` (20 tests): explicit-id full lifecycle → cooldown
  once; explicit-id missing opened/closed → reconciliation; missing position id → reconciliation;
  same explicit id with different opened/closed/pid → violation (+ same-lifecycle replay guard);
  malformed/empty/closed-before-opened/future opened/future closed → reconciliation; v3
  processed-event row → v4 `reconciliation_required=1` (key stays NULL); clean v3 row not blocked;
  migrated row cannot enter; migrated row cleared by authoritative OPEN; migrated row reconciled
  into COOLDOWN by a valid close; persist-level conflict on a divergent `last_close_event_key`.
  Tests assert returned state, reason codes, reconciliation flag, authoritative anchor, cooldown
  values, processed event id, `last_close_event_key`, and persisted state — not only the reason.
- Pre-existing R1.1/R1.3 cooldown tests were updated to supply the now-required full valid
  lifecycle (added `position_id`/`opened`); **no assertion weakened or removed** — only required
  input fields added, per the operator ruling.
- `pytest tests/universe` → **250 passed**. `pytest tests` → **4 failed, 1582 passed**; the 4 are
  the pre-existing `tests/test_breakout_indicators.py` baseline (identical at
  `breakout-strategy @ 550fa625`). No new failure outside that file.

## Posture (unchanged)
Default-off (`enable_dynamic_universe_shadow=False`, absent from live config); `bot.universe`
un-wired (not imported by `main.py`/`api_server.py`); broker-free (injected stubs); no production
`universe.db`; no migration/restart/broker call/runtime action; R2A-1/R2B/R2C untouched and open.

## Status
Findings 1–3: **IMPLEMENTED — awaiting independent review** (P3-R1-A/B/C NOT marked resolved).
