# Dynamic Universe v1 — Pre-Enable Blocker Register

> **Status of this PR:** `feature/dynamic-universe-ibkr-shadow-v1-hardening` is a
> **default-off, un-wired, broker-free, additive** foundation. The feature flag
> `enable_dynamic_universe_shadow` is `False` everywhere, the package is not imported by
> `main.py`/`api_server.py`/any live module, and no broker/data-provider/live DB is
> touched. **Merging this foundation does NOT authorize runtime enablement.**
>
> This register records the independent senior review of Draft PR #2
> (`APPROVE_WITH_NON_BLOCKING_FINDINGS` — P0:0, P1:0, P2:2, P3:9). The two **P2** items
> are RESOLVED in this commit (see below). The **P3** items are recorded as explicit
> pre-enable blockers. Each item below documents: *finding, risk, current behavior, why
> the disabled/un-wired merge is currently safe, required correction before enablement,
> required test, owner/status.*
>
> **No mandatory pre-enable blocker may be considered cleared until it is implemented,
> tested, and independently reviewed.** A separate sign-off is required before any shadow
> flag is enabled outside isolated tests.

---

## Consolidated R1–R1.3 independent review — outcome

The full R1–R1.3 series (`breakout-strategy @ 46b8f257` → `feature/dynamic-universe-preenable-r1-fix3
@ 6713cfa`; commits R1 `5da3e6e`, R1.1 `5c09119`, R1.2 `73d305f`, R1.3 `6713cfa`) received a
consolidated independent read-only review:

```text
R1_SERIES_APPROVED_FOR_DISABLED_MERGE
P0: 0   P1: 0   P2: 0   P3: 3 pre-enable residuals
```

Focused suite: **221 passed** (`pytest tests/universe`); full-suite failures remain only in the
pre-existing `tests/test_breakout_indicators.py` isolation issue (identical at base `46b8f257`).

**The reviewed R1-series items are therefore marked `RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE`:**

> Resolved only for merging while the feature remains default-off,
> un-wired, and not migrated in production.
>
> This status does not authorize runtime enablement, scheduler wiring,
> production migration, shadow soak, paper trading, live trading,
> or Phase R2.

This is **not** a claim that all pre-enable work is complete. Three P3 residuals
(**P3-R1-A**, **P3-R1-B**, **P3-R1-C**, below) remain **OPEN — mandatory before runtime
enablement**. The R2 blockers are at differing stages: P3-4 (R2B) and P3-5 / BLOCKER-S
(R2C) are **implemented and approved for disabled merge**, and P3-6 / P3-7 (R2A-1) are
implemented and awaiting independent review — but **none are resolved for runtime
enablement**. Enablement remains blocked until R2C is merged/deployed and the remaining
pre-enable residuals (the R2B residuals R2B-P3-1..4 and P3-R1-A/B/C) are closed. See
`docs/dynamic_universe_pre_enable_r1_completion.md`,
`…_r1_2_completion.md`, and `…_r1_3_completion.md`.

---

## P2 — resolved in this hardening commit (were "fix before enablement")

### P2-1 — Migration atomicity — **RESOLVED**
- **Finding:** the migration framework documented single-transaction atomicity but,
  under Python's default `sqlite3` `isolation_level=""`, `CREATE TABLE` / `PRAGMA
  user_version` ran in autocommit (no enclosing transaction), so a mid-migration failure
  could leave a partial schema.
- **Correction (this commit):** `bot/universe/db.migrate` now sets
  `conn.isolation_level = None` and wraps each migration in one explicit
  `BEGIN IMMEDIATE` … (all schema/index statements) … `PRAGMA user_version` … `COMMIT`;
  on any error it `ROLLBACK`s and re-raises the original exception. `executescript()` is
  not used (it forces an implicit COMMIT). Verified empirically that SQLite DDL **and**
  `PRAGMA user_version` roll back together.
- **Tests:** `tests/universe/test_migrations.py` — injected mid-migration failure
  (rollback removes all objects, `user_version` unchanged, clean retry succeeds,
  idempotent rerun), failure on the last statement, concurrent-writer locking, and an
  AST guard that `migrate` never calls `executescript`.
- **Status:** ✅ resolved (still default-off; does not authorize enablement).

### P2-2 — USD currency normalization for eligibility — **RESOLVED**
- **Finding:** `adv20_usd` / price thresholds (`MIN_PRICE_USD`=$10, `MIN_ADV20_USD`=$20M)
  were compared against **un-converted local-currency** values (GBP/GBX/EUR) labelled USD.
- **Correction (this commit):** new `bot/universe/fx.py` performs explicit USD
  normalization via an **injected, broker-free** `FxRateProvider` (returns an
  `FxQuote(rate, as_of)`; USD assumed 1.0, no provider call). The evaluator now carries
  distinct `price_local`/`adv20_local`/`currency`/`price_unit`/`fx_to_usd`/
  `fx_effective_date` and USD-normalized `price_usd`/`adv20_usd`; eligibility compares the
  **USD-normalized** values. GBX is `currency='GBP', price_unit='GBX'` (÷100 → ×GBPUSD).
  All FX problems **fail closed** (never fall back to local) with deterministic reason
  codes: `fx_conversion_unavailable`, `fx_rate_invalid`, `fx_rate_stale`,
  `currency_unknown`, `price_unit_unknown`, `gbx_gbp_unit_ambiguous`,
  `normalized_price_invalid`, `normalized_adv20_invalid`. FX freshness: the rate must be
  effective on the trading date (or a prior valid FX session ≤ `MAX_FX_STALENESS_DAYS`=4
  days earlier); a future rate is rejected.
- **Tests:** `tests/universe/test_fx_normalization.py` — USD/GBP/GBX/EUR conversions, all
  fail-closed modes, freshness boundary, USD-normalized price ($10) and ADV20 ($20M)
  boundaries (below/equal/above), USD-never-calls-provider, flag-off-never-calls-provider.
- **Deferred (documented, NOT in P2 scope):** *hypothetical risk sizing* still uses the
  instrument's local price/ATR (single-currency approximation). It is hypothetical-only
  (no orders). FX-normalized sizing must be implemented before enablement — see
  **BLOCKER-S** below.
- **Status:** ✅ resolved for **eligibility** (still default-off; does not authorize
  enablement).

---

## P3 — mandatory / advisory pre-enable blockers

| ID | Class | One-line | Status |
|----|-------|----------|--------|
| P3-1 | advisory (doc only) | three docs outside declared naming scope | acknowledged |
| P3-2 | mandatory | `cooldown_until` stores a session count, not a date | RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE (R1) |
| P3-3 | **mandatory** | state + history writes not atomic together | RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE (R1 atomic + R1.1/R1.3 runtime replay integrity); see residual P3-R1-B |
| P3-4 | mandatory | candidate-source table not consumed in selection | RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE (R2B); 4 pre-enable residuals R2B-P3-1..4 IMPLEMENTED — awaiting independent review (still mandatory before enablement) |
| P3-5 | mandatory | portfolio heat ignores inherited/open-book exposure | IMPLEMENTED — awaiting independent review (R2C) |
| P3-6 | mandatory | canonical-ID collision risk | IMPLEMENTED — awaiting independent review (R2A-1) |
| P3-7 | **mandatory** | IBKR mapping check ignores verification status | IMPLEMENTED — awaiting independent review (R2A-1) |
| P3-8 | **mandatory** | provider removal can preserve stale open-position state | RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE (R1.1) |
| P3-9 | **mandatory** | cooldown depends on observing `POSITION_EXITED_TODAY` | RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE (R1.1/R1.3); see residual P3-R1-A |
| P3-R1-A | **mandatory (pre-enable)** | reused explicit provider `close_event_id` can mask a second close | IMPLEMENTED — awaiting independent review (R2A-0 + R2A-0.1) |
| P3-R1-B | **mandatory (pre-enable)** | legacy NULL transition-hash fallback skips some markers | IMPLEMENTED — awaiting independent review (R2A-0) |
| P3-R1-C | **mandatory (pre-enable)** | remaining test-completeness items | IMPLEMENTED — awaiting independent review (R2A-0 + R2A-0.1) |
| BLOCKER-S | mandatory | hypothetical sizing not FX-normalized (from P2-2) | IMPLEMENTED — awaiting independent review (R2C) |

> "mandatory" = must be resolved + independently reviewed before the flag is enabled
> outside isolated tests. P3-2/P3-3/P3-8/P3-9 are now `RESOLVED FOR DEFAULT-OFF / UN-WIRED
> MERGE` following the consolidated R1–R1.3 review (above) — see the qualification there; this
> is **not** an enablement authorization, and the pre-enable residuals P3-R1-A/B/C remain OPEN.
> The R1 attempt at P3-8/P3-9 was found DEFECTIVE in the first independent review (an `UNKNOWN`
> observation erased the last authoritative open state, so an exit during a provider outage
> bypassed cooldown and re-enabled entry). **Phase R1.1**
> (`feature/dynamic-universe-preenable-r1-fix1`) re-implemented P3-8/P3-9 with authoritative
> position continuity and added content-aware idempotency conflict detection for P3-3. See
> `docs/dynamic_universe_pre_enable_r1_completion.md`. The foundation remains default-off and
> un-wired. P3-4, P3-5, P3-6, P3-7, BLOCKER-S are deferred to Phase R2.

### R1.2 — pre-enable corrections from the independent R1.1 review

The independent review of R1/R1.1 **approved the disabled merge** but raised three **P2
pre-enable** findings. **Phase R1.2** (branch `feature/dynamic-universe-preenable-r1-fix2`)
corrects all three; the consolidated R1–R1.3 review (above) marks them `RESOLVED FOR
DEFAULT-OFF / UN-WIRED MERGE`.

| ID | One-line | Status |
|----|----------|--------|
| P2-A | historical replay rejected after current state legitimately advanced (`persist_transition_atomic`) | RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE (R1.2) |
| P2-B | v3 migration lost the authoritative open anchor for a position open at the migration boundary | RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE (R1.2) |
| P2-C | `EXIT_ONLY` overloaded for position uncertainty (could imply a position definitely exists) | RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE (R1.2) |

- **P2-A:** `_reconcile_duplicate` now treats a current-state row that has legitimately
  advanced past the replayed history date as a valid idempotent no-op (exact replay) or a
  conflict (divergent history content), reserving `StateHistoryConsistencyError` for a
  missing / behind / incoherent current state.
- **P2-B:** the v3 back-fill (corrected IN PLACE — v3 is unreleased / never run in
  production) derives the authoritative anchor from the v2 `last_observed_position_status`
  (using `evaluated_trading_date` as provenance); UNKNOWN / missing / malformed → blocked,
  never inferred flat.
- **P2-C:** a dedicated `POSITION_RECONCILIATION` state now represents position uncertainty;
  `EXIT_ONLY` is reserved for an authoritatively-open position.

See `docs/dynamic_universe_pre_enable_r1_2_completion.md`. The foundation remains
default-off and un-wired; nothing is enabled, wired, migrated, or deployed.

### R1.3 — pre-enable corrections from the independent R1.2 review

The independent review of R1.2 **approved the disabled merge** and raised two **P2** findings
plus a **P3** coverage gap. **Phase R1.3** (branch `feature/dynamic-universe-preenable-r1-fix3`)
corrects all three; the consolidated R1–R1.3 review (above) marks them `RESOLVED FOR
DEFAULT-OFF / UN-WIRED MERGE`. P3-3 is cleared because the **runtime write path always stores
the complete transition hash**; the legacy NULL-hash fallback (residual **P3-R1-B**) is the
named follow-up that does NOT reopen P3-3 but must be hardened before enablement.

| ID | One-line | Status |
|----|----------|--------|
| Finding 1 | synthetic close-event IDs could collide across distinct lifecycles (cooldown bypass) | RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE (R1.3); residual P3-R1-A (explicit-id reuse) |
| Finding 2 | advanced historical replay did not detect divergent cooldown bookkeeping | RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE (R1.3); residual P3-R1-B (legacy NULL-hash fallback) |
| Finding 3 | minor migration test-coverage gaps | RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE (R1.3); residual P3-R1-C (remaining test completeness) |

- **Finding 1:** close-event identity is now lifecycle-safe — explicit `close_event_id`
  (preferred), else a synthetic id keyed on `(canonical_id, position_id_hash,
  opened_trading_date, closed_trading_date)` with a `close:v2` version prefix. A close with
  NEITHER an explicit id NOR an `opened_trading_date` discriminator is AMBIGUOUS → it does NOT
  start cooldown and instead requires authoritative reconciliation (POSITION_RECONCILIATION).
  The old `(pid_hash, closed)`-only fallback is removed.
- **Finding 2:** the append-only history row now stores a deterministic
  `transition_snapshot_json` + `transition_snapshot_hash` covering ALL material transition
  outputs (incl. cooldown bookkeeping the feature hash omits). Idempotency is decided on that
  hash, so a divergent replay is detected even after the current state has legitimately
  advanced. Two history columns added to the unreleased v3 migration IN PLACE (no v4).
- **Finding 3:** added tests for the `latest_observed_at` column, the `POSITION_EXITED_TODAY`
  migration fixture, all six v3 authoritative/reconciliation columns + the two transition
  columns, and an active-cooldown + reconciliation no-decrement safety test.

See `docs/dynamic_universe_pre_enable_r1_3_completion.md`. The foundation remains default-off
and un-wired; nothing is enabled, wired, migrated, or deployed.

### Pre-enable residuals from the consolidated R1–R1.3 review (P3-R1-A/B/C)

These three P3 residuals were surfaced by the consolidated review. They are **OPEN —
mandatory before runtime enablement**. They are **not** blockers to the disabled, un-wired
merge (none can manifest while the flag is off and no provider/migration runs), and they do
not reopen the items marked `RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE`.

#### P3-R1-A — reused explicit provider close-event ID
```text
A provider-supplied close_event_id must be globally unique per close
lifecycle.

A reused explicit ID across distinct lifecycles is currently interpreted
as a replay and can mask the second genuine close.

Before enablement:
- enforce and document the uniqueness contract;
- preferably detect the same explicit event ID paired with a different
  lifecycle discriminator;
- route that violation to POSITION_RECONCILIATION;
- add a regression test.
```
Status: **IMPLEMENTED — awaiting independent review (R2A-0 + R2A-0.1).** R2A-0.1 (operator
ruling) makes an explicit `close_event_id` PREFERRED but NOT sufficient alone: a close is
processed only with a COMPLETE valid lifecycle — position_id (→ hash) + valid opened & closed
dates (opened <= closed <= eval date, opened not future) — folded into a deterministic versioned
qualified key (`close-key:v2:cid|pid|opened|closed|explicit`, persisted in
`universe_state.last_close_event_key`). The same explicit id under ANY differing lifecycle
component (opened/closed/pid), or any missing/malformed lifecycle field, routes to
`POSITION_RECONCILIATION` (entry blocked, no cooldown, no processed marker, authoritative OPEN
anchor retained). The v4 migration fails closed for pre-v4 processed-event rows
(`position_reconciliation_required = 1` when the qualified key is NULL). Tests in
`tests/universe/test_r2a0_pre_enable.py` and `tests/universe/test_r2a0_1_lifecycle.py`. NOT yet
resolved — see `docs/dynamic_universe_pre_enable_r2a0_1_completion.md`. Touches P3-9's
exactly-once cooldown property.

#### P3-R1-B — legacy NULL transition hash
```text
The primary runtime persistence path always writes a complete
transition_snapshot_hash.

Legacy/raw history rows with a NULL hash use a bounded fallback that
does not compare all cooldown and authoritative-position markers.

Before enablement:
- fail closed for advanced replay of NULL-hash history;
  or
- ensure every history-writing API computes the complete hash;
- add a regression test.
```
Status: **IMPLEMENTED — awaiting independent review (R2A-0).** Frozen policy: an ADVANCED
replay of a NULL-`transition_snapshot_hash` row fails closed (`StateHistoryConsistencyError`) —
incomplete immutable content is never trusted/inferred; the SAME-DATE path keeps the safe full
comparison. Additionally `append_history` now writes the complete transition snapshot/hash, so no
supported history-writing API creates a new NULL-hash row; legacy NULL rows are left in place (no
guessed backfill). Tests in `tests/universe/test_r2a0_pre_enable.py`. NOT yet resolved — see
`docs/dynamic_universe_pre_enable_r2a0_completion.md`. Tracked within P3-3's machinery; does not
reopen P3-3.

#### P3-R1-C — remaining test completeness
```text
- explicit provider close_event_id reuse across lifecycles;
- NULL-hash replay outcome;
- explicit coverage of all transition-hash fields;
- bare POSITION_EXITED ambiguity.
```
Status: **IMPLEMENTED — awaiting independent review (R2A-0).** Added
`tests/universe/test_r2a0_pre_enable.py` (9 tests): explicit `close_event_id` reuse across
lifecycles (+ same-lifecycle replay guard), NULL-hash advanced replay fail-closed / same-date
full comparison / `append_history` non-NULL hash, every transition-hash material field (incl.
`last_close_event_key`), and bare `POSITION_EXITED` **and** `POSITION_EXITED_TODAY` ambiguity →
reconciliation. NOT yet resolved — see `docs/dynamic_universe_pre_enable_r2a0_completion.md`.

### P3-1 — Documentation scope deviation (advisory; no code change)
- **Risk:** none (inert documentation).
- **Current behavior:** `docs/ig_service_pause_runbook.md`,
  `docs/ig_shutdown_readiness_audit.md`, `docs/universe_db_schema.md` are outside the
  originally declared `dynamic_universe_*` naming scope. They contain no code and alter no
  runtime/config.
- **Why the disabled merge is safe:** inert docs; nothing executes them.
- **Required correction before enablement:** none (optionally widen the stated PR scope or
  split the IG runbook docs into their own PR). Recorded as a harmless PR-scope deviation.
- **Required test:** none.
- **Owner/Status:** docs owner — acknowledged, no action required.

### P3-2 — Misleading `cooldown_until` field name (mandatory)
- **Risk:** a future date-comparison against a column that actually holds a remaining
  **session count** would silently misbehave.
- **Current behavior:** `universe_state.cooldown_until` stores `str(cooldown_remaining)`
  (an integer count), read back via `int(...)`. Correct today, misleading name.
- **Why the disabled merge is safe:** the value is only ever written/read as an integer
  count by the same evaluator; no calendar comparison exists.
- **Required correction before enablement:** either rename to
  `cooldown_remaining_sessions` (new additive migration) **or** store an explicit release
  **trading date** with calendar semantics — and document the chosen decision.
- **Required test:** a migration/round-trip test asserting the column's semantics match
  its name (count vs date), plus a transition test for the chosen representation.
- **Resolution (R1):** additive migration v2 adds explicit session-based fields —
  `cooldown_started_trading_date`, `cooldown_sessions_remaining` (canonical count),
  `cooldown_last_counted_trading_date`, and a DISPLAY-ONLY `cooldown_release_estimate` (left
  NULL — never authoritative without an approved exchange calendar). Runtime logic reads
  `cooldown_sessions_remaining`, never `cooldown_until`; the legacy column is kept as
  deprecated compatibility metadata. An ambiguous legacy row (non-zero `cooldown_until`,
  NULL session field) fails safe into a blocked/manual-review `COOLDOWN`
  (`cooldown_legacy_ambiguous`) — the count is never inferred. Counting is by COMPLETED
  evaluated session (no calendar dependency): exit session E not counted; ≤1 decrement per
  completed session; weekends/holidays/missing-bar sessions, open positions, and UNKNOWN
  status never decrement.
- **Tests:** `tests/universe/test_cooldown_sessions.py`, `tests/universe/test_r1_migration.py`.
- **Owner/Status:** universe owner — **RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE** (R1).

### P3-3 — Current-state and history writes are not atomic together (**mandatory**)
- **Risk:** a crash between `upsert_state` and `append_history` leaves `universe_state`
  advanced (hysteresis/cooldown counters) with no history row; the idempotency key (the
  history row) is then absent, so the next run re-evaluates the same date and
  **double-advances** counters.
- **Current behavior:** `evaluator._evaluate_one` calls `registry.upsert_state(...)` then
  `registry.append_history(...)` as two separate autocommit writes.
- **Why the disabled merge is safe:** shadow-only, flag-off; no scheduler runs in
  production, so the crash window is never opened.
- **Required correction before enablement:** write both in **one transaction** (or write
  the history/idempotency row first, then the mutable state) so they commit or roll back
  together.
- **Required test:** inject a failure between the state and history writes; verify **both
  roll back together** and the next run re-evaluates cleanly (no double-advance).
- **Resolution (R1):** new `Registry.persist_transition_atomic(state, history)` writes both
  rows in ONE explicit `BEGIN IMMEDIATE` transaction (`isolation_level = None`, no
  `executescript`). The history/idempotency row is inserted FIRST; the two writes commit or
  roll back together; a duplicate key rolls the whole tx back as a no-op (no double-advance).
  The evaluator persists every transition exclusively through this method. Defence in depth:
  v2 adds append-only `BEFORE UPDATE/DELETE` triggers on `universe_state_history`.
- **R1.1 addition:** content-aware idempotency conflict detection. A duplicate idempotency
  key with DIVERGENT content (different new_state / feature hash / cooldown result /
  lifecycle event / authoritative markers) now raises `TransitionConflictError` instead of
  the R1 silent no-op (review Finding 3); a broken history/state pair raises
  `StateHistoryConsistencyError`. Identical replays remain idempotent no-ops.
- **Tests:** `tests/universe/test_atomic_persistence.py` (fault injection at each seam incl.
  authoritative/close markers, rollback leaves both tables unchanged, identical-replay
  idempotency, conflicting new_state/hash/cooldown/close-event → conflict, history/state
  inconsistency → consistency error, concurrent-writer serialisation, append-only triggers).
- **Owner/Status:** universe owner — **RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE** (atomic
  transaction R1; content-aware conflict detection R1.1; complete transition-hash replay
  integrity R1.3). Cleared because the runtime write path always stores the complete hash;
  residual **P3-R1-B** (legacy NULL-hash fallback) is OPEN — mandatory before enablement.

### P3-4 — Candidate-source table not used in candidate selection (mandatory)
- **Risk:** the AUTO/TTI/MANUAL candidate concept is decoupled from selection; enabling
  without wiring it would either ignore curated candidates or apply them inconsistently.
- **Current behavior:** `candidate_sources` rows are TTL-expired but never read during
  contention; selection draws from all `ENTRY_ELIGIBLE` instruments with `entry_signal`.
  The §12 invariants hold vacuously.
- **Why the disabled merge is safe:** inert foundation; selection output is hypothetical
  and unused.
- **Required correction before enablement:** define and implement candidate **activation,
  TTL expiry, deduplication, source merging, resubmission behavior, and
  inactive-candidate exclusion**, and make selection consume active candidate rows as the
  runtime source.
- **Required test:** selection consumes only active AUTO/TTI/MANUAL candidates; expired /
  inactive candidates are excluded; dedup + source-merge + resubmission behave as specified.
- **Resolution (R2B) — RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE:** schema **v6** adds a
  dedicated `candidates` table (keyed by the R2A-1 verified `instrument_uid` + `listing_uid`,
  never a ticker) plus an append-only `candidate_audit`. The new `bot.universe.candidate_store`
  is the ONLY selection source: the evaluator consumes candidates exclusively via
  `effective_candidates(trading_date)`. Frozen precedence MANUAL>TTI>AUTO (suppressed
  candidates retained/auditable; an ACTIVE higher-precedence candidate that fails
  identity/listing validation blocks with `candidate_source_conflict` — no silent
  fall-through). Frozen TTL: MANUAL/TTI = 5 completed sessions (read-then-count; effective
  E..E+4, expired E+5; idempotent per date), AUTO per-session/per-batch (atomic replacement).
  Resubmission supersedes (old row retained, `superseded_by_candidate_id` set); identical
  replay is a no-op; divergent replay raises `CandidateConflictError`. Identity-unresolved /
  ambiguous / unverified candidates are REJECTED (auditable, fail-closed). Candidate presence
  is necessary but NOT sufficient — all existing gates still apply, candidate expiry affects
  NEW entries only, open positions keep being managed. Wired behind a **default-off**
  `require_candidate_source` evaluator switch; feature stays disabled and un-wired. Legacy v1
  `candidate_sources` rows (no verified identity) are never R2B-effective.
- **Tests:** `tests/universe/test_r2b_{candidates,ttl,auto_batch,migration,selection}.py`.
- **Owner/Status:** universe owner — **RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE**. Independent
  frozen-scope review accepted: `R2B_APPROVED_FOR_DISABLED_MERGE` (P0/P1/P2 = 0; P3 = 4).
  Resolved only for merging while the feature remains default-off, un-wired and not migrated in
  production. Runtime enablement remains blocked by the four R2B pre-enable residuals below.
  The R2C blockers P3-5 (inherited/open-book portfolio heat) and BLOCKER-S (FX-normalized
  sizing) are implemented and approved for disabled merge (R2C), but remain NOT resolved for
  runtime enablement until R2C is merged/deployed and the R2B residuals below are closed.
- **R2B pre-enable residuals — IMPLEMENTED — awaiting independent review (R2B residuals
  tranche); NOT yet resolved — mandatory before runtime enablement:**
  - **R2B-P3-1 — candidate-store error observability:** `candidate_store_unavailable` and
    `candidate_malformed` reason codes exist, but candidate DB/read failures currently propagate
    as exceptions. Safety remains fail-closed because failure occurs before contention and TTL
    mutation, but a wired scheduler could crash rather than emit a stable blocked result. Before
    enablement: catch supported candidate-store/database read failures; convert them to
    `candidate_store_unavailable`; convert malformed/unknown persisted rows to
    `candidate_malformed`; block new entry without crashing the evaluation cycle; add regression
    tests.
    - **Resolution (R2B residuals) — IMPLEMENTED, awaiting independent review:**
      `effective_candidates` now catches any `sqlite3.Error` and returns
      `EffectiveSelection(store_unavailable=True)`; the evaluator additionally guards store
      construction. On a read failure the evaluator blocks EVERY new entry with
      `candidate_store_unavailable`, records NO selection audit, and does NOT tick TTL (no
      mutation); the cycle never crashes and open positions keep being managed. Tests:
      `tests/universe/test_r2b_residuals.py` (missing-table / corrupt-DB → `store_unavailable`;
      evaluator block-all; open-position management).
  - **R2B-P3-2 — unknown persisted source/status:** supported write APIs reject unknown sources,
    but a raw/injected ACTIVE row with an unknown source is not defensively rejected by
    `effective_candidates`. Before enablement: add enum CHECK constraints where migration policy
    permits; and/or defensively reject unknown source/status as `candidate_malformed`; add a
    raw-row regression test.
    - **Resolution (R2B residuals) — IMPLEMENTED, awaiting independent review:** defensive
      read-path validation on the HIGHEST-precedence candidate per instrument rejects an unknown
      source, a present-but-non-integer TTL, or a present-but-unparseable effective/generation
      date as `candidate_malformed`, and a literal unknown (non-enum) status is caught by a
      separate pass — all fail closed. A malformed higher-precedence candidate BLOCKS the
      instrument and never falls through to a lower-precedence one. Tests cover unknown source,
      unknown status, invalid TTL, invalid effective date, and the no-fall-through guarantee.
  - **R2B-P3-3 — selection audit completeness:** `SUPPRESSED` and `SELECTED_EFFECTIVE` are
    defined but not persisted. Before enablement, decide and implement one frozen policy:
    persist deterministic selection/suppression audit events without creating duplicate
    read-path noise; or explicitly remove those event types from the promised audit contract.
    - **Resolution (R2B residuals) — IMPLEMENTED, awaiting independent review:** new
      `CandidateStore.record_selection_audit` persists `SELECTED_EFFECTIVE` (per effective
      candidate) and `SUPPRESSED` (per suppressed candidate, reason
      `suppressed_by_higher_precedence_source`) idempotently — at most once per
      `(candidate_id, trading_date, event_type)` via a check-then-insert under the
      `BEGIN IMMEDIATE` write lock, so a duplicate same-date evaluation adds no rows. It is
      called ONLY on the gate-enabled path (feature-off writes nothing) and preserves
      append-only (INSERT-only; no UPDATE/DELETE). **No schema v8 required** (see the schema
      doc). Tests cover once-per-date persistence, duplicate-evaluation idempotency, and
      append-only preservation.
  - **R2B-P3-4 — transaction consistency and TTL fault coverage:** `deactivate_candidate_atomic`
    currently uses a deferred transaction rather than `BEGIN IMMEDIATE`. Atomicity is preserved,
    but the lock is acquired later than the other multi-row lifecycle APIs. Before enablement:
    use explicit `BEGIN IMMEDIATE`; add a TTL-update fault-injection rollback test; verify
    concurrent deactivation/TTL operations fail or serialize safely.
    - **Resolution (R2B residuals) — IMPLEMENTED, awaiting independent review:**
      `deactivate_candidate_atomic` now uses an explicit `BEGIN IMMEDIATE` (status UPDATE +
      append-only audit COMMIT/ROLL BACK together), consistent with the other lifecycle writes.
      `tick_ttl_atomic` gained fault-injection seams; tests prove a fault AFTER a TTL decrement,
      AFTER an expiry status flip, or AFTER an EXPIRED audit insert rolls the whole batch back
      (no partial decrement / orphan audit), the connection is discarded and a retry succeeds
      cleanly, and a concurrent writer either fails cleanly (`OperationalError`, no partial
      data) or serializes after the lock releases.

### P3-6 — Canonical-ID collision risk (mandatory)
- **Risk:** `canonical_id = {US|LSE|EU}_{symbol}` (region from currency) can silently merge
  two distinct securities that share symbol+currency+region via `ON CONFLICT DO UPDATE`.
- **Current behavior:** `seed.canonical_id` builds the id from region (currency-derived)
  and ticker text only; no exchange or permanent broker identifier.
- **Why the disabled merge is safe:** the curated ~29-instrument set has no collisions;
  ingestion is not broad and is shadow-only.
- **Required correction before enablement:** use a stable identity based on a verified
  permanent listing/instrument identifier (e.g. IBKR `conId`) where available, and handle:
  same ticker on different exchanges, ticker reuse over time, share classes, listing
  migrations, ticker changes, delisted/relisted securities. (Alternatively, refuse to merge
  rows whose `name`/`exchange` differ.)
- **Required test:** two distinct securities sharing symbol+currency+region do **not**
  merge; identity survives ticker change / listing migration fixtures.
- **Resolution (R2A-1) — IMPLEMENTED, awaiting independent review:** schema **v5** adds a
  four-concept canonical model — `instrument_uid` (opaque, immutable, ANCHOR-derived from a
  verified ISIN/FIGI, **never** the ticker), `listing_uid` (one venue/currency listing),
  verified broker mapping attributes, and `display_symbol`. Identity is created only through
  the controlled `bot.universe.identity_store.resolve_identity_atomic` API. Same-ticker /
  different-MIC / different-currency / different-anchor / share-class / ticker-reuse cases
  never collapse (anchor-derived uids); a ticker rename retains `instrument_uid` (display
  changes, audited); a listing migration creates a new `listing_uid` and retains the old row
  (`valid_to` set). Conflicting anchors raise `IdentityConflictError` (never merge/overwrite).
  Existing v4 rows become `identity_status='UNVERIFIED'` with NULL `instrument_uid` — no
  ticker-derived backfill, fail-closed (entry blocked until provider-backed resolution). The
  gate is **default-off and un-wired**; the feature remains disabled.
- **Tests:** `tests/universe/test_r2a1_identity.py`, `test_r2a1_migration.py`.
- **Owner/Status:** universe owner — **IMPLEMENTED — awaiting independent review (R2A-1)**.

### P3-7 — IBKR mapping check does not enforce verification status (**mandatory**)
- **Risk:** an unverified (`CONFIG_DERIVED`, no `conId`) IBKR mapping currently satisfies
  routing eligibility; if IBKR ever becomes order-capable this would permit routing on an
  unverified contract.
- **Current behavior:** `ibkr_ok = mapping is not None and primary_gateway == "IBKR"`;
  `verification_status` is ignored.
- **Why the disabled merge is safe:** there is **no execution path whatsoever** in v1; the
  check only affects a hypothetical eligibility flag.
- **Required correction before enablement:** require an explicit acceptable state such as
  `VERIFIED_REFERENCE_MATCH` (or a separately defined safe configuration-derived status)
  before hypothetical or real routing eligibility is granted.
- **Required test:** `CONFIG_DERIVED`/`UNVERIFIED` mapping → not routing-eligible;
  only the approved verified status → eligible.
- **Resolution (R2A-1) — IMPLEMENTED, awaiting independent review:** the v5 `ibkr_mapping`
  table carries an explicit `verification_status` over the frozen enum
  (`VERIFIED_REFERENCE_MATCH`, `VERIFIED_CONFIGURED`, `UNVERIFIED`, `STALE`, `REJECTED`,
  `AMBIGUOUS`). `bot.universe.identity.verify_ibkr_mapping` passes **only**
  `VERIFIED_REFERENCE_MATCH` and additionally fails closed on a missing `conId`, a future
  `verified_at`, an expired/absent `reverify_after_date` (default reverification interval **90
  calendar days**), or a currency/MIC/exchange mismatch against the verified listing — each
  with a stable reason code (`ibkr_mapping_*`). Mapping verification is independent of
  canonical identity. IG mappings persist with `order_routing_blocked=1` and are never made
  routing-eligible in this tranche. Wired into the evaluator behind a default-off
  `enforce_verified_identity` gate; no routing path exists.
- **Tests:** `tests/universe/test_r2a1_mappings.py`, `test_r2a1_isolation.py`.
- **Owner/Status:** universe owner — **IMPLEMENTED — awaiting independent review (R2A-1)**.

### P3-8 — Provider removal can preserve stale open-position state (**mandatory**)
- **Risk:** if a position provider was present and later becomes absent, the legacy
  prior-state fallback (`has_open = prior_state in (POSITION_OPEN, EXIT_ONLY)`) can retain
  `POSITION_OPEN`/`EXIT_ONLY` indefinitely, pretending a position is still authoritatively
  known.
- **Current behavior:** `_position_ctx` derives open-state from the persisted prior state
  when no provider is injected; only a hypothetical `trend_break` clears it.
- **Why the disabled merge is safe:** shadow-only, flag-off, no routing; the provider/
  no-provider mix never occurs in production.
- **Required correction before enablement:** absence of the authoritative provider must
  produce a deterministic **`POSITION_STATUS_UNKNOWN`** safe state that blocks new entries
  without asserting the position is still known. (Reason code already exists:
  `Reason.POSITION_STATUS_UNKNOWN`.)
- **Required test:** provider present → `POSITION_OPEN`; provider removed on a later run →
  state resolves to unknown-safe (blocks entry), never a retained stale open.
- **Resolution attempt (R1) — partially correct:** the legacy no-provider prior-state
  derivation was removed and a missing/error/stale provider resolves to `UNKNOWN`
  (fail-safe). BUT R1 stored only one `last_observed_position_status` that `UNKNOWN`
  overwrote — so the "stale prior preserved as evidence" guarantee was not actually met
  across an outage. See R1.1 below.
- **R1 DEFECT → R1.1 correction:** the R1 implementation persisted a single
  `last_observed_position_status` that an `UNKNOWN` observation OVERWROTE — erasing the
  authoritative open state. R1.1 separates the LATEST observation (which UNKNOWN may
  overwrite) from the LAST AUTHORITATIVE evidence (`last_authoritative_position_status` /
  `_id_hash` / `_observed_at`), which non-authoritative observations never erase. An
  authoritative OPEN→flat WITHOUT durable closure evidence now sets a persistent
  `position_reconciliation_required` block (blocks entry, no liquidation, never assumes flat).
- **Tests:** `tests/universe/test_position_authority.py`,
  `tests/universe/test_position_continuity.py` (OPEN→UNKNOWN→OPEN / →flat-with-evidence /
  →flat-without-evidence; reconciliation persists across the UNKNOWN gap; cleared by
  authoritative open or evidence-close).
- **Owner/Status:** universe owner — **RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE** (R1.1).

### P3-9 — Cooldown depends on observing `POSITION_EXITED_TODAY` (**mandatory**)
- **Risk:** if the provider transitions `POSITION_OPEN → NO_POSITION` directly (never
  emitting the one-day `POSITION_EXITED_TODAY`), cooldown never starts and an unsafe
  eligibility path could open.
- **Current behavior:** `state_machine` starts cooldown only when `exited=True`, which the
  provider path sets only on `POSITION_EXITED_TODAY`. The rehearsal always injects
  `POSITION_EXITED_TODAY` for exactly one session, masking the dependency.
- **Why the disabled merge is safe:** shadow-only; cooldown only gates hypothetical entries.
- **Required correction before enablement:** derive the exit from an authoritative
  observed transition — `previous snapshot = open` **and** `current = no position` plus a
  reconciled exit event / close timestamp → start cooldown **exactly once** — rather than
  trusting a transient status value.
- **Required test:** `OPEN → NO_POSITION` with **no** `EXITED_TODAY` still starts cooldown
  exactly once; idempotent across reruns.
- **Resolution attempt (R1) — SUPERSEDED, found defective:** R1 stopped trusting the
  transient status but keyed exit detection off a single `last_observed_position_status` and
  accepted a bare `position_id` as evidence. Both were wrong: a same-run `UNKNOWN` overwrote
  `last_observed`, so an exit during an outage (`OPEN→UNKNOWN→NO_POSITION`) was missed
  (cooldown bypassed). The rehearsal was updated to a durable `OPEN→NO_POSITION` exit, but the
  underlying continuity bug remained. See R1.1 below.
- **R1 DEFECT → R1.1 correction:** in R1, an exit that occurred DURING an `UNKNOWN` outage
  (OPEN→UNKNOWN→NO_POSITION) was missed — cooldown was bypassed and the instrument became
  ENTRY_ELIGIBLE — because UNKNOWN had erased the authoritative open anchor. R1.1 keys exit
  detection off the durable `last_authoritative_position_status==POSITION_OPEN` (which
  survives the outage), requires EXPLICIT closure evidence (`closed_trading_date` /
  `close_event_id` / `explicitly_closed`; a bare `position_id` is insufficient), and
  de-duplicates via `last_processed_position_event_id` with a stale-close guard.
- **Tests:** `tests/universe/test_exit_detection.py` + `tests/universe/test_position_continuity.py`
  (OPEN→UNKNOWN→flat-with-evidence starts cooldown once; without evidence → reconciliation,
  no cooldown; replayed close does not reset; new separate close starts a fresh cooldown;
  older/future snapshots are non-authoritative; deprecated EXITED_TODAY still honoured once).
- **Owner/Status:** universe owner — **RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE** (R1.1;
  lifecycle-safe close identity R1.3). Residual **P3-R1-A** (reused explicit `close_event_id`
  can mask a second close) is OPEN — mandatory before enablement.

### BLOCKER-S — Hypothetical sizing not FX-normalized (mandatory; from P2-2)
- **Risk:** hypothetical qty/risk use local price/ATR; cross-currency risk figures are not
  comparable in USD.
- **Current behavior:** eligibility is USD-normalized (P2-2 resolved); `_hypothetical_order`
  still sizes in local units. Hypothetical only — never an order.
- **Why the disabled merge is safe:** sizing output is hypothetical and unused; no orders.
- **Required correction before enablement:** FX-normalize the sizing inputs (equity, price,
  ATR) consistently, recording FX source/date, before any real or paper sizing is used.
- **Required test:** a GBP/GBX instrument's hypothetical risk_usd/notional_usd equal the
  USD-normalized expectation; USD unchanged.
- **Correction (R2C):** new `bot/universe/sizing.py` sizes into the **account base currency**
  via an INJECTED, broker-free `BaseFxRateProvider` (rate = BASE per 1 instrument unit), in
  deterministic `Decimal` arithmetic. same-currency → rate 1 (no provider call); cross-currency
  → a fresh (≤ `MAX_FX_RATE_AGE_SECONDS`), valid, currency-matched rate or FAIL CLOSED
  (`fx_rate_missing`/`fx_rate_stale`/`fx_rate_invalid`/`fx_currency_mismatch`/
  `fx_provider_unavailable`/`sizing_currency_unresolved`). USD is NEVER assumed and 1.0 is
  NEVER substituted for a cross-currency pair. Quantity is floored (ROUND_DOWN); a candidate
  that cannot be sized within the base-currency per-trade risk limit / notional cap is blocked
  (`sizing_*`). Surfaced through the **default-off** evaluator gate `enforce_portfolio_heat`
  (the FX-normalized sizing is the prerequisite step of that gate). Tests:
  `tests/universe/test_r2c_sizing.py`.
- **Owner/Status:** universe owner — **IMPLEMENTED — awaiting independent review (R2C)**.
  Not resolved; the implementation session did not run the independent review.

### P3-5 — Portfolio heat ignores inherited/open-book exposure (mandatory)
- **Risk:** the pre-R2C contention check accumulated heat only WITHIN a single shadow run; an
  inherited book (existing open positions + open orders / pending intents) could already be at
  or over the limit and a new entry would still be admitted.
- **Current behavior (pre-R2C):** `_apply_contention` summed only the run's own hypothetical
  orders against `MAX_PORTFOLIO_HEAT`.
- **Why the disabled merge is safe:** contention output is hypothetical and unused; no orders.
- **Correction (R2C):** new `bot/universe/portfolio_heat.py` computes
  `post_trade_heat = existing_open_position_risk + open_order/pending_intent_risk +
  proposed_risk` (all normalized to base currency) from an INJECTED, broker-free
  `PortfolioRiskProvider` snapshot, compared against `MAX_PORTFOLIO_HEAT × base-currency
  equity` (or an explicit absolute base limit). FAIL CLOSED on a missing/stale/date-mismatched/
  currency-incomplete/identity-incomplete snapshot or missing/stale/invalid equity
  (`portfolio_snapshot_missing`/`_stale`/`_date_mismatch`, `portfolio_open_order_snapshot_stale`,
  `portfolio_currency_unresolved`, `portfolio_identity_unresolved`, `portfolio_equity_missing`/
  `_stale`/`_invalid`); an inherited book already over the limit → `open_book_heat_exceeded`, a
  proposed entry crossing it → `portfolio_heat_exceeded`. Freshness is enforced against an
  INJECTED `evaluation_time` (daily snapshot date == evaluation trading date; entry-time /
  open-order age ≤ 15 min). Heat gates NEW entries only — never forces liquidation, never alters
  an existing position. Surfaced through the **default-off** evaluator gate
  `enforce_portfolio_heat`. Optional schema-v7 `risk_evaluation` (+ append-only
  `risk_evaluation_audit`) records the decision evidence. Tests:
  `tests/universe/test_r2c_heat.py`, `test_r2c_gate.py`, `test_r2c_persistence.py`.
- **Owner/Status:** universe owner — **IMPLEMENTED — awaiting independent review (R2C)**.
  Not resolved; the implementation session did not run the independent review.

---

## Enablement gate (summary)

Before `enable_dynamic_universe_shadow` is set true **anywhere outside isolated tests**:
1. **Phase R1–R1.3 (P3-2, P3-3, P3-8, P3-9; P2-A/B/C; R1.3 Finding 1/2/3) passed the
   consolidated independent review and are `RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE`** —
   this clears them for the inert merge ONLY, and is **not** an enablement authorization.
2. The pre-enable residuals **P3-R1-A, P3-R1-B, P3-R1-C** remain **OPEN — mandatory before
   runtime enablement** (see their entries above).
3. **Phase R2:** P3-4 (candidate-source integration) is `RESOLVED FOR DEFAULT-OFF / UN-WIRED
   MERGE` (R2B); its four pre-enable residuals **R2B-P3-1..4** are now `IMPLEMENTED — awaiting
   independent review` (R2B residuals tranche) and remain **mandatory before runtime
   enablement** until that review resolves them (see the P3-4 entry above). P3-6 (canonical
   identity) and P3-7 (IBKR verification
   status) are `IMPLEMENTED — awaiting independent review` (R2A-1). **BLOCKER-S (FX-normalized
   sizing)** and **P3-5 (inherited/open-book portfolio heat)** are now `IMPLEMENTED — awaiting
   independent review` (R2C) — NOT resolved; a separate frozen-scope independent review is
   required, and the R2B residuals **R2B-P3-1..4** remain mandatory before runtime enablement.
4. A separate runtime-wiring change (into `main.py`/scheduler) is proposed and reviewed on
   its own — it is explicitly **out of scope** here.

Neither the R1–R1.3 work nor this register changes the posture: the foundation remains
default-off and un-wired. Marking these items resolved-for-disabled-merge does **not**
authorize enablement, scheduler wiring, production migration, shadow soak, paper/live trading,
or Phase R2.

---

## Shadow-wiring prerequisites (Gate C) — BLOCKER-W1 / BLOCKER-W2

These two prerequisites were raised by the shadow-readiness design packet
(`/root/deployment_records/dynamic_universe_shadow_readiness_design.md`, §11) as **mandatory
before runtime wiring**. They are implemented as inert boundary code ONLY — no runtime startup
is wired, no DB is created, no provider is called, and the feature stays default-off.

- **BLOCKER-W1 — broker-free completed-bar provider boundary — IMPLEMENTED — awaiting
  independent review.** The only existing daily-bar fetcher (`main.py:_regime_bars_fetcher` →
  `broker.fetch_bars`) is a broker call and must not be used by the shadow path. New
  `bot/universe/bar_provider.py` defines an INJECTED, broker-free `CompletedBarProvider`
  protocol + an immutable `CompletedBarSnapshot` (identity, trading_date, timeframe,
  `bar_end_time`, `source`, `version`, availability) and a fail-closed `safe_completed_bar` /
  `as_bar_available_fn` adapter (the scheduler's `bar_available_fn` seam). A missing provider, a
  provider exception, a malformed/mismatched result, an unavailable bar, or a stale/incomplete
  bar ALL resolve to `available=False` with a stable reason; the boundary never raises, never
  reports available without full proof, imports no broker, and fetches no live data.
  - **Concrete source (BLOCKER-W1 source) — IMPLEMENTED — awaiting independent review.** The
    boundary above was a `Protocol` only — no concrete broker-free source existed, which kept
    shadow non-enablable. New `bot/universe/local_bar_provider.py` adds the concrete, broker-free
    `LocalCompletedBarSnapshotProvider`: it answers from an EXPLICITLY-configured, local,
    read-only, immutable daily-bar snapshot (JSON / JSONL file, or a directory of them) — no
    broker, no IBKR/IG/EODHD, no live data, no production DB. A pure `validate_source_path`
    rejects a missing or production-DB-colliding source (basename + symlink-resolved basename),
    and a fail-closed `build_local_completed_bar_provider` factory constructs the provider only
    for a present, safe source. The `main.py` seam (`build_completed_bar_provider_from_config`
    → `_resolve_shadow_runtime_config`) builds it ONLY when the master flag is on AND a safe
    snapshot source is configured — default-off, no live default. Tests:
    `tests/universe/test_completed_bar_provider.py`. See
    `docs/dynamic_universe_completed_bar_provider_completion.md`.
  - **Still not enablement.** `bars_provider` (the eligibility-bars provider) remains a SEPARATE,
    not-yet-implemented tranche, so `validate_shadow_config` still fails closed at
    `bars_provider_missing` even with the flag on and a valid completed-bar provider. Shadow
    enablement (Gate E) and the service-restart window (Gate F) remain unapproved.
- **BLOCKER-W2 — lazy, flag-gated, side-effect-free construction — IMPLEMENTED — awaiting
  independent review.** Constructing `Registry`/`CandidateStore`/`IdentityStore` (or
  `seed_registry`) runs `migrate()` → `sqlite3.connect()`, creating the DB file even with the
  flag off. New `bot/universe/shadow_runtime.py` adds `validate_shadow_config` (PURE — no stat/
  open/connect/migrate) and a lazy `build_shadow_scheduler` factory that validates FIRST and
  constructs the Registry/evaluator/scheduler ONLY when the flag is on and the config is fully
  valid. Flag off / missing-or-unsafe DB path / missing provider / live-provider-without-approval
  → fail closed with a stable reason and ZERO construction (no Registry/store/provider, no
  `sqlite3.connect`, no `migrate`, no DB file, no provider call). The shadow DB path is validated
  (no filesystem touch) against the production DB basenames (`positions.db`/`regime.db`/
  `backtest.db`/`learning_loop.db`/… and `universe.db`).
- **NOT runtime activation.** No `main.py`/`api_server.py` wiring, scheduler startup, shadow
  enablement, service restart, production `universe.db`/`universe_shadow.db`, migration, or
  broker/provider call is part of this tranche. Tests:
  `tests/universe/test_shadow_prereqs_w1_w2.py`.

### Shadow-wiring prereq note — realpath/symlink preflight (W1W2-2)

`bot/universe/shadow_runtime.validate_shadow_config` validates the shadow DB path by **basename
only** (pure, no filesystem resolution), so it cannot catch a path that *resolves* through a
symlink to a production DB. Before Gate E/F shadow enablement or a service restart, the operator
must run a **realpath preflight** confirming the approved shadow DB path does not resolve to any
production DB; if realpath cannot be resolved safely, shadow enablement must stop. This is an
operator obligation documented in `docs/dynamic_universe_shadow_operations.md` and is **not** an
authorization of runtime activation.

## Gate C runtime wiring — IMPLEMENTED — awaiting independent review

Builds on W1/W2 (which must be merged/deployed first). Wires the **default-off, fail-closed**
runtime path in `main.py` only — the path that can LATER start the shadow scheduler when
explicitly enabled and validly configured. **This is wiring, not activation.** The feature stays
disabled (`enable_dynamic_universe_shadow=False` default), no DB/migration/provider/broker/order/
service-restart is part of this tranche, and `api_server.py` is **untouched** (strictly enforced
by `tests/universe/test_no_live_integration.py`).

- **`main.py` wiring (lazy, default-off).** New module-level `init_shadow_runtime(flags, …)` and a
  `TradingBot._maybe_run_shadow_cycle` per-cycle seam (called once after the regime scheduler).
  The master flag is checked FIRST: when absent/false (production default) `init_shadow_runtime`
  imports NOTHING from `bot.universe`, constructs nothing (no `sqlite3.connect`, no `migrate()`,
  no DB file, no provider, no scheduler), and logs a single `shadow_runtime_disabled` line. There
  is **no top-level `bot.universe` import** in `main.py`; the W1/W2 boundary is imported lazily,
  inside `init_shadow_runtime`, and only after the flag is true (AST-asserted by tests).
- **Enabled path fails closed unless fully configured.** When the flag is true, the wiring calls
  the W2 `build_shadow_scheduler` (which `validate_shadow_config` FIRST). Missing/unsafe shadow DB
  path, missing bars/completed-bar provider, a live provider without approval, or any
  builder/provider exception → fail closed: no scheduler, no DB touch, no provider call, no crash,
  and a stable non-secret reason logged.
- **No live provider default (policy).** `TradingBot._resolve_shadow_runtime_config` supplies a
  provider-injection seam but **no live default** — providers are `None`, and the shadow DB path
  is read from an EXPLICIT config block (`settings.dynamic_universe_shadow.shadow_db_path`) only,
  never the production default. So the production posture is `flag_off` (disabled); and the
  fail-closed chain stops at the FIRST unmet gate — were the flag flipped on with no config it
  fails at `shadow_db_path_missing`, and with a path but no injected provider at
  `bars_provider_missing`. Either way `shadow_runtime.scheduler` stays `None` and the per-cycle
  seam is a guarded no-op. Candidate ingestion is NOT wired — the seam feeds the scheduler an
  empty (placeholder) record set.
- **Scheduler lifecycle.** A scheduler is constructed only on the fully-valid, non-live-provider
  path (reachable only with injected stub providers in tests/rehearsal). It is shadow-only: it
  submits/modifies/cancels no orders, opens/closes no positions, calls no broker, and writes only
  to its dedicated shadow DB. Runtime scheduling stays disabled unless the flag is explicitly true
  AND the config is fully valid.
- Tests: `tests/universe/test_shadow_runtime_wiring.py` (flag-off no-op + no-import + no-connect;
  enabled fail-closed for missing/unsafe path, missing/live provider, build exception; ready path
  with stub builder; per-cycle seam no-op vs inert empty-records run; broker-free / order-safety /
  `api_server.py` untouched). The pre-existing `test_no_live_integration.py` guard was updated so
  `main.py` is the one allowed **lazy** integration point while `api_server.py` and every other
  live module remain at **zero** `bot.universe` references.
