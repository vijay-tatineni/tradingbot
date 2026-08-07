# Dynamic Universe — Shadow-wiring prerequisites W1/W2 completion

**Status:** `IMPLEMENTED — awaiting independent review`. NOT resolved; the implementation session
did **not** run the independent review. This tranche is **boundary code only** — it does NOT wire
runtime startup, enable the feature, create any DB, run any migration, restart any service, or
call any broker/provider. The feature remains **disabled** (`enable_dynamic_universe_shadow=False`
default) and un-wired; no runtime activation is approved.

**Base:** `breakout-strategy` @ `bf624e954a4cb0b59564fe0f9b1ea0900afd6f38`.
**Branch:** `feature/dynamic-universe-shadow-prereqs-w1-w2`.
**Design reference:** `/root/deployment_records/dynamic_universe_shadow_readiness_design.md`
(§11 BLOCKER-W1 / BLOCKER-W2).

## Scope

Implements ONLY the two Gate-C prerequisites. Does NOT implement runtime startup wiring,
`main.py`/`api_server.py` activation, scheduler startup, shadow enablement, service restart, live
provider integration, broker routing, paper trading, or order submission.

## BLOCKER-W1 — broker-free completed-bar provider boundary

New `bot/universe/bar_provider.py`:
- `CompletedBarProvider` — an INJECTED `Protocol` (`completed_bar(*, record, trading_date,
  timeframe) -> CompletedBarSnapshot`). Implementations must answer from an already-materialized
  broker-free source (fixture / approved offline-cached snapshot); they import no broker and
  fetch no live data. The concrete cached/live implementation is a separate approved tranche.
- `CompletedBarSnapshot` — frozen, immutable: `trading_date`, `timeframe`, `available`,
  `instrument_uid`, `listing_uid`, `bar_end_time`, `source`, `version`, `reason`.
- `safe_completed_bar(provider, *, record, trading_date, timeframe="1d")` — fail-closed wrapper.
  Returns `available=True` ONLY when the provider returns a well-formed snapshot for the requested
  date/timeframe, marked available, with full proof (`bar_end_time`+`source`+`version`) whose
  `bar_end_time` covers the requested `trading_date`. Every other outcome — no provider
  (`bar_provider_missing`), exception (`bar_provider_error`), malformed (`bar_provider_malformed`),
  date/timeframe mismatch, unavailable, incomplete, stale — resolves to `available=False` with a
  stable reason. NEVER raises.
- `as_bar_available_fn(provider, timeframe)` — adapts the provider into the existing
  `DailyUniverseScheduler.bar_available_fn` callable (`Callable[[dict,str],bool]`), fully
  fail-closed. The scheduler depends only on this callable boundary, never on a broker client.

## BLOCKER-W2 — lazy, flag-gated, side-effect-free construction

New `bot/universe/shadow_runtime.py`:
- `validate_shadow_config(*, flags, db_path, bars_provider, completed_bar_provider)` — PURE
  validation with NO filesystem side effects (no stat/open/connect/migrate). Fail-closed reasons:
  `flag_off` (master flag not true — the normal disabled posture), `shadow_db_path_missing`,
  `shadow_db_path_unsafe` (path basename collides with a production DB —
  `positions.db`/`regime.db`/`backtest.db`/`learning_loop.db`/`layer3_silver.db`/`advisor.db`/
  `news.db`/`trades.db`/`trading.db`/`universe.db`), `bars_provider_missing`,
  `completed_bar_provider_missing`, `live_provider_requires_approval` (a provider declaring
  `is_live=True`). Returns `ok=True` with the validated `db_path` only when every check passes.
- `build_shadow_scheduler(...)` — lazy factory: validates FIRST; if not ok returns a fail-closed
  `ShadowBuildResult(ok=False, reason, scheduler=None)` having constructed NOTHING (no Registry/
  store/provider, no `sqlite3.connect`, no `migrate`, no DB file, no provider call). Only on the
  flag-on, fully-valid path does it lazily construct the Registry (the single DB-opening site),
  the evaluator, and the `DailyUniverseScheduler` (whose completed-bar gate is the W1 boundary).
  Heavy modules are imported lazily inside the default factories; importing `shadow_runtime` has
  no side effects. `_registry_factory`/`_evaluator_factory` are test seams (spies prove
  construction happens exactly when — and only when — permitted, with no real DB).

## Config validation behavior

Invalid/disabled config → a fail-closed result, no scheduler/store/provider construction, no
filesystem touch. Validation is a pure decision; the factory is the only thing that may construct,
and only after validation passes.

## DB no-touch / provider no-call / broker-free proof

- DB no-touch: flag-off and every invalid-config path construct nothing; tests assert zero `*.db`
  files are created and (with default factories + a monkeypatched `sqlite3.connect` that raises)
  that `connect` is never reached on the flag-off path.
- Provider no-call: the injected stubs record calls; off/invalid paths leave `.calls == []`.
- Broker-free: `bot/universe/{bar_provider,shadow_runtime,scheduler,evaluator}.py` contain no
  `bot.brokers`/`ib_insync`/`trading_ig`/`bot.connection` import and no `.fetch_bars(` call
  (asserted by `test_shadow_path_modules_import_no_broker_and_no_fetch_bars`).

## Tests

`tests/universe/test_shadow_prereqs_w1_w2.py` (23 tests) + `StubCompletedBarProvider` in
`tests/universe/_fixtures.py`. Covers: flag-off → no construction / no connect / no DB file / no
provider call; missing or unsafe DB path (positions/regime/backtest/learning_loop/universe) → no
start, no touch; missing bars/completed-bar provider → fail closed; live provider → requires
approval; enabled+valid → lazy construct exactly once (spies, no real DB); W1 fail-closed for
missing/exception/unavailable/incomplete/stale/mismatch/malformed; available stub → eligible;
broker-free / no-`fetch_bars` import isolation.

- **Focused:** `pytest tests/universe` → **460 passed** (437 base + 23 new). No regression.
- **Full:** `pytest tests` (`-p no:randomly`) → **1792 passed, 4 failed**; all 4 failures are in
  `tests/test_breakout_indicators.py` — the repository's pre-existing missing-`ohlcv`/`backtest.db`
  environment issue (the fresh worktree auto-creates an empty `backtest.db` with no `ohlcv` data),
  unrelated to W1/W2. No new failure outside that file.

## Known limitations / not in scope

- The concrete broker-free completed-bar/data PROVIDER implementation (cached/offline source) is a
  separate, explicitly-approved tranche; this delivers only the injected boundary + fail-closed
  semantics + stub.
- Runtime wiring of `build_shadow_scheduler` into `main.py` is NOT done here (Gate C wiring +
  Gate D inert deploy + Gate E enablement remain separate, reviewed steps).
- `live_provider_requires_approval` uses an `is_live` attribute convention; a concrete live
  provider's approval/gating is decided in its own tranche.
