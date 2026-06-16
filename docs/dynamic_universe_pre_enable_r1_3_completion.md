# Dynamic Universe — Pre-Enable Remediation, Phase R1.3 Completion

> **Branch:** `feature/dynamic-universe-preenable-r1-fix3`, from
> `feature/dynamic-universe-preenable-r1-fix2 @ 73d305fe5220fb5f13e2b5f3f6ad50ec161de9d2`
> (based on `breakout-strategy @ 46b8f2571f32cf1deec71688cad230445bd25bdd`).
> **Scope:** exactly the two **P2** findings and the **P3** coverage gap from the independent
> R1.2 review — **Finding 1, Finding 2, Finding 3**. Nothing else.
>
> **STATUS — IMPLEMENTED, NOT RESOLVED.** All corrections are implemented and tested; they
> **await independent review**. **The feature remains default-off, un-wired, broker-free, and
> not running.** This work does NOT authorize enablement, scheduler wiring, shadow soak,
> production migration, production `universe.db`, paper/live trading, service restart, or
> Phase R2. P3-3 / P3-8 / P3-9 stay `IMPLEMENTED — awaiting independent review`; P3-4 / P3-5 /
> P3-6 / P3-7 / FX-normalized sizing remain OPEN for R2.

## Finding 1 — lifecycle-safe close-event identity

- **Defect (review):** the synthetic close-event id was keyed only on `(position_id_hash,
  closed_trading_date)`. A provider reusing a `position_id` across distinct lifecycles that
  close on the same trading date produced colliding ids → the second genuine exit was masked
  (`exit_detected=False`) → no cooldown (a cooldown bypass).
- **Fix (`bot/universe/evaluator.py`, `bot/universe/models.py`):**
  - **Preferred identity:** an explicit provider-supplied `close_event_id` (documented as the
    strongest identity; the provider owns uniqueness).
  - **Fallback identity:** `_synth_close_event_id(canonical_id, position_id_hash,
    opened_trading_date, closed_trading_date)` — a `close:v2`-prefixed sha256. The
    `opened_trading_date` is the lifecycle discriminator, so a reused `position_id` closing on
    the same date in a DIFFERENT lifecycle (different open date) still gets a DISTINCT id. The
    canonical instrument id is keyed in, so different instruments never collide. No raw account
    id or unredacted identifier is embedded.
  - **Missing lifecycle discriminator:** a close with NEITHER an explicit `close_event_id` NOR
    an `opened_trading_date` is AMBIGUOUS → the evaluator does NOT synthesize a collision-prone
    id, does NOT mark the close processed, does NOT start cooldown, does NOT permit entry; it
    sets/retains `position_reconciliation_required` and routes to `POSITION_RECONCILIATION`.
    The old `(pid_hash, closed)`-only fallback is removed.
- **Sufficiency-contract change (owned):** this NARROWS what counts as cooldown-starting
  closure evidence. Cooldown now requires `close_event_id` OR (`opened_trading_date` +
  `closed_trading_date`). `closed_trading_date`-alone, `explicitly_closed`-alone, and a BARE
  `POSITION_EXITED`/`POSITION_EXITED_TODAY` now route to `POSITION_RECONCILIATION` instead of
  starting cooldown. The §4-preserved "with evidence → COOLDOWN" scenarios are kept by
  supplying a lifecycle discriminator; the evidence-matrix test was split into
  with-discriminator (→ COOLDOWN) and without (→ POSITION_RECONCILIATION) to ENCODE the new
  contract. The `models.py` closure-evidence docstring and the state-transitions doc were
  updated accordingly. The rule is applied UNIFORMLY (incl. exit-signal statuses) — carving
  exit signals out would leave the same collision on that path.
- **Required behavior (verified):** same lifecycle + same event → replay no reset; reused
  `position_id` + same close date + different open date → distinct id → second cooldown
  starts; explicit `close_event_id` takes precedence; missing id + missing open date →
  POSITION_RECONCILIATION (entry blocked); different instruments with same pid/dates → distinct
  ids.

## Finding 2 — complete immutable transition content for advanced replay

- **Defect (review):** the advanced-replay regime compared only the immutable history fields +
  `feature_snapshot_hash`. Cooldown bookkeeping is not in the feature hash, so a replay with
  identical state+hash but divergent `cooldown_sessions_remaining` was a silent no-op instead
  of a `TransitionConflictError`.
- **Fix (`bot/universe/registry.py`, `bot/universe/migrations.py`):** option **B** — a
  registry-owned, deterministic `transition_snapshot_json` + `transition_snapshot_hash` is
  computed from the (state, history) pair and stored in two new `universe_state_history`
  columns. The snapshot covers ALL material outputs: `prior_state`, `new_state`, sorted
  `reason_codes`, `feature_snapshot_hash`, `cooldown_started_trading_date`,
  `cooldown_sessions_remaining`, `cooldown_last_counted_trading_date`,
  `last_processed_position_event_id`, `last_position_close_trading_date`,
  `latest_observed_position_status`, `latest_observed_at`, `last_authoritative_position_status`,
  `last_authoritative_position_id_hash`, `last_authoritative_observed_at`,
  `position_reconciliation_required`. `_reconcile_duplicate` now decides idempotency on that
  hash in BOTH regimes, so a divergent replay is detected even after the current state has
  legitimately advanced. **Structural consistency/ordering checks run FIRST** (current-state
  missing / behind the history row / same-date `current_state` ≠ stored `new_state` →
  `StateHistoryConsistencyError`), so an incoherent pair is never masked by an identical hash.
- **Deterministic serialization:** built from a FIXED field list (never `**state`); dates →
  ISO strings inside the builder (a `date` object on write and an ISO string on replay hash
  identically); `reason_codes` sorted; wall-clock fields (`evaluated_at`) excluded; the one
  builder feeds both the write path and the reconcile recompute so they cannot drift. A
  legacy/`append_history` row with a NULL stored transition hash falls back to the field
  comparison (NULL is never treated as a match).

## Finding 3 — migration test coverage

Added tests: `latest_observed_at` column presence; the two `transition_snapshot_*` history
columns; all six v3 authoritative/reconciliation columns; the `POSITION_EXITED_TODAY` v2 row
migrating to a clean `NO_POSITION` anchor; and an active-cooldown + `position_reconciliation_required`
no-decrement safety test. Migration idempotent-rerun and atomic-rollback coverage retained.

## Schema / migration decision

v3 corrected **IN PLACE** (it is unreleased / never merged / never run in production); the two
`universe_state_history` columns are added via `ALTER TABLE … ADD COLUMN` (DDL — the v2
append-only `BEFORE UPDATE/DELETE` triggers do not fire). **No schema v4** is introduced. The
migration stays strictly additive and atomic; no production migration is run.

## Preserved safety behavior (re-verified)

`OPEN→UNKNOWN→OPEN` ⇒ POSITION_OPEN, no cooldown · `…→NO_POSITION` with evidence+discriminator
⇒ COOLDOWN remaining 3 · `…→` unsupported flat ⇒ POSITION_RECONCILIATION · `UNKNOWN→NO_POSITION`
no prior open ⇒ no manufactured exit · bare `position_id` ⇒ not closure evidence ·
POSITION_RECONCILIATION → authoritative OPEN ⇒ POSITION_OPEN · → valid close ⇒ COOLDOWN · same
close-event replay ⇒ no reset. Frozen cooldown ladder E=3 / E+1=2 / E+2=1 / E+3=0 (blocked) /
E+4=WATCHLIST / E+5=ENTRY_ELIGIBLE.

## Tests & probes

- `pytest tests/universe` → **221 passed** (204 prior + 17 new R1.3 tests).
- `pytest tests` → only `tests/test_breakout_indicators.py` fails (4), identically to
  `breakout-strategy @ 46b8f257` — pre-existing, unrelated.
- Explicit §8 probes (two-lifecycle reused-pid distinct ids; same-lifecycle replay no reset;
  ambiguous close → reconciliation; advanced cooldown-divergence → conflict; exact advanced
  replay → no-op; `POSITION_EXITED_TODAY` migration → flat anchor; active cooldown +
  reconciliation → no decrement) all pass.

## Out of scope (unchanged)

P3-4 candidate-source integration · P3-5 inherited/open-book heat · P3-6 canonical identity
redesign · P3-7 IBKR mapping verification · FX-normalized sizing — all remain OPEN for Phase
R2. No runtime wiring, no enablement, no production DB/migration, no service action.
