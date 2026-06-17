# Dynamic Universe Pre-Enable R2B — Persisted Candidate-Source Integration

**Status: IMPLEMENTED — awaiting independent review.**
**P3-4 (candidate-source runtime integration): IMPLEMENTED — awaiting independent review.**
Not marked resolved yet. R2C blockers remain open: **P3-5 (inherited/open-book portfolio
heat)** and **BLOCKER-S (FX-normalized sizing)**. The feature remains **disabled**
(`enable_dynamic_universe_shadow=False`), the candidate gate is **default-off**
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
Append-only `candidate_audit` records SUBMITTED / ACTIVATED / SUPPRESSED / SUPERSEDED /
EXPIRED / DEACTIVATED / REJECTED / SELECTED_EFFECTIVE; UPDATE/DELETE are physically rejected by
triggers.

## Fail-closed behavior
Blocks selection (NEW entry only) for: missing store, DB error, malformed row, unknown
source/status, unverified instrument/listing, ambiguous listing, conflicting active
higher-precedence sources, stale effective date, invalid TTL state. Never forces liquidation;
never calls a broker. Stable reason codes: `candidate_store_unavailable`,
`candidate_identity_unresolved`, `candidate_listing_unverified`, `candidate_listing_ambiguous`,
`candidate_source_conflict`, `candidate_expired`, `candidate_inactive`, `candidate_malformed`.

## Selection-store-only
The evaluator consumes candidates ONLY via `effective_candidates(trading_date)`. Raw
AUTO/TTI/MANUAL source output cannot bypass the store (tested). Candidate presence is necessary
but NOT sufficient — identity/mapping/eligibility/cooldown/reconciliation/contention gates all
still apply, and candidate expiry affects NEW entries only (open positions keep being managed).

## Tests / baseline
* `pytest tests/universe` — **354 passed** (308 baseline + 46 new).
* `pytest tests` — **1686 passed**; the full suite introduced **no failure outside**
  `tests/test_breakout_indicators.py`. Any failures inside that file are the repository's known
  nondeterministic test-isolation/timing issue and are unrelated to R2B. (Compared against
  `breakout-strategy @ 83a41d3`.)

## Known limitations (NOT addressed here)
R2C blockers remain open: P3-5 (inherited/open-book portfolio heat), BLOCKER-S (FX-normalized
sizing). No runtime wiring, scheduler activation, shadow-mode/paper-trading enablement, or
broker routing. `SELECTED_EFFECTIVE`/`SUPPRESSED` audit events are defined and recorded on
submission/expiry paths; per-session selection auditing is left to the (default-off) evaluator
integration and is not exercised in production (feature disabled).
