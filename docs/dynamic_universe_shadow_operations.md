# Dynamic Universe v1 — Shadow Operations

> How to migrate, seed, run (test/shadow only), inspect outputs, recover, and back up.
> The shadow foundation is non-integrated; nothing here runs in production by default.

## Migration

```python
from bot.universe.db import migrate
migrate("/path/to/universe.db")   # idempotent; creates/updates the 6 tables to version 1
```

## Registry seed (idempotent, read-only on configs)

```python
from bot.universe.seed import seed_registry
seed_registry("/path/to/universe.db")           # reads repo instruments.json + instruments_ig.json
# or seed_registry(db, instruments_path, instruments_ig_path)
```
Behaviour: creates canonical records; IBKR mappings `CONFIG_DERIVED`; **IG mappings
`UNVERIFIED` + `order_routing_blocked=1` (always)**; `primary_gateway=IBKR`;
`hard_disabled` preserved (XAUUSD/XAGUSD never enabled). Safe to rerun (pure upserts;
config files never rewritten — asserted by tests).

## Running the shadow evaluator (TEST/SHADOW ENVIRONMENT ONLY)

```python
from bot.universe.registry import Registry
from bot.universe.evaluator import ShadowEvaluator

flags = SomeFlags(enable_dynamic_universe_shadow=True)   # NEVER set in live config
ev = ShadowEvaluator(Registry(db), bars_provider, flags, equity=100_000)
ev.maybe_run("2026-06-10")        # flag false → {'ran': False, ...} no-op
```
`bars_provider(rec)` returns `{bars, corp_action_status, sector, spread, fresh_bar,
admin_paused}`; it must NOT touch a broker. The `DailyUniverseScheduler` wraps this with
post-close gating and restart-safe idempotency.

## Shadow outputs

* `universe_state` — current state, hysteresis counters, cooldown remaining, snapshot hash.
* `universe_state_history` — append-only per (instrument, trading_date, evaluator
  version), with `feature_snapshot_json` (bar_count, price, adv20, indicators_available,
  ohlc_valid, corp_action_status, sector, entry_signal, trend_break, atr14, sma50,
  primary_gateway, ibkr_mapping_ok) and reason codes.
* `maybe_run` returns per-instrument outcomes plus `selected` (with `hypothetical_order`:
  entry_price, initial_stop, qty, risk_usd, notional_usd, primary_gateway=IBKR) and
  `rejected` (with `rejected_reason`: slot_cap_reached / sector_cap_reached /
  portfolio_heat_exceeded). **No PF / Sharpe / returns / rankings are computed or logged.**

## Failure behaviour

* Missing/short bars → DATA_INELIGIBLE (insufficient_history / indicators_unavailable).
* Corporate-action data unavailable → `corp_action_data_unavailable` reason, eligibility
  FAILS (never a silent pass).
* Unknown sector → `sector_unknown` recorded (non-blocking).
* Idempotency conflict on history insert → skipped (already recorded); other DB errors
  propagate (not silently swallowed).

## Backup / restore

`universe.db` is gitignored. Back up by copying the file (and recording its SHA-256) to
a restricted directory; restore by copying back and running `migrate` (idempotent). It
holds only research/shadow state — it is never the source of live execution state.

## Provider / historical-validation status

Historical validation of the shadow universe remains **BLOCKED** pending approved
provider access — see `docs/dynamic_universe_provider_status.md`. The verdict
`TRIAL ACCESS REQUIRED BEFORE DECISION` stands; no EODHD call or ingestion is performed.
