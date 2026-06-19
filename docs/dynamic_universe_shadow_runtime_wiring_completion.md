# Dynamic Universe — Gate C runtime-wiring completion

**Status:** `IMPLEMENTED — awaiting independent review`. NOT resolved; the implementation session
did **not** run the independent review. This tranche wires the **default-off, fail-closed**
runtime path in `main.py` only — it does NOT enable the feature, start shadow mode, start paper
trading, create any DB, run any migration, restart any service, or call any broker/provider. The
feature remains **disabled** (`enable_dynamic_universe_shadow=False` default). **No runtime
activation is approved.**

**Base:** `breakout-strategy` @ `bc3dd22467a2611f619874401f8f826f3015b29d` (merged W1/W2).
**Branch:** `feature/dynamic-universe-shadow-runtime-wiring`.
**Design reference:** `/root/deployment_records/dynamic_universe_shadow_readiness_design.md`
(§2 runtime-wiring proposal + REQUIRED fail-safe; §5 provider policy).

## Scope

Implements ONLY the `main.py` guarded runtime wiring + its tests + docs. Does NOT implement:
`api_server.py` wiring, feature enablement, production `universe.db`/`universe_shadow.db`
creation, production migrations, candidate ingestion, live provider implementation, broker/market
calls, paper trading, order submission, or deployment.

## `main.py` wiring (lazy, default-off, fail-closed)

- **`init_shadow_runtime(flags, *, db_path, bars_provider, completed_bar_provider, builder, emit)`**
  (module-level). Master-flag gate FIRST: flag absent/false → returns `ShadowRuntimeStartup(
  ready=False, reason="flag_off")` having imported NOTHING from `bot.universe` and constructed
  nothing (no `sqlite3.connect`, no `migrate()`, no DB file, no provider, no scheduler), logging a
  single `shadow_runtime_disabled` line. Only when the flag is true does it **lazily** import
  `bot.universe.shadow_runtime.build_shadow_scheduler` and call it (W2 validates first). There is
  **no top-level `bot.universe` import** in `main.py`.
- **Fail-closed mapping.** Non-OK W2 results map to stable events and return `ready=False` with no
  scheduler: `shadow_db_path_missing`/`live_provider_requires_approval`/`build_exception`/
  `boundary_import_failed` → `shadow_runtime_config_invalid`; `shadow_db_path_unsafe` →
  `shadow_runtime_db_path_unsafe`; `bars_provider_missing`/`completed_bar_provider_missing` →
  `shadow_runtime_provider_missing`; each accompanied by `shadow_runtime_not_started`. Never
  raises. The fully-valid + non-live-provider path logs `shadow_runtime_ready` and returns the
  constructed shadow-only scheduler.
- **`TradingBot.__init__`** calls `init_shadow_runtime(self.flags, **self._resolve_shadow_runtime_config(settings))`
  and stores `self.shadow_runtime`. **`_resolve_shadow_runtime_config`** is the provider/path
  injection seam with **no live default**: providers are `None`, and `db_path` is read from an
  EXPLICIT `settings.dynamic_universe_shadow.shadow_db_path` only (never the production default).
  So in production the build fails closed at `bars_provider_missing` and `self.shadow_runtime.
  scheduler` is `None`.
- **`TradingBot._maybe_run_shadow_cycle`** is the per-cycle seam, called once in the main loop
  immediately after `RegimeClassificationScheduler.maybe_run(...)`. It is a guarded no-op when
  `self.shadow_runtime.scheduler is None` (always, in production). When a scheduler exists (only
  reachable with injected non-live stub providers, i.e. tests/rehearsal) it calls
  `scheduler.maybe_run(self._shadow_canonical_records())`; `_shadow_canonical_records` returns an
  empty list (documented placeholder — candidate ingestion is NOT wired this tranche). Exceptions
  are swallowed (logged `[UniverseShadow] cycle error`).

## Safety invariants

- **Default-off no-op:** flag off ⇒ no `bot.universe` import through startup, no
  `build_shadow_scheduler` call, no `sqlite3.connect`, no `migrate()`, no DB file, no provider
  construction/call, no scheduler. (Functional + AST tests.)
- **Fail-closed when enabled-but-misconfigured:** missing/unsafe DB path, missing provider, live
  provider without approval, builder exception → no start, no DB touch, no crash, stable reason.
- **Broker-free / order-safe:** the shadow wiring path imports no broker and calls no
  `.fetch_bars(`; the seam constructs no order object and the scheduler (when built) is
  shadow-only (submits/modifies/cancels no orders, opens/closes no positions, calls no broker).
- **`api_server.py` untouched** (zero `bot.universe` references — strictly enforced).
- **No live provider default** (design §5): a missing provider is `None`, never auto-built.

## Tests

- New `tests/universe/test_shadow_runtime_wiring.py` (25 tests): flag-off no-op + no-import (fresh
  subprocess) + no-connect/no-DB; enabled fail-closed for missing/unsafe path (parametrized),
  missing bars / missing completed-bar / live provider / build exception; ready path (stub
  builder); per-cycle seam no-op vs inert empty-records run vs exception-swallow; broker-free,
  order-safety, and `api_server.py`-untouched static proofs; main-loop call-site presence.
- Updated `tests/universe/test_no_live_integration.py`: `main.py` is now the one allowed **lazy**
  integration point (new `test_main_py_integration_is_lazy_only` AST check); `api_server.py` and
  every other live module remain at **zero** `bot.universe` references. This was an explicit,
  scoped change — `main.py` guarded wiring is in-scope and cannot exist without referencing
  `bot.universe`, so the pre-wiring "main.py un-wired" assertion was moved (not weakened).
- Results (isolated temp paths; no production DB touched):
  - `pytest tests/universe/test_shadow_prereqs_w1_w2.py` → **28 passed**.
  - `pytest tests/universe` → **491 passed** (465 prior + 25 wiring + 1 lazy-only).
  - `pytest tests` → **1823 passed, 4 failed**; all 4 failures in `tests/test_breakout_indicators.py`
    (the repository's pre-existing missing-`ohlcv`/`backtest.db` environment issue, unrelated to
    runtime wiring). No new failure outside that file.

## Known limitations / not in scope

- The wired call site runs the scheduler with an EMPTY record set; candidate ingestion is a
  separate, not-yet-approved tranche.
- No live/cached broker-free provider exists yet — the provider seam supplies `None`, so the
  enabled path cannot become `ready` in production. Supplying a provider is a separate approval.
- `validate_shadow_config` is basename-only; the realpath/symlink preflight remains an operator
  obligation before any enablement/restart (see operations doc).
- Not approved: shadow mode, runtime activation, paper trading. Not done: deployment, service
  restart, PR open/merge.
