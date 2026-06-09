# universe.db — Schema (Dynamic Universe v1 shadow)

> Research/operational-shadow store, **separate from regime.db and backtest.db**.
> Operational execution state is never merged in. Created by an additive
> `PRAGMA user_version` migration framework (`bot/universe/db.py` + `migrations.py`).
> `universe.db` is gitignored (`*.db`) and never committed.

## Migration framework

* `bot.universe.db.migrate(db_path)` applies migrations whose target > the DB's current
  `user_version`, in order, each in a transaction; bumps `user_version` only on success.
* **Idempotent**: rerunning a fully-migrated DB is a no-op. Migrations are append-only;
  never edit a released migration (add a new `(version, [stmts])` tuple).
* Current schema version: **1**.

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
cooldown_until (v1: remaining-session count as text), evaluated_trading_date,
evaluated_at, feature_snapshot_hash, evaluator_version
```

### universe_state_history (append-only)
```text
id PK AUTOINCREMENT, canonical_instrument_id, trading_date, prior_state, new_state,
reason_codes, feature_snapshot_json, feature_snapshot_hash, evaluator_version, created_at
UNIQUE(canonical_instrument_id, trading_date, evaluator_version)   -- idempotency key
```

## Idempotency

Enforced by the unique index `ux_history_idem` on
`(canonical_instrument_id, trading_date, evaluator_version)`. The evaluator skips any
instrument already having a history row for the `(instrument, trading_date, evaluator
version)` triple — so a same-day rerun or a restart re-run performs no duplicate work.
