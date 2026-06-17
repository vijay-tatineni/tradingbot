# Dynamic Universe Pre-Enable R2B — Persisted Candidate-Source Integration

**Status: RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE.**
**P3-4 (candidate-source runtime integration): RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE.**
Independent frozen-scope review accepted: `R2B_APPROVED_FOR_DISABLED_MERGE` (P0/P1/P2 = 0;
P3 = 4). Resolved only for merging while the feature remains default-off, un-wired and not
migrated in production. Runtime enablement remains blocked by the four R2B pre-enable
residuals recorded below (`R2B-P3-1..4`) and by the open R2C blockers **P3-5 (inherited/
open-book portfolio heat)** and **BLOCKER-S (FX-normalized sizing)**. The feature remains
**disabled** (`enable_dynamic_universe_shadow=False`), the candidate gate is **default-off**
(`require_candidate_source=False`), and nothing is wired into `main.py` / `api_server.py`. No
deployment, service restart, production migration, or broker call is part of this change.

## Scope
Implements only P3-4 for sources MANUAL / TTI / AUTO. Does NOT implement portfolio heat,
FX-normalized sizing, runtime startup wiring, scheduler activation, paper trading, or broker
routing.

## What was added
| Area | File | Summary |
|------|------|---------|
| Schema v6 | `bot/universe/migrations.py` | Additive `candidates` + append-only `candidate_audit` (triggers) + uniqueness/lookup indexes. v1–v5 unchanged. |
| Candidate store | `bot/universe/candidate_store.py` | `submit_candidate_atomic`, `submit_auto_batch_atomic`, `deactivate_candidate_atomic`, `tick_ttl_atomic`, `active_candidates`, `effective_candidates`; `CandidateConflictError`; frozen precedence. All BEGIN IMMEDIATE, broker-free, fail-closed. |
| Reason codes | `bot/universe/models.py` | `candidate_*` / `suppressed_by_higher_precedence_source` gate codes (added to `BLOCKING_REASONS`). |
| Selection wiring | `bot/universe/evaluator.py` | Default-off `require_candidate_source`: NEW-entry contention consumes `effective_candidates` only; read-then-count TTL tick; open-position management untouched. |
| Tests | `tests/universe/test_r2b_{candidates,ttl,auto_batch,migration,selection}.py` | Precedence, identity/listing, TTL, AUTO batches, resubmission, selection, rollback/concurrency, isolation. |
| Docs | `docs/universe_db_schema.md`, `dynamic_universe_state_transitions_v1.md`, `dynamic_universe_shadow_operations.md`, `dynamic_universe_pre_enable_blockers.md` | v6 schema, gate codes, operations, blocker status. |

## Candidate data model
`candidates`: candidate_id, submission_key, source, source_candidate_key, instrument_uid,
listing_uid, submitted/effective_from trading dates, status
(ACTIVE/EXPIRED/SUPERSEDED/DEACTIVATED/REJECTED), ttl_sessions_remaining,
last_counted_trading_date, superseded_by_candidate_id, deactivated_at, deactivation_reason,
source_payload_hash, resolver_version, generation_trading_date, generation_batch_id (AUTO),
timestamps. Only a deterministic hash + non-sensitive audit metadata is stored (no credentials,
account ids, tokens, or raw payloads).

## Identity / listing enforcement
A candidate is usable only with a VERIFIED `instrument_uid` and an ACTIVE `listing_uid` that
belongs to it (R2A-1). Ticker-only → `candidate_identity_unresolved`; unknown instrument →
`candidate_identity_unresolved`; unknown/wrong/inactive listing → `candidate_listing_unverified`;
missing listing on a (possibly multi-listing) instrument → `candidate_listing_ambiguous`.
Never resolves/merges by ticker; invalid submissions are persisted REJECTED and audited.

## Precedence
Frozen MANUAL > TTI > AUTO; at most one effective candidate per instrument. Suppressed
lower-precedence candidates are retained/auditable. §4 conservative rule: an ACTIVE
higher-precedence candidate that fails identity/listing validation BLOCKS the instrument with
`candidate_source_conflict` — never a silent fall-through to a different listing.

## TTL (frozen, read-then-count, documented E/E+1)
MANUAL/TTI default TTL = 5 completed sessions; AUTO valid only for its generation session.
The evaluator reads `effective_candidates` FIRST, then `tick_ttl_atomic` counts the completed
session. A candidate effective at E with TTL=5 is EFFECTIVE on E, E+1, E+2, E+3, E+4 and
EXPIRED from E+5. The tick is idempotent per date (`last_counted_trading_date`): duplicate
same-date runs, missing-bar/weekend/non-sessions, and pre-session outages never double-count.

## AUTO-batch behavior
AUTO candidates carry `generation_trading_date` + `generation_batch_id`. A new batch is
persisted atomically and prior-session AUTO candidates not in the new batch are DEACTIVATED
(retained/audited) — all in one `BEGIN IMMEDIATE`. A failed batch never partially replaces the
prior batch. Identical batch replay is idempotent; divergent replay raises
`CandidateConflictError`.

## Resubmission / supersession
Candidate identity is immutable. A resubmission (new submission for the same source /
source_candidate_key / instrument_uid / listing_uid) creates a NEW candidate_id, marks the
prior ACTIVE row SUPERSEDED, sets `superseded_by_candidate_id`, and audits both. Identical
replay of a submission key is a no-op; the same key with divergent immutable content raises
`CandidateConflictError`.

## Audit
Append-only `candidate_audit` currently persists:

* `SUBMITTED`
* `ACTIVATED`
* `REJECTED`
* `SUPERSEDED`
* `DEACTIVATED`
* `EXPIRED`

`SUPPRESSED` and `SELECTED_EFFECTIVE` are defined but are **not** currently persisted on the
read/selection path (they are not recorded by any write path today — see residual
**R2B-P3-3**). UPDATE/DELETE on `candidate_audit` are physically rejected by triggers.

## Fail-closed behavior
Blocks selection (NEW entry only) for: unverified instrument/listing, ambiguous listing,
conflicting active higher-precedence sources, stale effective date, invalid TTL state. Never
forces liquidation; never calls a broker. Reason codes emitted today on the selection path:
`candidate_identity_unresolved`, `candidate_listing_unverified`, `candidate_listing_ambiguous`,
`candidate_source_conflict`, `candidate_expired`, `candidate_inactive`.

`candidate_store_unavailable` and `candidate_malformed` reason codes are **defined** (and in
`BLOCKING_REASONS`) but are **not yet emitted**: candidate-store/DB read failures and
malformed/unknown persisted rows currently propagate as exceptions rather than a stable
reason-coded block. Safety remains fail-closed (the failure occurs before contention and the
TTL mutation, on a pure-read path, and no broker is reachable), but converting these to stable
blocked results is a pre-enable residual — see **R2B-P3-1** and **R2B-P3-2**.

## Selection-store-only
The evaluator consumes candidates ONLY via `effective_candidates(trading_date)`. Raw
AUTO/TTI/MANUAL source output cannot bypass the store (tested). Candidate presence is necessary
but NOT sufficient — identity/mapping/eligibility/cooldown/reconciliation/contention gates all
still apply, and candidate expiry affects NEW entries only (open positions keep being managed).

## Tests / baseline
* `pytest tests/universe` — **354 passed** (308 baseline + 46 new).
* `pytest tests` (compared against `breakout-strategy @ 83a41d3` in identical isolated
  worktrees):
  * Head: **1686 passed, 4 failed.**
  * Base: **1640 passed, 4 failed.**

  All failures are confined to `tests/test_breakout_indicators.py` and reproduce as
  `pandas DatabaseError: no such table: ohlcv`. This is the repository's pre-existing
  missing-fixture/environment issue and is unrelated to R2B. The delta of 46 passing tests is
  exactly the new R2B universe suite; no new failure was introduced outside that file.

## Known limitations (NOT addressed here)
R2C blockers remain open: P3-5 (inherited/open-book portfolio heat), BLOCKER-S (FX-normalized
sizing). No runtime wiring, scheduler activation, shadow-mode/paper-trading enablement, or
broker routing. `SELECTED_EFFECTIVE` and `SUPPRESSED` audit events are defined but are not
currently persisted on any path; per-session selection auditing is left to the (default-off)
evaluator integration and is not exercised in production (feature disabled) — see residual
**R2B-P3-3**.

## Independent review — accepted residuals (R2B pre-enable)
The independent frozen-scope review returned `R2B_APPROVED_FOR_DISABLED_MERGE` with four P3
findings. P3-4 is therefore **RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE**: resolved only for
merging while the feature remains default-off, un-wired and not migrated in production. Runtime
enablement remains blocked by the residuals below, each **OPEN — mandatory before runtime
enablement**.

### R2B-P3-1 — candidate-store error observability
`candidate_store_unavailable` and `candidate_malformed` reason codes exist, but candidate
DB/read failures currently propagate as exceptions. Safety remains fail-closed because failure
occurs before contention and TTL mutation, but a wired scheduler could crash rather than emit a
stable blocked result. Before enablement: catch supported candidate-store/database read
failures; convert them to `candidate_store_unavailable`; convert malformed/unknown persisted
rows to `candidate_malformed`; block new entry without crashing the evaluation cycle; add
regression tests.

### R2B-P3-2 — unknown persisted source/status
Supported write APIs reject unknown sources, but a raw/injected ACTIVE row with an unknown
source is not defensively rejected by `effective_candidates`. Before enablement: add enum
CHECK constraints where migration policy permits; and/or defensively reject unknown
source/status as `candidate_malformed`; add a raw-row regression test.

### R2B-P3-3 — selection audit completeness
`SUPPRESSED` and `SELECTED_EFFECTIVE` are defined but not persisted. Before enablement, decide
and implement one frozen policy: persist deterministic selection/suppression audit events
without creating duplicate read-path noise; or explicitly remove those event types from the
promised audit contract.

### R2B-P3-4 — transaction consistency and TTL fault coverage
`deactivate_candidate_atomic` currently uses a deferred transaction rather than
`BEGIN IMMEDIATE`. Atomicity is preserved, but the lock is acquired later than the other
multi-row lifecycle APIs. Before enablement: use explicit `BEGIN IMMEDIATE`; add a TTL-update
fault-injection rollback test; verify concurrent deactivation/TTL operations fail or serialize
safely.
