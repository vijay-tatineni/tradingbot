# Dynamic Universe — Concrete Completed-Bar Provider (BLOCKER-W1 source) — Completion

> **Status:** `IMPLEMENTED — awaiting independent review`.
> This tranche resolves the **BLOCKER-W1 source** gap (a concrete broker-free
> `CompletedBarProvider`). It is **code only**: default-off, un-enabled, broker-free, additive.
> **It does NOT authorize** shadow enablement, runtime activation, paper trading, a service
> restart, config edits, DB creation, or migrations. Shadow remains **non-enablable** (see
> "Still blocked", below).

## What this adds

`bot/universe/local_bar_provider.py` — `LocalCompletedBarSnapshotProvider`, the first concrete
implementation of the existing `bot/universe/bar_provider.py::CompletedBarProvider` protocol.

Before this tranche, `CompletedBarProvider` was a `Protocol` with only test fixtures implementing
it — so even with the master flag on, the `main.py` seam injected `completed_bar_provider=None` and
`validate_shadow_config` failed closed at `completed_bar_provider_missing`. This provides a real,
broker-free source so that gate can be satisfied (in tests / future approved rehearsal).

## Provider design

- **Broker-free / live-free / read-only / injected-only / default-off.** Imports no broker /
  IBKR / IG / EODHD / live market-data API; constructs no live client; fetches no live data;
  opens the snapshot strictly read-only (`open(..., "r")`, never write); touches no production DB.
- **`is_live = False`** so `validate_shadow_config` accepts it (a live source would set it `True`
  and be rejected pending separate approval).
- **Pure path validator + fail-closed factory** (mirrors `shadow_runtime.py`'s
  `validate_shadow_config` vs `build_shadow_scheduler` split):
  - `validate_source_path(path) -> Optional[reason]` — pure; returns `source_path_missing` or
    `source_path_unsafe`, else `None`. Unsafe = configured basename is a production DB basename,
    **or** the symlink-resolved basename is (production-path basename checked FIRST, so an obvious
    production path is rejected without resolving/stat-ing it).
  - `build_local_completed_bar_provider(source_path, *, now_fn=None) -> provider | None` —
    constructs the provider only for a present, safe source; logs a stable reason and returns
    `None` otherwise. No filesystem read at construction (existence/format checked at read time).
- **Answer flow.** `completed_bar(record, trading_date, timeframe)` loads the snapshot read-only,
  matches by canonical identity → `trading_date` → `timeframe`, then validates the SELECTED record
  (schema, availability, proof, completion, not-future). Returns `available=True` only for a
  recognized, available, fully-proven, completed (non-future) bar covering the requested session.
  **Never raises** — any unexpected error resolves to `provider_error`.
- **Clock injection.** `now_fn` (default real `datetime.now(UTC)`) makes the "not future" check
  deterministic in tests.

## Snapshot format (minimal, versioned)

A JSON object, a JSON array of objects, or JSONL — or a directory of `.json`/`.jsonl` files. Each
record:

| field | meaning |
|---|---|
| `schema_version` | snapshot format version (supported: `1`) |
| `instrument_uid` / `listing_uid` | R2A-1 canonical identity (never a ticker) |
| `trading_date` | requested completed session (`YYYY-MM-DD`) |
| `timeframe` | e.g. `1d` |
| `bar_end_time` | ISO date/datetime proving the completed session (`[:10]` must equal `trading_date`) |
| `available` | the go/no-go flag |
| `source` | non-sensitive provider/source label (proof) |
| `version` or `content_hash` | deterministic version/hash (proof) |
| `generated_at` | when the snapshot was materialized |

OHLCV values may be included but are not required. **Never** store credentials, account IDs, or
raw broker/provider payloads.

## Config integration (inert in production)

`settings.dynamic_universe_shadow.completed_bar_snapshot_source` (explicit only) selects the
source. `main.py`:
- `build_completed_bar_provider_from_config(flags, shadow_cfg)` — returns `None` (importing nothing
  from `bot.universe`) when the master flag is off; only when on does it lazily import the factory
  and build from the configured source.
- `TradingBot._resolve_shadow_runtime_config` now wires `completed_bar_provider` through that
  helper. `bars_provider` is still `None` (separate tranche). No top-level `bot.universe` import is
  introduced (lazy-only contract preserved; AST-asserted by `test_no_live_integration.py` /
  `test_shadow_runtime_wiring.py`).

Behaviour matrix: flag off → no construction; flag on + no source → fail closed; flag on + unsafe
source → fail closed; flag on + valid local snapshot → provider constructed (tests / future
approved rehearsal only).

## Fail-closed reason map

| reason | when |
|---|---|
| `source_path_missing` | source absent/empty |
| `source_path_unsafe` | source basename (or symlink-resolved basename) is a production DB |
| `source_path_not_found` | configured source does not exist at read time |
| `source_path_unreadable` | file/dir not readable |
| `snapshot_malformed` | not valid JSON/JSONL/object-array, or unparseable `bar_end_time` |
| `snapshot_schema_unsupported` | selected record's `schema_version` not supported |
| `instrument_mismatch` | no record matches the requested canonical identity |
| `trading_date_mismatch` | identity matches but no record for the requested date |
| `timeframe_mismatch` | identity+date match but no record for the requested timeframe |
| `bar_unavailable` | matched record has `available=false` |
| `bar_proof_missing` | matched record missing `bar_end_time` / `source` / `version`(`content_hash`) |
| `bar_not_completed` | `bar_end_time[:10]` ≠ requested `trading_date` |
| `bar_future_timestamp` | `bar_end_time` is after the (injected/real) clock |
| `provider_error` | any unexpected error (defensive; never escapes) |

## Broker-free / production-DB no-touch proof

- Static: `tests/universe/test_completed_bar_provider.py` asserts the module source contains no
  broker import token (`bot.brokers`, `ib_insync`, `trading_ig`, `bot.connection`) and no
  `.fetch_bars(` call.
- Import isolation: a fresh-interpreter subprocess imports the module and asserts no broker module
  appears in `sys.modules`.
- No-touch: a test wraps `builtins.open` and asserts the provider opens only the temp snapshot —
  never any production DB basename (`positions.db`/`regime.db`/`backtest.db`/`universe.db`/… ).
  The provider opens files read-only and never the production default DB path.

## Tests

`tests/universe/test_completed_bar_provider.py` — 35 passed, 1 skipped (the `chmod 0` unreadable
case skips under root). Covers every required case: flag-off no construction; missing / unsafe
(incl. symlink-to-production) source; missing / malformed / unsupported-schema snapshot; identity
/ date / timeframe mismatch; unavailable; future `bar_end_time`; missing proof; valid snapshot
available (direct AND through `safe_completed_bar` / `as_bar_available_fn`); import-isolation;
no production-DB touch.

Regression: `test_shadow_prereqs_w1_w2.py` 28 · `test_shadow_runtime_wiring.py` 25 ·
`tests/universe` 526 · full suite 4 failed / 1858 passed / 1 skipped — the 4 failures are confined
to `tests/test_breakout_indicators.py` (the repo's pre-existing missing-ohlcv/backtest.db
environment issue, unrelated to this provider).

## Still blocked (NOT approved by this tranche)

- `bars_provider` (eligibility bars) is still unimplemented → `validate_shadow_config` fails
  closed at `bars_provider_missing` even with the flag on and a valid completed-bar provider.
- Selecting any **live** data feed (e.g. EODHD) is a separate `BLOCKED_NEEDS_DATA_SOURCE_APPROVAL`.
- Shadow enablement (Gate E: flag flip + shadow DB path + snapshot source approval), the
  service-restart window (Gate F), runtime activation, and paper trading all remain **unapproved**.
- The operator realpath/symlink preflight on the shadow DB path remains a manual obligation.
