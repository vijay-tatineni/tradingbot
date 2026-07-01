# Dynamic Universe — offline snapshot generation/validation tooling completion

**Status: tooling implemented — awaiting independent review.** Code-on-disk on a feature branch.
Out-of-band operator tooling; broker-free, additive, imported by NOTHING in the runtime.

This tranche does NOT authorize or perform any of the following — all remain unapproved / not done:

- shadow enablement **NOT approved**
- runtime activation **NOT approved**
- paper trading **NOT approved**
- **no real snapshot artifacts generated** (no `runtime/shadow_snapshots/`, no operator input drop
  transformed)
- **no live provider calls** (EODHD / IBKR / IG / live market-data / FX / portfolio APIs untouched)
- **no production DB reads** (`backtest.db` and every other production DB basename refused by
  path-safety; no database is opened by either tool)
- no DB creation, no migration, no registry seed, no service restart, no config edit, no deployment,
  no PR opened, no merge.

## What this closes

The offline snapshot **records/seed seam**
(`docs/dynamic_universe_offline_snapshot_seed_seam_completion.md`, merged as PR #13) made the shadow
scheduler consume two approved, local, read-only `schema_version=1` snapshot artifacts
(`bars_snapshot_source` = local-bars OHLCV, `completed_bar_snapshot_source` = completed-bar
availability). But it explicitly deferred the step that **produces** those artifacts:

> "Registry seeding into a shadow DB and **snapshot generation** remain SEPARATE, out-of-band, gated
> steps (Gate E/F + data-ops) — this seam opens no database; it only READS approved local files."

This tranche adds the two out-of-band operator tools that close the **generation + pre-enablement
validation** gap, and nothing else:

- `tools/gen_shadow_snapshots.py` — transform an APPROVED local offline OHLCV export into the two
  snapshot artifacts + a `MANIFEST.json`.
- `tools/validate_shadow_snapshots.py` — enforce the design's pre-enablement checks against a
  generated snapshot root; exit non-zero on any failure.
- `tools/__init__.py` — makes `tools` an importable package for the fixtures-only tests. Carries a
  header stating the package is NOT imported by `main.py` / `bot.universe` runtime.

Both tools are **operator-run only**: neither is imported by `main.py` or any `bot.universe` runtime
module, so packaging them cannot change any running behavior.

## Generation tool design (`tools/gen_shadow_snapshots.py`)

Reads an operator-supplied approved **input dir** and writes an explicit **output root**.

### Input format (approved Option A operator file drop)

Under `--input`:

- `approval.json` — a JSON object attesting the data-source approval. Required non-empty keys:
  `approval_id`, `approver`, `approval_timestamp`, `data_source_name`, `data_source_version`,
  `license_status`, `asset_class`, `allowed_use`, `not_approved_for`. `allowed_use` must equal
  exactly `"Dynamic Universe shadow-only"`. Any credential-shaped key (see below) is refused.
- exactly one of `ohlcv.json` (JSON array) or `ohlcv.jsonl` (one JSON object per line) — a non-empty
  list of instrument records. Each record carries the R2A-1 identity triple
  (`canonical_instrument_id`, `instrument_uid`, `listing_uid`), a `currency`, an optional
  `display_symbol`, and a `bars` list of `{date: YYYY-MM-DD, open, high, low, close, volume}`.

### Output snapshot schema (`schema_version = 1`)

Under `--out` (no default — the tool refuses to run without an explicit output root):

- `local_bars/local_bars.jsonl` — per-instrument local-bars record: identity triple, `currency`,
  `timeframe`, `trading_date`, `bar_end_time`, `bars` (oldest→newest, ≥ `MIN_BARS`=200), plus
  `schema_version`, `source`, `content_hash`, `version`, and audit metadata; `display_symbol` when
  supplied.
- `completed_bars/completed_bars.jsonl` — per-instrument completed-bar record: identity triple,
  `timeframe`, `trading_date`, `bar_end_time`, `available: true`, plus `schema_version`, `source`,
  `content_hash`, `version`, audit metadata.
- `MANIFEST.json` — `manifest_version`, generator name/version, `generated_at`/`generated_by`, the
  full `approval` block, `approval_id`, `timeframe`, `trading_date`, `instrument_count`, per-file
  `inputs`/`outputs` (sha256 + record_count), a per-instrument `instruments` list, `allowed_use`,
  and `not_approved_for`.

### Approval-metadata handling

The tool derives coverage/date_range/source-file hashes and the raw-file manifest itself; the
attestation fields (`approver`, `approval_timestamp`, source name/version, license, allowed/not-
approved use) are copied verbatim from `approval.json` into `MANIFEST.json`. `allowed_use` is
asserted equal to `"Dynamic Universe shadow-only"`, and the same value is re-stamped on the manifest
so the validator can re-check it. No secret/credential field is accepted or emitted (forbidden-key
scan on both input and every emitted record).

### Manifest / hash behavior (deterministic)

Records are sorted by `canonical_instrument_id`; each JSONL line is serialized with sorted keys and
compact separators. Each record's `content_hash` is `sha256` over its content fields only (audit
metadata excluded), and `version` mirrors it. Passing `--generated-at` reproduces byte-identical
artifacts. `MANIFEST.json` records the sha256 and record_count of every input and output file. The
manifest is written LAST — its presence is the completion proof; a mid-run validation failure raises
`GenError` and writes no manifest.

## Validation tool design (`tools/validate_shadow_snapshots.py`)

Given a snapshot root, runs a named battery of checks and exits non-zero on ANY failure:

- **structure** — `local_bars/`, `completed_bars/`, `MANIFEST.json` present.
- **path safety** — dir/file basenames and symlink-resolved basenames ∉ `PRODUCTION_DB_BASENAMES`;
  no symlinks anywhere under the root.
- **manifest integrity** — every listed output exists; recomputed sha256 matches; line count matches
  `record_count`; `allowed_use == "Dynamic Universe shadow-only"`.
- **per-record schema** — `schema_version == 1`, identity triple present, `timeframe == 1d`, valid
  `trading_date`, `bar_end_time[:10] == trading_date`, non-future `bar_end_time`, proof present,
  ≥200 numeric (non-bool) OHLCV bars, no forbidden keys.
- **content_hash recompute** — tamper/determinism proof per record.
- **cross-snapshot consistency** — same instrument set both sides; identity triple + `trading_date`
  agree per instrument.
- **seam identity/build (G-real)** — builds via the REAL `build_shadow_records_source` and asserts
  no exclusions and one record per instrument.
- **provider dry-run (G6)** — the REAL `build_local_bars_provider` /
  `build_local_completed_bar_provider` against the local files only; asserts `available` for every
  instrument.
- **broker-free proof (G7)** — a SUBPROCESS imports the validator (pulling in the providers + seam +
  generation tool) and asserts no `ib_insync` / `trading_ig` / `bot.brokers*` module was loaded,
  isolated from any pytest-process import pollution.

## Path safety / realpath behavior

Both tools reuse the already-reviewed broker-free helpers from `bot.universe.local_bar_provider`
(`validate_source_path`, `PRODUCTION_DB_BASENAMES`). The generator additionally refuses any input or
output path that is a symlink, ends in `.db`, or whose basename is a production-DB basename. The
validator walks the entire snapshot tree and fails on any symlink or on any file whose basename OR
`os.path.realpath` basename is a production-DB basename — so a symlink pointing at a production DB is
caught by the resolved-target check. There is no default output path, so importing or smoke-running
the generator can never create the real runtime snapshot dirs.

## Broker-free proof

- Static: `gen_shadow_snapshots.py` imports only stdlib + `PRODUCTION_DB_BASENAMES` /
  `validate_source_path` (pure path-safety, no snapshot read). `validate_shadow_snapshots.py` imports
  stdlib + the same helpers + the already-broker-free providers and records seam.
- Dynamic (G7): the validator's subprocess check asserts no `ib_insync` / `trading_ig` /
  `bot.brokers*` module is loaded when the tools + providers are imported.
- Test: `test_tools_import_no_broker_modules` asserts the same at import time from the test process's
  own perspective.

## Production-DB no-touch proof

Neither tool opens a database of any kind — the generator reads only `approval.json` +
`ohlcv.json|ohlcv.jsonl` under the approved input dir and writes only JSON/JSONL under the explicit
output root; the validator reads only the snapshot artifacts. Every candidate input/output path is
checked against the full `PRODUCTION_DB_BASENAMES` set (basename + symlink-resolved basename) and
`.db` is refused outright. `test_production_db_output_path_rejected` asserts the generator refuses a
production-DB output path.

## Tests

`tests/universe/test_shadow_snapshot_tooling.py` (fixtures-only; no broker, no network, no real
snapshots): generate→validate happy path; determinism (byte-identical with fixed `--generated-at`);
CLI round-trip exit codes; rejection of <200 bars, bool volume, future bar, forbidden key, a
production-DB output path, and a missing OHLCV input; tampered-artifact validation failure; and the
broker-free import assertion.

### Results

- Focused tooling: `pytest tests/universe/test_shadow_snapshot_tooling.py` → **11 passed**.
- Universe: `pytest tests/universe` → **616 passed, 1 skipped**.
- Full suite: `pytest tests` → **1948 passed, 1 skipped, 4 failed**. The 4 failures are all in
  `tests/test_breakout_indicators.py` (`pandas.errors.DatabaseError` reading `backtest.db`).

> Failures confined to `tests/test_breakout_indicators.py` are the repository's pre-existing
> missing-ohlcv/`backtest.db` environment issue and are unrelated to offline snapshot tooling. The
> same file passes 8/8 on a checkout that has the local `backtest.db` data file and fails only in a
> fresh worktree that lacks it — confirming an environment (missing data file) cause, not a code
> regression.

## Known limitations

- The tools transform/validate an APPROVED offline export; they do not fetch data, and the approval
  attestation is trusted as supplied by the operator (its shape and `allowed_use` are enforced, its
  truthfulness is a human gate).
- `--readonly` chmod-0444 is defense-in-depth only; the runtime read-only guarantee is structural
  (providers open mode `"r"`). Directory hardening (0555 / `chattr +i`) remains a G5 ops step.
- Generating real artifacts, seeding a shadow DB, and enabling the shadow path remain SEPARATE,
  out-of-band, gated steps not performed here.
