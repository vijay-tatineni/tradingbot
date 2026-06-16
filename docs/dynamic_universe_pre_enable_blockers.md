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
enablement**, and the R2 blockers (P3-4, P3-5, P3-6, P3-7, BLOCKER-S) remain open. See
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
| P3-4 | mandatory | candidate-source table not consumed in selection | OPEN (R2) |
| P3-5 | mandatory | portfolio heat ignores inherited/open-book exposure | OPEN (R2) |
| P3-6 | mandatory | canonical-ID collision risk | OPEN (R2) |
| P3-7 | **mandatory** | IBKR mapping check ignores verification status | OPEN (R2) |
| P3-8 | **mandatory** | provider removal can preserve stale open-position state | RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE (R1.1) |
| P3-9 | **mandatory** | cooldown depends on observing `POSITION_EXITED_TODAY` | RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE (R1.1/R1.3); see residual P3-R1-A |
| P3-R1-A | **mandatory (pre-enable)** | reused explicit provider `close_event_id` can mask a second close | OPEN — mandatory before runtime enablement |
| P3-R1-B | **mandatory (pre-enable)** | legacy NULL transition-hash fallback skips some markers | OPEN — mandatory before runtime enablement |
| P3-R1-C | **mandatory (pre-enable)** | remaining test-completeness items | OPEN — mandatory before runtime enablement |
| BLOCKER-S | mandatory | hypothetical sizing not FX-normalized (from P2-2) | OPEN (R2) |

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
Status: **OPEN — mandatory before runtime enablement.** Touches P3-9's exactly-once cooldown
property; the synthetic-id path is already collision-safe (R1.3 Finding 1).

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
Status: **OPEN — mandatory before runtime enablement.** Unreachable via the runtime write path
(`persist_transition_atomic` always stores the hash); the residual lives only on the
`append_history`/raw-insert fallback. Tracked within P3-3's machinery; does not reopen P3-3.

#### P3-R1-C — remaining test completeness
```text
- explicit provider close_event_id reuse across lifecycles;
- NULL-hash replay outcome;
- explicit coverage of all transition-hash fields;
- bare POSITION_EXITED ambiguity.
```
Status: **OPEN — mandatory before runtime enablement.** Test-completeness only; the live code
paths are correct by inspection/trace.

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
- **Owner/Status:** universe owner — **OPEN**.

### P3-5 — Portfolio heat ignores inherited/open-book exposure (mandatory)
- **Risk:** the heat cap is not a true aggregate; with real inherited positions, new
  selections could be approved that breach total portfolio heat.
- **Current behavior:** `_apply_contention` seeds `heat_used = 0.0` and accumulates only
  newly selected candidates' risk; open / `EXIT_ONLY` positions consume a slot but
  contribute no risk to the heat sum. (Under the frozen `5×0.5%` params the slot cap binds
  first, so heat is not currently the binding constraint.)
- **Why the disabled merge is safe:** shadow-only; no real positions; figures are
  hypothetical.
- **Required correction before enablement:** receive a position/risk snapshot and seed
  `heat_used` with existing planned-stop risk for **EXIT_ONLY**, **POSITION_OPEN**, and
  pending accepted intents where applicable. No existing exposure may be ignored.
- **Required test:** with an injected open-book risk snapshot, heat binds on the aggregate
  (inherited + new) and rejects with `portfolio_heat_exceeded` when total exceeds the cap.
- **Owner/Status:** universe owner — **OPEN**.

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
- **Owner/Status:** universe owner — **OPEN**.

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
- **Owner/Status:** universe owner — **OPEN (mandatory)**.

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
- **Owner/Status:** universe owner — **OPEN**.

---

## Enablement gate (summary)

Before `enable_dynamic_universe_shadow` is set true **anywhere outside isolated tests**:
1. **Phase R1–R1.3 (P3-2, P3-3, P3-8, P3-9; P2-A/B/C; R1.3 Finding 1/2/3) passed the
   consolidated independent review and are `RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE`** —
   this clears them for the inert merge ONLY, and is **not** an enablement authorization.
2. The pre-enable residuals **P3-R1-A, P3-R1-B, P3-R1-C** remain **OPEN — mandatory before
   runtime enablement** (see their entries above).
3. **Still OPEN for Phase R2:** P3-4 (candidate-source wiring), P3-5 (inherited/open-book
   portfolio heat), P3-6 (canonical identity), P3-7 (IBKR verification status), BLOCKER-S
   (FX-normalized sizing).
4. A separate runtime-wiring change (into `main.py`/scheduler) is proposed and reviewed on
   its own — it is explicitly **out of scope** here.

Neither the R1–R1.3 work nor this register changes the posture: the foundation remains
default-off and un-wired. Marking these items resolved-for-disabled-merge does **not**
authorize enablement, scheduler wiring, production migration, shadow soak, paper/live trading,
or Phase R2.
