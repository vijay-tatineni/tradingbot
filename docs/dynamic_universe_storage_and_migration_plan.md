# Dynamic Universe — Storage Boundary & Migration-Order Plan

> **Design/planning only.** No migrations created, no tables created, no DB modified
> (read-only introspection only). Part of `design/dynamic-universe-hybrid-v1`; master index
> `experiments/dynamic_universe_hybrid_v1_DRAFT.md`. This is **Task 4**.

---

## 1. Existing databases (verified, read-only)

| DB file | Role | Representative tables (verified) | Conventions |
|---|---|---|---|
| `regime.db` | **Claude/regime experiment** + shadow | `regime_classification_cache`, `shadow_decisions`, `shadow_hypothetical_trades`, `smoothed_regime_state`, `instrument_entry_pauses`, `position_metadata`, `regime_blocked_entries` | WAL + `busy_timeout` across `bot/regime/*` |
| `positions.db` | **live operational** position state | `open_positions`, `watch_positions`, `pnl_cache` | `bot/position_tracker.py:30,88` |
| `backtest.db` | **research** | `ohlcv`, `wf_results`, `optimise_results` | `backtest/database.py:26`, WAL + 5s busy_timeout |
| `learning_loop.db` | closed-trade records | `trades` | `bot/plugins/learning_loop.py` |
| `news.db`, `layer3_silver.db`, `advisor.db` | news / scalper / advisor | — | — |

DB connection idiom to reuse (verified across `bot/regime/*`, `backtest/database.py`):
`sqlite3.connect(...)`, `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`,
row-factory dicts, idempotent `CREATE TABLE IF NOT EXISTS`, upsert via `INSERT OR REPLACE` /
`PRIMARY KEY` conflict.

---

## 2. Recommended database boundary

**Adopt the task's preferred three-store separation** — repository evidence *supports*, not
contradicts, it:

```text
universe.db            (NEW — universe identity & control plane)
   canonical_instruments            (registry)
   gateway_map_ibkr / gateway_map_ig (mappings)
   candidate_sources                (AUTO/TTI/MANUAL)
   universe_state                   (current per-instrument state)
   universe_state_history           (append-only audit)

execution.db           (NEW — execution/ops plane; OR extend positions.db)
   order_intents                    (broker-neutral intents)
   idempotency_keys                 (dedupe + UNKNOWN_PENDING_RECONCILIATION)
   unified_positions                (cross-gateway position records)
   reconciliation_events            (startup/per-cycle reconcile outcomes)

research_data.db       (NEW — versioned research data; supersedes backtest.db.ohlcv)
   raw_bars                         (raw + adjusted)
   corporate_actions                (splits/dividends/renames/delist)
   dataset_manifest                 (point-in-time snapshot + SHA-256)
```

### Why these boundaries (grounded)

* **Do NOT put universe identity/state in `regime.db`.** `regime.db` is the *experiment* DB
  for the Claude/regime work (still shadow, partly unwired per `docs/TECH_DEBT.md`). Coupling
  the canonical registry and universe-control plane to an experiment DB violates the task's
  explicit "do not couple core universe state to the Claude/regime experiment database merely
  for convenience" and would entangle a load-bearing control plane with an experimental,
  churning schema.
* **`universe.db` separate from `execution.db`.** Identity/eligibility (slow-changing,
  human-curated, audited) has a different lifecycle, backup cadence, and blast radius than
  live order/position state (high-churn, restart-sensitive). Separation limits blast radius
  and lets the registry be snapshot/version-controlled independently.
* **`execution.db` MAY be `positions.db` extended.** `positions.db` is already the live
  operational store (`open_positions`/`watch_positions`). Adding `order_intents`,
  `idempotency_keys`, `unified_positions`, `reconciliation_events` there is defensible (one
  operational DB) — an open decision (operator-decisions doc). Default recommendation: a
  dedicated `execution.db` to keep the new multi-gateway records decoupled from the existing
  single-broker tracker schema, with `unified_positions` as the cross-gateway superset of
  `open_positions`.
* **`research_data.db` separate from `backtest.db`.** `backtest.db.ohlcv` is RAW/survivor-only
  (verified). `raw_bars`/`corporate_actions`/`dataset_manifest` (provider-sourced,
  corporate-action-correct, point-in-time, hashed) supersede it for dynamic-universe research
  **without dropping** `backtest.db` (the frozen fixed-14 work depends on it). Keeping them
  separate avoids contaminating the frozen experiment's inputs.

### Single-consolidated-DB option — evaluated, not recommended

One operational DB is simpler to back up but couples identity, execution, and research
lifecycles, enlarges blast radius, and complicates the consolidation (IG instance would share
one DB). Rejected for v1 except the optional `execution.db = positions.db` merge above.

---

## 3. Migration order (M1–M7) — **text only, no files created**

All migrations are **additive** (`CREATE TABLE IF NOT EXISTS`), reuse the WAL+busy_timeout
idiom, and **never** alter `instruments.json`, the deployed `bot/guardrails.py` guard, or any
existing table. DDL below is illustrative.

Common per-migration discipline:
* **Idempotency:** `CREATE TABLE IF NOT EXISTS`; re-running a migration is a no-op.
* **Backup:** snapshot the target DB file (+ SHA-256) before applying, mirroring the deploy
  backup discipline used for `instruments.json`.
* **Rollback:** since additive, rollback = `DROP TABLE` the newly added tables (no existing
  data touched) + restore the pre-migration file hash if needed.
* **Compatibility:** existing code paths don't read the new tables until the corresponding
  runtime phase (roadmap), so each migration is safe to land ahead of runtime.
* **Dual-read/dual-write:** **not required** for M1–M6 (new tables, no existing reader). M7
  needs a dual-read window only when research code switches from `backtest.db.ohlcv` to
  `research_data.raw_bars` (see M7).

### M1 — Additive registry tables (`universe.db`)
```text
canonical_instruments(canonical_instrument_id PK, display_symbol, instrument_name,
  asset_class, sector, industry, primary_listing_mic, exchange, currency, quote_convention,
  trading_timezone, research_provider_symbol, research_provider_permid, listing_date,
  delisting_date, administratively_active, hard_disabled, disabled_reason,
  primary_execution_gateway, supported_gateways, created_at, updated_at, created_by)
INDEX (display_symbol), (exchange), (primary_execution_gateway)
UNIQUE (research_provider_permid)  -- where present
```
* FK: none (root table). Compat: pure addition. Dual-write: n/a.

### M2 — Gateway mapping tables (`universe.db`)
```text
gateway_map_ibkr(canonical_instrument_id PK FK→canonical_instruments, con_id, symbol,
  sec_type, exchange, primary_exchange, currency, trading_class, min_tick, lot_size,
  what_to_show, verified_against)
gateway_map_ig(canonical_instrument_id PK FK→canonical_instruments, epic, instrument_type,
  expiry, account_compatibility, currency, min_deal_size, min_stop_distance, value_per_point,
  margin_factor, market_hours_meta, data_entitlement, verified_against)
UNIQUE(gateway_map_ibkr.con_id) where present; UNIQUE(gateway_map_ig.epic) where resolved
```
* FK → `canonical_instruments`. Only the 10 verified EPICs populated; rest `epic='UNRESOLVED'`.

### M3 — Candidate + current-state tables (`universe.db`)
```text
candidate_sources(candidate_id PK, canonical_instrument_id FK, source, source_reference,
  added_at, effective_trading_date, expires_after_trading_date, reason_codes, operator_notes,
  active, created_by, audit_meta_json)
universe_state(canonical_instrument_id PK FK, state, since, effective_session_date,
  reason_code, reason_detail, data_pass_streak, cooldown_until_session, candidate_id, last_eval_at)
INDEX candidate_sources(canonical_instrument_id, effective_trading_date),
      candidate_sources(expires_after_trading_date), universe_state(state)
```
* FK → `canonical_instruments`. `state` constrained to the 8-state enum (CHECK or app-level).

### M4 — Append-only history (`universe.db`)
```text
universe_state_history(id PK AUTOINCREMENT, ts, canonical_instrument_id, from_state, to_state,
  event, reason_code, effective_session_date, had_open_position, position_protected, actor,
  flag_snapshot_json)
INDEX (canonical_instrument_id, ts)
```
* **Append-only** (no UPDATE/DELETE — enforced by app + review, mirroring `regime_blocked_entries`
  / `shadow_decisions`). `position_protected` asserted=1 for every "remove" transition (test #9).

### M5 — Broker-neutral intents + idempotency (`execution.db`)
```text
order_intents(intent_id PK, canonical_instrument_id, strategy_id, direction, signal_bar_date,
  risk_budget_base_currency, reference_entry_price, initial_stop, maximum_notional,
  primary_gateway, candidate_source, created_at, status)
idempotency_keys(intent_id PK, key_hash UNIQUE, state, created_at, resolved_at)
  -- key = strategy+canonical_id+direction+signal_bar_date+broker_account
  -- state ∈ {NEW, SUBMITTED, FILLED, REJECTED, UNKNOWN_PENDING_RECONCILIATION}
UNIQUE(idempotency_keys.key_hash)  -- prevents duplicate submission
```
* FK → `canonical_instruments`. `UNKNOWN_PENDING_RECONCILIATION` never auto-resubmits.

### M6 — Unified reconciliation records (`execution.db`)
```text
unified_positions(canonical_instrument_id, gateway, account, broker_position_id,
  internal_intent_id, quantity_or_deal_size, entry_price, active_stop, currency,
  position_state, last_reconciliation_at, PRIMARY KEY(canonical_instrument_id, gateway, account))
reconciliation_events(id PK AUTOINCREMENT, ts, gateway, account, canonical_instrument_id,
  event_type, detail, action_taken)   -- event_type ∈ {MATCH, MISSING_LOCAL, MISSING_BROKER, MISMATCH}
INDEX reconciliation_events(ts), unified_positions(gateway, account)
```
* Mismatch → pause via existing `InstrumentPauseRegistry` (no force-close, no duplicate).
* Compat: coexists with `positions.db.open_positions`; v1 reads broker truth at startup
  (generalised `bot/layer1.py:_reconcile_with_broker`).

### M7 — Research-data ingestion tables (`research_data.db`)
```text
raw_bars(canonical_instrument_id, timeframe, session_date, open_raw, high_raw, low_raw,
  close_raw, volume, adj_factor, close_adj, source, ingested_at, dataset_snapshot_id,
  PRIMARY KEY(canonical_instrument_id, timeframe, session_date, source))
corporate_actions(canonical_instrument_id, ex_date, action_type, ratio_or_amount,
  old_symbol, new_symbol, source, ingested_at, PRIMARY KEY(canonical_instrument_id, ex_date, action_type))
dataset_manifest(dataset_snapshot_id PK, created_at, provider, universe_as_of_date,
  n_instruments, raw_bars_sha256, corp_actions_sha256, notes)
```
* **Dual-read window required here:** research code currently reads `backtest.db.ohlcv`
  (`backtest/database.py:load_bars`). Switching to `raw_bars` must be a deliberate, gated
  cutover (roadmap Phase 8) with both readable during validation; `backtest.db` is **not**
  dropped (frozen fixed-14 dependency). Provider ingestion is **deferred to operator approval**
  (no download in this task).

---

## 4. Constraints honored

* No migration files created; no tables created; no DB modified.
* `instruments.json` and the deployed hard-disabled guard untouched.
* Universe control plane deliberately **kept out of** `regime.db`.
* Existing `backtest.db`/`positions.db` data preserved; new stores are additive supersets.
* Backup-before-migrate + additive-rollback discipline specified per migration.

Operator approval required before creating any migration or DB (see operator-decisions doc:
DB-placement decision, and `execution.db` vs `positions.db` merge).
