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

## Position-status seam (broker-free; task §3)

The evaluator optionally takes an injected `PositionSnapshotProvider`
(`get_position_status(canonical_instrument_id, trading_date) -> PositionStatus`). It is
broker-free by contract (a fixture / non-production snapshot — never a broker call) and
drives the POSITION_OPEN / EXIT_ONLY / COOLDOWN lifecycle organically. `UNKNOWN` is
fail-safe (no flat assumption, no entry, no forced liquidation, cooldown held); a
provider error is coerced to `UNKNOWN`. With no provider injected the legacy
prior-state + trend-break derivation is used.

```python
ev = ShadowEvaluator(Registry(db), bars_provider, flags, equity=100_000,
                     position_provider=my_provider)   # my_provider: NO broker access
```

## Scheduler completed-bar availability (task §5)

The fixed post-close UTC gates are conservative across both DST regimes, early closes
and half-days (they can only run LATE, never early). They cannot prove a bar EXISTS on
a holiday or that a late bar has arrived, so inject a `bar_available_fn(rec,
trading_date) -> bool`: instruments whose completed bar is not confirmed are SKIPPED and
logged (no history written) and retried idempotently next cycle. A raising check is
treated as unavailable (fail-safe skip). `maybe_run` returns `missing_bar_skipped`.

```python
sched = DailyUniverseScheduler(ev, flags, bar_available_fn=lambda rec, td: bar_exists(rec, td))
```

## Failure behaviour

* Missing/short bars → DATA_INELIGIBLE (insufficient_history / indicators_unavailable).
* Corporate-action status unknown/unavailable → **shadow** records
  `corporate_action_status_unknown` (warning, non-blocking); **paper/live** (default,
  fail-closed) records `corp_action_data_unavailable` and FAILS eligibility. A detected
  `anomaly` blocks in both. Never a silent pass. See state-transitions doc §"Corporate-
  action policy".
* Position status UNKNOWN → `position_status_unknown`, safe non-entry EXIT_ONLY hold.
* Completed bar not yet available (holiday/late) → scheduler skip + retry (no history).
* Unknown sector → `sector_unknown` recorded (non-blocking).
* Idempotency conflict on history insert → skipped (already recorded); other DB errors
  propagate (not silently swallowed).

## Offline rehearsal (task §6)

`tests/universe/rehearsal.py::run_rehearsal(db_path)` runs a deterministic, fully
offline end-to-end rehearsal on synthetic fixtures (no broker, no data provider, no live
DB, no production config writes). It exercises seed, idempotent migration rerun,
AUTO/TTI/MANUAL candidates + 5-session TTL, entry/removal hysteresis, ADMIN_PAUSED,
DATA_INELIGIBLE, HARD_DISABLED, the full POSITION_OPEN ⇄ EXIT_ONLY → COOLDOWN lifecycle
with E+1..E+3 blocking and E+4 release, slot/sector/heat contention, IBKR-primary +
IG-routing-blocked, multi-timezone scheduling, same-day idempotency, restart recovery,
missing-bar skip, corporate-action UNKNOWN warning, and position-status UNKNOWN safe
behaviour. It reports **operational metrics only** — never returns / PF / Sharpe /
winners / rankings (`format_report`). Determinism and the no-performance-metric guard
are asserted in `tests/universe/test_rehearsal.py`.

## Backup / restore

`universe.db` is gitignored. Back up by copying the file (and recording its SHA-256) to
a restricted directory; restore by copying back and running `migrate` (idempotent). It
holds only research/shadow state — it is never the source of live execution state.

## Provider / historical-validation status

Historical validation of the shadow universe remains **BLOCKED** pending approved
provider access — see `docs/dynamic_universe_provider_status.md`. The verdict
`TRIAL ACCESS REQUIRED BEFORE DECISION` stands; no EODHD call or ingestion is performed.
