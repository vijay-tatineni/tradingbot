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
* Current schema version: **2** (v2 = Pre-Enable R1, strictly additive — see below).

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
last_observed_position_status        TEXT   -- last authoritative provider status (P3-9 detection)
last_observed_position_id_hash       TEXT   -- non-sensitive hash of the observed position_id
last_processed_position_event_id     TEXT   -- durable close-event dedup key (exactly-once, P3-9)
last_position_close_trading_date     TEXT   -- close date of the last processed exit (P3-9)
```
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
reason_codes, feature_snapshot_json, feature_snapshot_hash, evaluator_version, created_at
UNIQUE(canonical_instrument_id, trading_date, evaluator_version)   -- idempotency key
```
v2 adds `BEFORE UPDATE`/`BEFORE DELETE` triggers (`trg_universe_history_no_update`,
`trg_universe_history_no_delete`) that `RAISE(ABORT, 'universe_state_history is
append-only')`, so a committed history row can never be rewritten or deleted (defence in
depth for P3-3).

## Atomic state + history persistence (P3-3)

`Registry.persist_transition_atomic(state, history)` writes the `universe_state` UPSERT and
the `universe_state_history` append inside ONE explicit `BEGIN IMMEDIATE` transaction
(`isolation_level = None`, no `executescript`). The history row is inserted FIRST so its
UNIQUE idempotency index gates duplicates; the two writes COMMIT together or ROLL BACK
together. A duplicate `(instrument, trading_date, evaluator_version)` rolls the whole tx
back as a no-op (neither table changes), so a retried-after-success run never duplicates
history nor double-advances counters. The evaluator persists every transition exclusively
through this method (it no longer calls `upsert_state` + `append_history` separately).

## Idempotency

Enforced by the unique index `ux_history_idem` on
`(canonical_instrument_id, trading_date, evaluator_version)`. The evaluator skips any
instrument already having a history row for the `(instrument, trading_date, evaluator
version)` triple — so a same-day rerun or a restart re-run performs no duplicate work.
