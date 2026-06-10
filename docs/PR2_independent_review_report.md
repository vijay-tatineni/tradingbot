# Independent Senior Review — Draft PR #2

**Dynamic Universe IBKR Shadow v1 — foundation + review-hardening (disabled, un-wired)**

Reviewer posture: independent senior code reviewer. Review-only. No code, commits,
branches, services, config, or PR state were modified.

---

## 1. Reviewed commit range

```
base  breakout-strategy @ ffd6d23
head  feature/dynamic-universe-ibkr-shadow-v1-hardening @ 5f40b63
commits (exactly 3, no unrelated history):
  4d3c046  Dynamic Universe v1: IBKR-only shadow foundation (additive, feature-flagged)
  82814cd  Dynamic Universe shadow: log/persist §9 hypothetical contention outputs
  5f40b63  Dynamic Universe shadow hardening: cooldown/seam/corp-action/scheduler/rehearsal
```
`git rev-list --count ffd6d23..5f40b63` = **3**. Confirmed.

Review performed in an isolated detached worktree (`/tmp/pr2-review @ 5f40b63`) and a
clean base worktree (`/tmp/pr2-base @ ffd6d23`). The operational checkout
`/root/trading` was left untouched (`breakout-strategy`, clean).

## 2. Diff scope

35 files, **+3901 / -0** — purely additive (zero deletions, zero edits to existing
executable code other than additive entries in `bot/regime/flags.py`).

In-scope: `bot/universe/*` (12), `bot/regime/flags.py` (additive flag entry only),
`tests/universe/*` (13), `docs/dynamic_universe_*` (6),
`experiments/dynamic_universe_ibkr_shadow_v1.md` (1).

**Out of declared scope (3 files, documentation only — see P3-1):**
```
docs/ig_service_pause_runbook.md        (future IG maintenance runbook; NOT executed)
docs/ig_shutdown_readiness_audit.md     (IG readiness audit record)
docs/universe_db_schema.md              (this feature's schema doc; not dynamic_universe_ prefix)
```
None contain code or alter any runtime/config. Not merge-blocking.

No `.db` file is committed; `*.db` is gitignored (`.gitignore:2,46`).

## 3. Focused-test result

`python3 -m pytest tests/universe/ -q` → **82 passed in 11.91s**. Reproduced
independently in the isolated worktree.

## 4. Full-suite result

`python3 -m pytest tests/ -q` → **4 failed, 1414 passed** (367 warnings, all pre-existing
`datetime.utcnow()` deprecations unrelated to this PR).

## 5. Baseline-failure comparison

The four failures are exactly the expected set, all in `tests/test_breakout_indicators.py`:
```
test_entry_signal_requires_all_four_conditions
test_warmup_date_matches_audit[AAPL-2025-01-06]
test_warmup_date_matches_audit[NVTS-2025-02-06]
test_warmup_date_matches_audit[SGLN-2025-01-08]
```
Reproduced on the clean base `ffd6d23` worktree: **identical 4 failures**, same names,
same exception class `pandas.errors.DatabaseError`, same root cause
`no such table: ohlcv` (missing market-data DB — an environment/fixture issue, not a code
defect). Base run: `4 failed, 4 passed`.

**Conclusion: zero new or different failures. No regression introduced by this PR.**

---

## 6. P0 findings (immediate live/security risk)

**None.** The foundation is default-off, un-wired into `main.py`, imports no broker/data
provider, and performs no DB write on the flag-off path.

## 7. P1 findings (merge blockers)

**None.** Nothing in the PR alters live strategy, execution, broker, config, risk, or
service startup. All checks for an inert, disabled, broker-free foundation pass.

## 8. P2 findings (should fix before future enablement)

### P2-1 — Migration "single transaction" atomicity is not actually provided
- **File/line:** `bot/universe/db.py:45-67` (and `docs/universe_db_schema.md` "Migration
  framework"; `bot/universe/migrations.py`).
- **Finding:** `migrate()` runs each migration's DDL statements then bumps
  `PRAGMA user_version`. The docstring states *"Each migration runs in a single
  transaction; user_version is bumped only after its statements succeed"* and the schema
  doc says *"each in a transaction."* Empirically, under Python's default
  `isolation_level=""` (verified on this env, Python 3.12.3), `CREATE TABLE` and `PRAGMA`
  execute in **autocommit** mode — `conn.in_transaction` is `False` after each, so there
  is **no enclosing transaction**. A simulated mid-migration failure left the
  already-created table committed with `user_version` still 0.
- **Impact:** For v1 this is harmless because every statement is `CREATE … IF NOT EXISTS`
  (re-run is a safe no-op and re-bumps the version). But the documented all-or-nothing
  guarantee is false, and a future non-idempotent migration (e.g. `ALTER TABLE`, data
  backfill, `INSERT`) appended to `MIGRATIONS` would silently rely on atomicity that does
  not exist and could leave a half-applied schema.
- **Evidence:** `in_transaction after CREATE: False`, `in_transaction after PRAGMA:
  False`; failed-migration probe → `t1 exists … user_version = 0`. Only the `IF NOT
  EXISTS` idempotency (not a transaction) preserves correctness.
- **Recommended correction:** Wrap each migration's statements in an explicit transaction
  (e.g. `conn.execute("BEGIN"); … ; conn.execute(f"PRAGMA user_version={int(target)}");
  conn.commit()` with rollback on exception, or `conn.isolation_level=None` + explicit
  `BEGIN`/`COMMIT`). Note: `PRAGMA user_version` is legal inside a transaction. Alternatively,
  correct the docstring/schema doc to state the guarantee is idempotent-replay via
  `IF NOT EXISTS`, not transactional atomicity — and add a rule that all future migrations
  must remain idempotent.

### P2-2 — ADV20/price thresholds compared against un-converted local currency while labelled USD
- **File/line:** `bot/universe/evaluator.py:152` (`adv20_usd = (close*volume).mean()`),
  `:146-148` (`price = close`); thresholds `bot/universe/params.py:20-21`
  (`MIN_PRICE=10.0`, `MIN_ADV20_USD=20_000_000.0`); predicate
  `bot/universe/eligibility.py:62-68`.
- **Finding:** `adv20_usd` is computed as `close × volume` in the instrument's **local
  currency** with **no FX conversion**, then compared to `MIN_ADV20_USD` ($20M). Likewise
  the raw local `close` is compared to `MIN_PRICE` ($10). The snapshot key and threshold
  both carry a `_usd` / `$` label they do not satisfy for non-USD instruments (the seeded
  set includes GBP/EUR names — BARC, SU, ANTO, SGLN, SSLN, …).
- **Impact:** Shadow-only (no live effect). But task §10 explicitly requires the
  implementation *not silently claim currency-normalized eligibility if no FX conversion
  exists*. A GBP instrument's ADV in GBP being treated as USD-equivalent biases the
  eligibility gate. The evaluator docstring acknowledges a single-currency approximation
  for **risk sizing**, but the **eligibility** field naming still asserts USD.
- **Evidence:** `evaluator.py:152` multiplies the local `close` by `volume` with no FX
  step; `eligibility.py:67` compares directly to `MIN_ADV20_USD`.
- **Recommended correction:** Before enablement, either (a) apply an explicit FX
  conversion to a USD-equivalent ADV/price (recording the FX source/date), or (b) rename
  the field/threshold to local-currency semantics and record a `currency_unconverted`
  reason code so the snapshot cannot imply normalization that did not happen. Restricting
  v1's evaluated set to USD instruments would also need to be enforced, not assumed.

## 9. P3 findings (documentation / test / latent-quality)

### P3-1 — Three files outside declared review scope (documentation only)
`docs/ig_service_pause_runbook.md`, `docs/ig_shutdown_readiness_audit.md`,
`docs/universe_db_schema.md`. The experiment doc's deliverable map (Workstream 2 /
Workstream 3) attributes them to this task, and all three are inert documentation. Not a
blocker; reported per the scope-verification instruction. **Recommendation:** either
widen the stated PR scope to include them or split the IG-runbook docs into their own PR.

### P3-2 — `universe_state.cooldown_until` stores an integer session-count, not a date
- `bot/universe/migrations.py:100`; written `str(outcome.cooldown_remaining)`
  (`evaluator.py:195`); read `int(prior["cooldown_until"])` (`evaluator.py:126`).
- The column name implies a timestamp/date; it actually holds a remaining-session count.
  This is correctly documented in `universe_db_schema.md` ("v1: remaining-session count as
  text"), but the misleading name invites a future date-comparison bug.
- **Recommendation:** rename to `cooldown_remaining_sessions` in the next additive
  migration, or add a column comment.

### P3-3 — `upsert_state` and `append_history` are separate autocommit writes (no shared transaction)
- `bot/universe/evaluator.py:188-206`; `registry.py:223-298`. Idempotency is keyed solely
  on the **history** row (`has_history`, `evaluator.py:98`). A crash between the state
  upsert and the history append would leave `universe_state` mutated (hysteresis/cooldown
  counters advanced) with no history row, so the next run re-evaluates the same date and
  **double-advances** the counters.
- Shadow-only and a narrow window, but it weakens the stated idempotency guarantee.
- **Recommendation:** write the history row first (or both in one transaction) so the
  idempotency key is established before mutable state is changed.

### P3-4 — Candidate sources (AUTO/TTI/MANUAL) are recorded and TTL-expired but not consulted in selection
- `bot/universe/evaluator.py:264-302` selects from **all** evaluated instruments that
  reach `ENTRY_ELIGIBLE` with `entry_signal`; the `candidate_sources` table is only
  expired (`maybe_run:92`), never read during contention.
- The §12 invariants ("identical rules", "source metadata cannot bypass eligibility",
  "inactive candidates cannot affect selection") therefore hold trivially/vacuously. Fine
  for an inert foundation, but the candidate-source concept is effectively decoupled from
  selection in v1 — worth flagging so a future enablement wires it deliberately rather
  than assuming it is already active.

### P3-5 — Portfolio-heat probe ignores inherited/open-position risk
- `bot/universe/evaluator.py:277,293`: `heat_used` starts at `0.0` and accumulates only
  **new** selections; existing open/`EXIT_ONLY` positions consume a slot but contribute no
  risk to the heat sum. Correct under the frozen params (`5×0.5% = 2.5%`, slot cap binds
  first — documented in `dynamic_universe_shadow_hardening_v1.md §5`), but the heat cap is
  not a true *aggregate* including inherited exposure.
- **Recommendation:** when the feature is enabled with real positions, seed `heat_used`
  with the open book's risk so heat can bind independently of the slot cap.

### P3-6 — `canonical_id` collision keyed on (currency-region, symbol) only
- `bot/universe/seed.py:31-41`: id = `{US|LSE|EU}_{symbol}` where region derives from
  **currency** (not exchange or `conId`). Two distinct securities sharing
  symbol+currency+region would silently merge via `ON CONFLICT … DO UPDATE`. Low risk for
  the curated ~29-instrument set, but the merge is silent.
- **Recommendation:** incorporate exchange (or a verified broker id) into the canonical id,
  or add a guard that refuses to merge rows whose `name`/`exchange` differ.

### P3-7 — IBKR `ibkr_mapping_ok` ignores `verification_status`
- `bot/universe/evaluator.py:131`: `ibkr_ok = mapping is not None and primary_gateway ==
  "IBKR"`. A merely `CONFIG_DERIVED` (un-verified, no `conId`) IBKR mapping satisfies the
  eligibility check. Harmless in v1 (no execution path whatsoever), but if IBKR ever
  becomes order-capable this check must additionally require `verification_status` to be
  verified.

### P3-8 — Position-status seam depends on the provider; legacy fallback can persist stale open-states (task §8)
- **File/line:** `bot/universe/evaluator.py:230-243` (`_position_ctx`),
  `:237` (`exited = status == POSITION_EXITED_TODAY`), `:240-242` (legacy branch),
  `bot/universe/state_machine.py:91` (`if exited:` is the only cooldown trigger).
- **Finding (two coupled gaps, both shadow-only):**
  1. **Stale open-state across a provider→no-provider transition.** `POSITION_OPEN` /
     `EXIT_ONLY` are only ever *written* by a provider-driven run. The legacy fallback
     (no provider injected) derives `has_open = prior_state in (POSITION_OPEN, EXIT_ONLY)`
     from the persisted prior state. So if a provider drives an instrument to
     `POSITION_OPEN` and a later run is constructed **without** a provider, the fallback
     reads the stale `prior_state` and keeps the instrument `POSITION_OPEN`/`EXIT_ONLY`
     indefinitely unless a hypothetical `trend_break` happens to fire — exactly the
     staleness §8 names.
  2. **Cooldown bypass via the provider contract.** Post-exit cooldown is triggered only
     by `exited=True`, which with a provider is set *only* on `POSITION_EXITED_TODAY`. A
     provider that transitions `POSITION_OPEN → NO_POSITION` directly (reporting current
     status, never a one-day "exited today") skips cooldown entirely. The rehearsal always
     injects `POSITION_EXITED_TODAY` for exactly one session (Scenario D), which masks this
     dependency in the test suite.
- **Impact:** Shadow-only, flag-off, no routing — does **not** block the inert merge. But
  the lifecycle's correctness silently depends on (a) never mixing provider and
  no-provider runs for the same instrument, and (b) the provider emitting
  `POSITION_EXITED_TODAY` exactly once on the exit day. Neither contract is enforced or
  tested for its negative case.
- **Recommended correction (before enablement):** make the seam's contract explicit and
  enforced — e.g. derive `exited` from an observed `POSITION_OPEN → NO_POSITION` edge
  rather than trusting a transient `EXITED_TODAY` flag, and either forbid the
  provider→no-provider transition or have the legacy fallback refuse to *originate* an open
  state it cannot confirm. Add tests for the `OPEN → NO_POSITION` (no `EXITED_TODAY`) path
  and the provider-removed path.

## 10. Documentation contradictions

No substantive contradictions found between code, tests, and docs on the high-risk items:

- **E+4 release vs E+5 entry-eligible:** consistent across `state_machine.py:83-101`,
  `state_transitions_v1.md` ("Cooldown"), `shadow_hardening_v1.md §1`, and the rehearsal.
  Verified by tracing: `in_cooldown` is itself a blocking eligibility reason, so the
  2-pass hysteresis counter resets during cooldown → earliest `ENTRY_ELIGIBLE` is E+5,
  cooldown *block* released at E+4. Code, doc, and rehearsal agree.
- **Corp-action shadow vs paper/live:** consistent; default mode is fail-closed
  (`eligibility.py:21` default `ELIGIBILITY_MODE_PAPER_LIVE`), invalid mode raises
  (`:46-47`), disjoint reason codes, anomaly blocks in both modes.
- **Position-provider fallback:** consistent (`evaluator.py:230-243`, state-transitions
  "Position status seam").
- **IBKR-primary metadata vs execution / IG routing-blocked:** consistent; IG
  `order_routing_blocked` is force-set to 1 in `registry.upsert_gateway_ig` even if a row
  is written unblocked (`registry.py:139,144`), matching `gateway_partition_v1.md`.
- **Scheduler retry / portfolio heat:** consistent with code.

The only doc/code mismatch is the migration **atomicity** wording (reported as **P2-1** —
it is both a latent code gap and an inaccurate doc claim).

## 11. Security observations

- **SQL injection:** all queries use `?` placeholders. The sole interpolated SQL is
  `PRAGMA user_version = {int(target)}` (`db.py:61`) where `target` is an `int()`-cast
  literal from the controlled `MIGRATIONS` list, never user input. No injection vector.
- **Dynamic SQL / unsafe deserialization:** none. JSON is `json.dumps/loads` of the
  module's own data; no `pickle`/`eval`.
- **Path traversal:** `db_path` is caller-supplied; default is `REPO_ROOT/universe.db`.
  No user-controlled path joins. `db.connect` is only ever called with the variable
  `db_path` (asserted by `test_no_live_integration.py:80-99`).
- **Secret leakage:** shadow logs and persisted snapshots contain only symbols, states,
  reason codes, and hypothetical sizing — **no credentials, account IDs, or P&L**. §9
  logging (`evaluator.py:304-317`) emits SELECT/REJECT with qty/risk/notional only; no
  PF/Sharpe/returns (guarded by a rehearsal test).
- **DB isolation:** the universe store opens only its own `db_path`; no hard-coded
  `positions.db`/`regime.db`/`backtest.db` connect target. `*.db` gitignored; none
  committed. History table is append-only with a uniqueness idempotency index
  (`ux_history_idem`); duplicate inserts raise `IntegrityError` and are swallowed only for
  the idempotency conflict, other errors propagate.
- **Accidental activation:** the flag is registered with `SAFE_DEFAULTS=False`, absent
  from every config file, validated against `KNOWN_FLAGS`, and not in the dependency
  graph. Malformed/missing config resolves to false; an unknown flag raises at startup.

## 12. Final verdict

```
APPROVE_WITH_NON_BLOCKING_FINDINGS
```

The PR is a genuinely **default-off, un-wired, broker-free, additive disabled
foundation**. It does not change current strategy behavior, live execution, broker
behavior, configuration, risk settings, or service startup. There are no P0 or P1
findings. The focused suite passes (82/82); the full suite introduces zero new failures
(the 4 failures are pre-existing on the clean base, environment-driven). It is technically
suitable to merge into `breakout-strategy` **while the flag remains false, the feature
remains un-wired, and no deployment occurs**. The verdict does **not** approve enabling or
deploying the feature.

The two P2 findings (migration atomicity guarantee, currency normalization of
eligibility) are **non-blocking for the inert merge** but **must be resolved before the
flag is ever enabled**.

## 13. Exact required corrections

None are required to merge the disabled foundation. Before any future enablement:
1. **P2-1** — make migrations genuinely transactional (or correct the atomicity claim and
   mandate idempotent migrations).
2. **P2-2** — apply real FX normalization for ADV20/price eligibility, or rename fields to
   local-currency and add an `unconverted`-currency reason code (and enforce a USD-only set
   if relied upon).
Recommended (P3) before enablement: P3-8 (make the position-status seam contract explicit
— stale open-state on provider→no-provider transition, and cooldown bypass when no
`EXITED_TODAY` is emitted), P3-3 (write history before state), P3-5 (seed heat with
open-book risk), P3-4 (wire candidate sources into selection deliberately).

## 14. Confirmation — no changes made

No files, services, configuration, branches, or PR state were modified. No code was
edited, no commit amended/created, nothing pushed, no merge/ready/auto-merge/deploy, no
service restarted, no live config altered. All work was done in disposable detached
worktrees (`/tmp/pr2-review`, `/tmp/pr2-base`); the operational checkout `/root/trading`
remains on `breakout-strategy` with a clean working tree. (The two temporary worktrees can
be removed with `git worktree remove`.)
