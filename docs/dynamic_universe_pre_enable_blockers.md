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
| P3-2 | mandatory | `cooldown_until` stores a session count, not a date | ✅ **RESOLVED (R1)** |
| P3-3 | **mandatory** | state + history writes not atomic together | ✅ **RESOLVED (R1)** |
| P3-4 | mandatory | candidate-source table not consumed in selection | OPEN (R2) |
| P3-5 | mandatory | portfolio heat ignores inherited/open-book exposure | OPEN (R2) |
| P3-6 | mandatory | canonical-ID collision risk | OPEN (R2) |
| P3-7 | **mandatory** | IBKR mapping check ignores verification status | OPEN (R2) |
| P3-8 | **mandatory** | provider removal can preserve stale open-position state | ✅ **RESOLVED (R1)** |
| P3-9 | **mandatory** | cooldown depends on observing `POSITION_EXITED_TODAY` | ✅ **RESOLVED (R1)** |
| BLOCKER-S | mandatory | hypothetical sizing not FX-normalized (from P2-2) | OPEN (R2) |

> "mandatory" = must be resolved + independently reviewed before the flag is enabled
> outside isolated tests. **P3-2, P3-3, P3-8, P3-9 were resolved in Phase R1** (branch
> `feature/dynamic-universe-preenable-r1`); see `docs/dynamic_universe_pre_enable_r1_completion.md`.
> The foundation remains default-off and un-wired. The remaining mandatory items
> (P3-4, P3-5, P3-6, P3-7, BLOCKER-S) are deferred to Phase R2.

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
- **Owner/Status:** universe owner — ✅ **RESOLVED (R1)**.

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
- **Tests:** `tests/universe/test_atomic_persistence.py` (fault injection at each seam,
  rollback leaves both tables unchanged, idempotent replay, concurrent-writer serialisation,
  exactly-one history row, append-only triggers).
- **Owner/Status:** universe owner — ✅ **RESOLVED (R1)**.

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
- **Resolution (R1):** the legacy no-provider prior-state derivation is removed. Position
  status is resolved exclusively from an injected `PositionSnapshotProvider`; a missing
  provider, exception, timeout, malformed/unrecognised value, or stale/absent observation
  all resolve to `UNKNOWN` (fail-safe) — which blocks new entry (`EXIT_ONLY` hold), never
  forces liquidation, never assumes flat, and never decrements cooldown. A stale prior state
  is preserved only as descriptive history (`last_observed_position_status`), never as proof
  of a current position.
- **Tests:** `tests/universe/test_position_authority.py`,
  `tests/universe/test_position_lifecycle.py::test_no_provider_is_unknown_safe_not_stale_open`.
- **Owner/Status:** universe owner — ✅ **RESOLVED (R1)**.

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
- **Resolution (R1):** the state machine no longer trusts a transient status value. The
  evaluator derives an exit from an authoritative `last_observed = POSITION_OPEN` → current
  `NO_POSITION` transition WITH durable evidence (`closed_trading_date` or `position_id`), or
  an explicit durable `POSITION_EXITED` signal, and de-duplicates via a durable
  `last_processed_position_event_id` so a replayed close never restarts cooldown. Cooldown is
  NOT started from `UNKNOWN → NO_POSITION`, a no-evidence `OPEN → NO_POSITION`, a stale open
  without a provider, or a provider error. `last_observed_position_status` is persisted every
  run (including `UNKNOWN`, and `NO_POSITION` after an exit) so `OPEN→UNKNOWN→NO_POSITION`
  cannot false-trigger. The offline rehearsal now drives a durable `OPEN→NO_POSITION` +
  `closed_trading_date` exit (it no longer masks this blocker with `POSITION_EXITED_TODAY`).
- **Tests:** `tests/universe/test_exit_detection.py` (durable open→flat starts cooldown once;
  missed transient still detected; UNKNOWN→flat / no-evidence open→flat do NOT start cooldown;
  replay does not restart; new separate close starts a fresh cooldown).
- **Owner/Status:** universe owner — ✅ **RESOLVED (R1)**.

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
1. All **mandatory** items above are implemented and tested. **Phase R1 resolved P3-2, P3-3,
   P3-8, P3-9** (state/history atomicity, authoritative position contract, durable
   exactly-once exit, session-based cooldown). **Still OPEN for Phase R2:** P3-4
   (candidate-source wiring), P3-5 (inherited/open-book portfolio heat), P3-6 (canonical
   identity), P3-7 (IBKR verification status), BLOCKER-S (FX-normalized sizing).
2. The position-status seam and state/history atomicity are independently reviewed (R1);
   candidate-source wiring and canonical identity remain to be reviewed (R2).
3. A separate runtime-wiring change (into `main.py`/scheduler) is proposed and reviewed on
   its own — it is explicitly **out of scope** here.

Neither the R1 work nor this register changes the posture: the foundation remains
default-off and un-wired. Resolving these blockers does **not** authorize enablement.
