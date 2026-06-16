# universe.db — Schema (Dynamic Universe v1 shadow)

> Research/operational-shadow store, **separate from regime.db and backtest.db**.
> Operational execution state is never merged in. Created by an additive
> `PRAGMA user_version` migration framework (`bot/universe/db.py` + `migrations.py`).
> `universe.db` is gitignored (`*.db`) and never committed.

## Migration framework

* `bot.universe.db.migrate(db_path)` applies migrations whose target > the DB's current
  `user_version`, in order. Each migration is applied **atomically** inside one explicit
  transaction (P2-1): the connection runs in manual mode (`isolation_level = None`) and
  each migration is wrapped in `BEGIN IMMEDIATE` → (every schema/index statement) →
  `PRAGMA user_version = <target>` → `COMMIT`. On any error the transaction is rolled
  back — leaving **no partial schema** and `user_version` **unchanged** — and the original
  exception is re-raised. This is genuine all-or-nothing: SQLite DDL *and* `PRAGMA
  user_version` are transactional and roll back together (verified in
  `tests/universe/test_migrations.py`). `executescript()` is deliberately **not** used (it
  forces an implicit COMMIT that would defeat the explicit transaction boundary).
* **Concurrency:** `BEGIN IMMEDIATE` takes the write lock up front, so a second concurrent
  migration blocks up to the connection `timeout` and then fails with
  `sqlite3.OperationalError` (database is locked) without creating an inconsistent schema.
* **Idempotent**: rerunning a fully-migrated DB is a no-op. Migrations are append-only;
  never edit a released migration (add a new `(version, [stmts])` tuple).
* Current schema version: **3** (v2 = Pre-Enable R1; v3 = Pre-Enable R1.1 authoritative
  position continuity, back-fill corrected in place by R1.2 / P2-B, and the
  `transition_snapshot_*` history columns added in place by R1.3 / Finding 2 — all strictly
  additive; see below). No schema v4 is added: v3 is unreleased / never run in production.

## Tables (v1)

### canonical_instruments (broker-neutral master identity)
```text
canonical_instrument_id PK   e.g. US_AAPL, LSE_BARC, EU_SU  (region from currency)
display_symbol, name, asset_class, sector, industry, exchange, currency, timezone,
research_symbol,
administratively_active INT (operator on/off), hard_disabled INT (AUTHORITATIVE),
disabled_reason, primary_gateway TEXT NOT NULL DEFAULT 'IBKR',
created_at, updated_at
```
`sector`/`industry` are NULL after a real seed (absent in instruments.json) — the
evaluator records `sector_unknown` rather than silently passing the sector cap.

### gateway_map_ibkr (per canonical id)
```text
canonical_instrument_id PK→canonical, conId, symbol, secType, exchange, primaryExchange,
currency, tradingClass, minTick, lotSize,
verification_status (seed → 'CONFIG_DERIVED'; no conId in config), verified_at
```

### gateway_map_ig (per canonical id — ALWAYS order-blocked in v1)
```text
canonical_instrument_id PK→canonical, epic, instrument_type, currency,
verification_status (always 'UNVERIFIED' in v1),
order_routing_blocked INT NOT NULL DEFAULT 1  (registry forces =1; cannot be unblocked here),
verified_at
```

### candidate_sources (AUTO / TTI / MANUAL)
```text
candidate_id PK, canonical_instrument_id→canonical, source, source_reference,
added_at, effective_trading_date, expires_after_trading_date, reason_codes,
operator_notes, active INT, created_by, created_at, updated_at
```
TTI/MANUAL candidates expire when `expires_after_trading_date < trading_date`
(`Registry.expire_candidates`, TTL = 5 completed sessions per policy).

### universe_state (current state per instrument)
```text
canonical_instrument_id PK→canonical, current_state, previous_state, reason_codes,
consecutive_passes, consecutive_failures, eligible_since, ineligible_since,
cooldown_until (DEPRECATED — see below), evaluated_trading_date,
evaluated_at, feature_snapshot_hash, evaluator_version,
-- ── v2 (R1) additive columns ────────────────────────────────────────────────
cooldown_started_trading_date        TEXT   -- exit session E (P3-2)
cooldown_sessions_remaining          INT    -- CANONICAL post-exit session count (P3-2)
cooldown_last_counted_trading_date   TEXT   -- last session a decrement was applied (idempotent/day)
cooldown_release_estimate            TEXT   -- DISPLAY-ONLY; never authoritative without an
                                            --   approved exchange calendar (left NULL in v1)
last_observed_position_status        TEXT   -- DEPRECATED v2 mirror (= latest_observed; not read)
last_observed_position_id_hash       TEXT   -- DEPRECATED v2 mirror
last_processed_position_event_id     TEXT   -- durable close-event dedup key (exactly-once, P3-9)
last_position_close_trading_date     TEXT   -- close date of the last processed exit (P3-9)
-- ── v3 (R1.1) authoritative position continuity ──────────────────────────────
latest_observed_position_status      TEXT   -- the latest raw observation (UNKNOWN MAY overwrite)
latest_observed_at                   TEXT   -- observed date of the latest observation
last_authoritative_position_status   TEXT   -- last AUTHORITATIVE status; NEVER erased by UNKNOWN
last_authoritative_position_id_hash  TEXT   -- non-sensitive hash of the authoritative position_id
last_authoritative_observed_at       TEXT   -- observed date of the last authoritative status
position_reconciliation_required     INT    -- durable block: authoritative open then flat w/o
                                            --   evidence; blocks entry, no liquidation (R1.1)
```
**Authoritative continuity (R1.1, P3-8/P3-9).** `latest_observed_*` records the most recent
observation (a non-authoritative `UNKNOWN`/stale/future/missing observation MAY overwrite it).
`last_authoritative_*` records the last status from a fresh, in-order, real-status snapshot
(`POSITION_OPEN`/`NO_POSITION`/`POSITION_EXITED`) and is **never** erased by a non-authoritative
observation — so an exit during a provider outage is still detected (exactly once) when an
evidence-bearing close arrives. An authoritative `OPEN→flat` WITHOUT explicit closure evidence
(`closed_trading_date`/`close_event_id`/`explicitly_closed`; a bare `position_id` is NOT
evidence) sets `position_reconciliation_required=1` — a durable block (state
`POSITION_RECONCILIATION` since R1.2, reason `position_reconciliation_required`) that blocks
new entry, never liquidates, holds cooldown, and clears only on an authoritative
`POSITION_OPEN` or an evidence-bearing close.

**v3 back-fill — R1.2 (P2-B) correction (v3 edited in place; unreleased / never run in
production).** On the v2→v3 upgrade the back-fill derives the authoritative anchor from the
pre-existing v2 `last_observed_position_status`, using `evaluated_trading_date` as observation
provenance: a row OPEN at the boundary keeps `last_authoritative_position_status='POSITION_OPEN'`
(+ provenance; blocked if no provenance date) so a later evidence-bearing close is detected as
an exit rather than a no-cooldown flat; a clean `NO_POSITION`/durable-exit row becomes a
non-blocked `NO_POSITION` anchor; and `UNKNOWN` / missing / malformed legacy status →
`position_reconciliation_required=1` (block, never infer flat). If R1 ran without a position
provider every row is `UNKNOWN`, so the whole universe is conservatively blocked on upgrade
until each instrument's next authoritative snapshot — the intended fail-safe.
**`cooldown_until` is DEPRECATED (P3-2).** In v1 it stored a remaining-session *count* as
text despite its date-implying name. Runtime logic no longer reads it as the count — the
authoritative source is `cooldown_sessions_remaining`. `cooldown_until` is still written as
deprecated compatibility metadata (the same count as text) and is otherwise inspected ONLY
to flag an *ambiguous legacy row*: a row that has a non-zero `cooldown_until` but a NULL
`cooldown_sessions_remaining` (e.g. migrated from v1 and not yet re-evaluated). Such a row
is **failed safe** — held in a blocked/manual-review `COOLDOWN` with reason
`cooldown_legacy_ambiguous`; the count is NOT inferred from the legacy value. The v2
migration is additive and does **not** back-fill the new columns.

**Cooldown counting (P3-2, no trading-calendar dependency).** Cooldown is counted in
COMPLETED evaluated sessions: the exit session E is not counted; the count decrements by at
most one per completed session (`cooldown_last_counted_trading_date` guards a duplicate
same-date run), and never decrements on a weekend/holiday/missing-bar session (no completed
bar → not countable), while a position is open, or while the status is UNKNOWN.

### universe_state_history (append-only — physically enforced)
```text
id PK AUTOINCREMENT, canonical_instrument_id, trading_date, prior_state, new_state,
reason_codes, feature_snapshot_json, feature_snapshot_hash, evaluator_version, created_at,
transition_snapshot_json, transition_snapshot_hash    -- v3 / R1.3 (Finding 2)
UNIQUE(canonical_instrument_id, trading_date, evaluator_version)   -- idempotency key
```
`transition_snapshot_json` / `transition_snapshot_hash` (added to the unreleased v3 migration
in place, via `ALTER TABLE … ADD COLUMN` — DDL, so the append-only triggers do not fire) hold
a deterministic serialization (+ sha256) of ALL material transition outputs: prior/new state,
sorted reason codes, feature hash, cooldown bookkeeping, and lifecycle/authoritative/
reconciliation markers. Wall-clock fields are excluded so the same logical transition always
hashes identically. This is the complete immutable record the content-aware idempotency check
compares (R1.3 / Finding 2).
v2 adds `BEFORE UPDATE`/`BEFORE DELETE` triggers (`trg_universe_history_no_update`,
`trg_universe_history_no_delete`) that `RAISE(ABORT, 'universe_state_history is
append-only')`, so a committed history row can never be rewritten or deleted (defence in
depth for P3-3).

## Atomic state + history persistence (P3-3)

`Registry.persist_transition_atomic(state, history)` writes the `universe_state` UPSERT and
the `universe_state_history` append inside ONE explicit `BEGIN IMMEDIATE` transaction
(`isolation_level = None`, no `executescript`); the two writes COMMIT together or ROLL BACK
together. The evaluator persists every transition exclusively through this method (it no
longer calls `upsert_state` + `append_history` separately). The `universe_state` write is
built from a single `_STATE_COLUMNS` registry shared with `upsert_state` (lockstep — no
column can be silently dropped from one write path).

**Content-aware idempotency (R1.1, completed in R1.3 / Finding 2).** On a duplicate
idempotency key the stored content is compared to the proposed transition INSIDE the
transaction. STRUCTURAL consistency checks run FIRST (current-state row missing, current
state older than the history row, or — same date — `current_state` ≠ stored `new_state` →
`StateHistoryConsistencyError`), so an incoherent pair can never be masked as a no-op. Then
idempotency is decided on the COMPLETE immutable `transition_snapshot_hash`:
* identical hash → idempotent no-op (returns `False`); the tx rolls back, neither table changes;
* DIVERGENT hash (ANY material output differs — `new_state` / reason codes / `feature_snapshot_hash`
  / **cooldown bookkeeping** / close-event marker / authoritative / reconciliation markers) →
  `TransitionConflictError`. This holds even when the current state has legitimately ADVANCED
  past the replayed date (the comparison is against the immutable history snapshot, not the
  current row) — closing the R1.2 gap where a cooldown-only divergence on an advanced replay
  went undetected.
A conflict/inconsistency fails closed (rollback + raise); it is never silently repaired.

## Idempotency

Enforced by the unique index `ux_history_idem` on
`(canonical_instrument_id, trading_date, evaluator_version)`. The evaluator skips any
instrument already having a history row for the `(instrument, trading_date, evaluator
version)` triple — so a same-day rerun or a restart re-run performs no duplicate work.
