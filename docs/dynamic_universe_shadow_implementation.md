# Dynamic Universe v1 — Shadow Implementation

> Additive, feature-flagged, **non-integrated** research/operational foundation. It does
> not change any live entry/exit/sizing/broker behaviour. Branch
> `feature/dynamic-universe-ibkr-shadow-v1` (base production `breakout-strategy @ ffd6d23`).

## Package layout (`bot/universe/`)

```text
params.py        frozen v1 constants (universe + risk; risk mirrors backtest/breakout_sim)
db.py            universe.db connection + PRAGMA user_version migration framework
migrations.py    ordered, append-only DDL (the 6 tables + idempotency index)
models.py        State enum (8 states), reason codes, data carriers
registry.py      CRUD for canonical/gateway maps/candidate sources/state (+history)
seed.py          idempotent registry seed from instruments.json/instruments_ig.json
eligibility.py   pure structural-eligibility predicate (task §7)
state_machine.py pure transition (dominance, hysteresis, cooldown)
evaluator.py     flag-gated daily shadow evaluator (NO broker import)
scheduler.py     idempotent daily scheduler (post-close, multi-timezone, restart-safe)
```

## Feature flag (default OFF)

`enable_dynamic_universe_shadow` is registered in `bot/regime/flags.py`
(`KNOWN_FLAGS` + `SAFE_DEFAULTS=False`, no dependency). Registering it means a future
`settings.feature_flags` entry can never crash startup with "Unknown flag"; the default
is **false**, so absent/false → no shadow behaviour.

## Zero live runtime effect (by NON-INTEGRATION)

The shadow foundation is **not wired into `main.py`** or any plugin/Layer-1 path. A test
(`tests/universe/test_no_live_integration.py`) asserts **no live module imports
`bot.universe`**, that adding the flag leaves every pre-existing flag's resolution
byte-identical, and that the universe package imports no broker module. Therefore:

* When the flag is false (or absent): `ShadowEvaluator.maybe_run` / scheduler return a
  no-op with **zero DB writes** and the live strategy path is provably unchanged.
* Enabling it in production would require a **separate, approved wiring step** — out of
  scope here.

## Reuse of the frozen breakout logic

The evaluator computes HYPOTHETICAL breakout candidates with
`backtest/breakout_strategy.compute_indicators` (read-only) — the same frozen
SMA50/200, Wilder ATR14/ADX14, 20-day-high, entry/trend-break logic as the breakout
result — so the shadow never diverges from the deployed mechanics. A test asserts the
risk constants equal `backtest/breakout_sim.py`'s.

## Data seam (no broker)

The evaluator takes an **injected `bars_provider(canonical_record) -> dict | None`**
callable. It returns `{bars, corp_action_status, sector, spread, fresh_bar, admin_paused}`.
There is deliberately **no broker dependency**; tests inject synthetic providers. v1
shadow runs would supply `corp_action_status='unavailable'` (honest — no corp-action
source yet), which correctly yields DATA_INELIGIBLE rather than a silent pass.

## What the evaluator does (flag ON, test/shadow env only)

structural eligibility → state transition (persisted to `universe_state` + append-only
`universe_state_history`, idempotent on instrument/date/evaluator-version) → TTI/MANUAL
candidate TTL expiry → hypothetical breakout signal → slot/sector/heat contention
(deterministic priority: ADV20 desc, spread asc, canonical id) → hypothetical IBKR
gateway (read from `primary_gateway`) → hypothetical risk/qty (frozen 0.50% risk, 20%
notional cap, 2.50% heat). It **never** submits orders, calls a broker/provider, reads
live positions.db, changes Layer-1/exit/sizing, or rewrites config.

## How to prove live behaviour is unchanged

```text
pytest tests/universe/test_no_live_integration.py   # no live import; flags unchanged
pytest tests/                                        # full suite stays green
```

## How to enable ONLY in a test/shadow environment

Construct a `ShadowEvaluator` (or `DailyUniverseScheduler`) with a flags object whose
`get('enable_dynamic_universe_shadow')` is true, pointed at a NON-production `universe.db`
and an injected bars provider. Never set the flag in the live `instruments.json`
`settings.feature_flags`. See `docs/dynamic_universe_shadow_operations.md`.
