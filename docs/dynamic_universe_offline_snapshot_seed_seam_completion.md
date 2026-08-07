# Dynamic Universe — offline snapshot records / seed seam completion

**Status: IMPLEMENTED — awaiting independent review.** Code-on-disk on a feature branch. Default-off,
broker-free, additive. Does NOT authorize shadow enablement, runtime activation, paper trading, a
service restart, config edits, DB creation, snapshot creation, or migrations.

## What this closes

The shadow readiness refresh after the local-bars provider
(`/root/deployment_records/shadow_enablement_readiness_refresh_after_local_bars_provider.md`) and the
Gate C runtime wiring both left one binding **records/seed** gap: `TradingBot._shadow_canonical_records`
returned an inert `[]` placeholder ("candidate ingestion is a SEPARATE, not-yet-approved tranche"),
so the shadow scheduler was fed an empty record set and the universe could never advance even with
valid providers and a valid shadow DB. This tranche adds the first concrete, **broker-free,
explicit-config-only, fail-closed** offline records source and wires it behind the same master flag.

It is the code counterpart to the data-ops design in
`/root/deployment_records/offline_snapshot_source_approval_design.md` (§4.3 identity co-dependency):
the seam READS approved local snapshots; it does not generate them and does not seed any DB.

## Records/seed seam design

`bot/universe/shadow_records.py`:

- **`SnapshotShadowRecordsSource`** — builds the scheduler's per-cycle canonical records from the
  SAME two approved, local, read-only snapshot files the providers already consume:
  `bars_snapshot_source` (the local-bars OHLCV snapshot) and `completed_bar_snapshot_source` (the
  completed-bar availability snapshot). It reads nothing else.
  - `__call__() -> list` is the plain runtime contract (`TradingBot._shadow_canonical_records` calls
    it); `build() -> ShadowRecordsResult` is the structured sibling (records + `source_reason` +
    per-instrument `excluded` reason map) for tests/logging.
  - Never raises, never writes, never opens a database, never calls a broker. `is_live = False`.
- **`build_shadow_records_source(*, bars_source, completed_source, timeframe="1d", now_fn=None)`** —
  the fail-closed factory: validates BOTH source paths with the sibling providers'
  `validate_source_path` and returns `None` (constructing/reading nothing) if either is missing or
  unsafe. Mirrors `build_local_bars_provider` / `build_local_completed_bar_provider`.
- **`_load_snapshot_records`** — read-only loader (file or directory of `.json`/`.jsonl`), reusing
  `_parse_records_from_text` and re-checking EACH child path (basename + symlink-resolved basename)
  against the full 14-name `PRODUCTION_DB_BASENAMES` set before any open.

The local-bars snapshot defines the candidate universe (each instrument with ≥200 bars); each bars
row is joined to its completed-bar availability row by R2A-1 identity.

## Snapshot schema usage (schema_version = 1)

Reused verbatim from the two providers (no new schema). Per instrument the seam requires, on the
**bars** row: `schema_version==1`, the full identity triple `canonical_instrument_id` +
`instrument_uid` + `listing_uid`, `timeframe=="1d"`, `trading_date`, `bar_end_time`
(`[:10]==trading_date`, non-future), `source` + (`version` or `content_hash`), and a `bars` array of
≥200 numeric (non-bool) OHLCV dicts (`open/high/low/close/volume`). On the matched **completed-bar**
row: `schema_version==1`, `available` true, `timeframe=="1d"`, `trading_date`, `bar_end_time`
(`[:10]==trading_date`, non-future), and proof (`source` + `version`/`content_hash`). Unknown keys
(audit metadata) are ignored. No credentials/account IDs/raw broker payloads are read or emitted.

## Identity consistency (R2A-1 co-dependency, design §4.3)

A canonical record is emitted ONLY when the bars row and the completed-bar row **agree on the full
triple** `(canonical_instrument_id, instrument_uid, listing_uid)`:

- the bars row must carry all three ids (no ticker/symbol fallback) — else `snapshot_identity_mismatch`;
- the matched completed-bar row's `instrument_uid`/`listing_uid` must equal the bars row's, and any
  `canonical_instrument_id` it carries must equal the bars row's — else `snapshot_identity_mismatch`;
- if no completed-bar row shares the bars row's identity → `snapshot_proof_missing`.

This guarantees each downstream consumer keys on a consistent identity: the scheduler on
`canonical_instrument_id`; the completed-bar gate on `instrument_uid`/`listing_uid`; the bars
provider on `canonical_instrument_id`. The emitted record carries `canonical_instrument_id`,
`instrument_uid`, `listing_uid`, `currency` (for the scheduler's post-close market gate),
`primary_gateway="IBKR"`, and non-sensitive `display_symbol`/`source` labels.

## Config integration (flag-gated, lazy, fail-closed)

`main.py`:

- **`build_shadow_records_fn_from_config(flags, shadow_cfg)`** — returns `None`, importing NOTHING
  from `bot.universe`, when the master flag is off (production default). Only on the flag-on path
  does it lazily import `build_shadow_records_source` and build from `bars_snapshot_source` +
  `completed_bar_snapshot_source`. Either source missing/unsafe → `None` (fail closed).
- **`TradingBot.__init__`** sets `self._shadow_records_fn = build_shadow_records_fn_from_config(...)`
  (from `settings.dynamic_universe_shadow`) right after `init_shadow_runtime`.
- **`TradingBot._shadow_canonical_records`** returns `list(self._shadow_records_fn())` when the fn
  exists, else the inert `[]`. The fn is consulted only when a shadow scheduler was actually
  constructed (guarded in `_maybe_run_shadow_cycle`) — never in production.

`main.py` retains NO module-level `bot.universe` import (AST-asserted by
`test_no_live_integration.py`).

## Path safety / realpath behavior

Reuses the providers' path safety unchanged: `validate_source_path` rejects a configured basename
that IS a production DB basename (plain-basename check first, no stat), then rejects a symlink-
resolved basename that IS one. `_load_snapshot_records` re-checks each child path (basename +
`os.path.realpath` basename) against the full `PRODUCTION_DB_BASENAMES` set before any `open(..., "r")`.
No production DB is read or written; no runtime snapshot directory/file is created.

## Fail-closed reason map

Source-level (whole build → empty `records`, one `source_reason`): `snapshot_source_missing` ·
`snapshot_source_unsafe` · `snapshot_source_not_found` · `snapshot_source_unreadable` ·
`snapshot_malformed`. Per-instrument (that instrument dropped, reason under `excluded`):
`snapshot_schema_unsupported` · `snapshot_identity_mismatch` · `snapshot_trading_date_mismatch` ·
`snapshot_timeframe_mismatch` · `snapshot_future_bar` · `snapshot_proof_missing` ·
`snapshot_bars_insufficient` · `snapshot_ohlcv_invalid`. Defensive: `snapshot_provider_error`.

## Broker-free / no-DB proof

- `bot/universe/shadow_records.py` imports no broker/IBKR/IG/EODHD/live-data/FX/portfolio module and
  opens no database — covered by the package-wide `test_no_live_integration.py::
  test_universe_package_imports_no_broker` (globs `bot/universe/*.py`) and a dedicated
  `test_module_imports_no_broker` (importing the module pulls in neither `ib_insync` nor `trading_ig`).
- `test_build_creates_no_database` asserts no `*.db` file appears anywhere under the temp tree after
  a build.

## Tests

`tests/universe/test_shadow_records_seam.py` (34 tests): valid fixtures → non-empty records; valid
fixtures → the real `DailyUniverseScheduler` (with the real broker-free completed-bar provider over
the same snapshot) receives the proven cids; valid fixtures → a real `ShadowEvaluator` over a SEEDED
tmp registry + real `LocalBarsSnapshotProvider` actually evaluates the instrument (`evaluated >= 1`,
proving the emitted `canonical_instrument_id` is genuinely consumable, not merely fail-closed-safe);
factory returns `None` for missing/unsafe sources;
source-level fail-closed for missing/not-found/malformed/symlink-to-prod-DB; per-instrument
fail-closed for unsupported schema, timeframe/trading_date mismatch, missing proof, `<200` bars,
bool/non-numeric OHLCV, future bar, identity gaps/disagreements, unavailable completed bar; foreign
dict rows skipped; never-raises; `main.py` config builder flag-off `None` (+ no file read/creation),
flag-on missing/unsafe `None`, flag-on valid builds records; no DB creation; no broker import.

## What remains before this seam does anything at runtime (SEPARATE, gated — NOT authorized here)

1. **Data-ops:** approved snapshot generation + validation manifest + operator signoff (Gate E/F,
   per `offline_snapshot_source_approval_design.md`). No snapshot exists yet.
2. **Registry seeding:** `seed_registry(shadow_db_path)` into the dedicated shadow DB so the
   evaluator's `registry.all_canonical()` carries the matching canonical rows (with the same R2A-1
   identity). This seam opens no DB and does not seed — seeding is Gate E/F.
3. **Shadow enablement:** the master flag flip + explicit config block + `cogniflowai-bot` restart
   window (Gate E/F). Until then the wiring stays inert (`shadow_runtime_disabled`, `[]` records).

**This tranche is wiring, not activation. It does not enable shadow mode, start paper trading,
create any DB, generate any snapshot, or restart any service.**
