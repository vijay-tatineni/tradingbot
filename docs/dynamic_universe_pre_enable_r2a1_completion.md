# Dynamic Universe Pre-Enable R2A-1 — Canonical Identity & Verified Broker Mappings

**Status: IMPLEMENTED — awaiting independent review.**
**P3-6 (canonical identity) and P3-7 (verified broker mappings): IMPLEMENTED — awaiting
independent review.** Not marked resolved yet. R2B and R2C blockers remain open. The feature
remains **disabled** (`enable_dynamic_universe_shadow=False`) and the new pre-entry gate is
**default-off** (`enforce_verified_identity=False`) and **un-wired** into `main.py` /
`api_server.py`. No deployment, service restart, production migration, or broker call is part
of this change.

## Frozen operator decisions implemented

Four distinct concepts, per the operator ruling:

* **`instrument_uid`** — opaque, immutable, **never** ticker-derived; the economic security.
  Derived deterministically from the verified anchor (ISIN preferred, FIGI fallback) by the
  controlled resolver, so it survives ticker renames, broker remaps, and listing migrations.
* **`listing_uid`** — opaque, immutable; one venue/currency listing. A listing migration
  creates a NEW `listing_uid` and never rewrites historical listing identity.
* **broker mapping identity** — IBKR `conId` / IG `epic` are VERIFIED MAPPING ATTRIBUTES only,
  never canonical identity.
* **`display_symbol`** — the human ticker; auditable, never an identity anchor.

Verified anchors: preferred `ISIN + MIC + currency`; supported fallback `FIGI`. No verified
anchor ⇒ `identity_unverified` ⇒ entry blocked. Instruments are never merged on matching
tickers.

Mapping eligibility states frozen to `VERIFIED_REFERENCE_MATCH`, `VERIFIED_CONFIGURED`,
`UNVERIFIED`, `STALE`, `REJECTED`, `AMBIGUOUS`. Only `VERIFIED_REFERENCE_MATCH` passes; all
else fails closed. Default reverification interval **90 calendar days**; expired ⇒ `STALE`. IG
routing is blocked regardless of mapping state.

## What was added

| Area | File | Summary |
|------|------|---------|
| Schema v5 | `bot/universe/migrations.py` | Additive `instrument_identity`, `instrument_listing`, `ibkr_mapping`, `ig_mapping`, `identity_audit` (append-only triggers); additive `canonical_instruments.instrument_uid`/`identity_status`. v1–v4 unchanged. |
| Identity domain | `bot/universe/identity.py` | Enums, immutable `IdentityReference`, broker-free `IdentityReferenceProvider` Protocol, anchor-based opaque-uid derivation, pure `verify_ibkr_mapping`, `IdentityConflictError`/`IdentityResolutionError`. |
| Identity store | `bot/universe/identity_store.py` | `resolve_identity_atomic`, `record_listing_migration_atomic`, `close_listing_atomic`, `upsert_ibkr_mapping`, `entry_identity_gate` (all SQLite-atomic, broker-free, fail-closed). |
| Reason codes | `bot/universe/models.py` | `identity_*` / `ibkr_mapping_*` / `ig_order_routing_blocked` gate codes (added to `BLOCKING_REASONS`). |
| Evaluator gate | `bot/universe/evaluator.py` | Default-off `enforce_verified_identity` folds gate blocks into eligibility; position/reconciliation logic untouched. |
| Tests | `tests/universe/test_r2a1_{identity,mappings,migration,isolation}.py` | Collisions, continuity, conflicts, missing identity, provider failures, mapping statuses/mismatches, atomicity/idempotency, migration, default-off isolation. |
| Docs | `docs/universe_db_schema.md`, `dynamic_universe_state_transitions_v1.md`, `dynamic_universe_shadow_operations.md`, `dynamic_universe_pre_enable_blockers.md` | v5 schema, gate reason codes, operational notes, blocker status. |

## Canonical identity rules (all tested)

* Same ticker / different MIC, different currency, or different verified anchor → never
  collapsed (distinct `instrument_uid`/`listing_uid`); share classes (distinct ISIN) distinct;
  ticker reuse → distinct uids.
* Ticker rename (same anchor) → same `instrument_uid`; the historical display symbol stays
  auditable in `identity_audit`.
* Listing migration → same `instrument_uid`, new `listing_uid`; old listing `valid_to` set and
  retained (`record_listing_migration_atomic`). Delist/relist retains the historical row.
* Conflicting anchor (ISIN/FIGI/name) for the same opaque key, or an active-coordinate reuse
  for a different instrument → `IdentityConflictError` (no merge, no overwrite).

## Migration behavior (v4 → v5)

Additive, atomic (single `BEGIN IMMEDIATE` in `db.migrate`), idempotent, fail-closed. Existing
v4 rows become `identity_status='UNVERIFIED'` with NULL `instrument_uid`: no fabricated
identity, no ticker-based backfill, no auto-merge. Entry stays blocked until provider-backed
resolution writes a real `instrument_uid`. An injected failure rolls back leaving v4 intact.

## Tests / baseline

* `pytest tests/universe` — **308 passed** (250 baseline + 58 new).
* `pytest tests` — **1640 passed**; the full suite introduced **no failure outside**
  `tests/test_breakout_indicators.py`. In identical isolated runs the base
  (`breakout-strategy @ 72eb691`) happened to pass while the R2A-1 head exhibited the
  repository's known **nondeterministic** breakout-test isolation/timing failures, confined
  entirely to that file. The R2A-1 production changes do not modify or import the breakout
  implementation, its tests, or backtest database handling.

## Still open (NOT addressed here)

R2B / R2C blockers (e.g. P3-4 portfolio heat / FX sizing, P3-5 candidate sourcing, BLOCKER-S),
runtime wiring, shadow-mode/paper-trading enablement, and any broker routing remain open and
out of scope for R2A-1.
