# Dynamic Universe — local eligibility-bars provider (`bars_provider`) completion

**Status: IMPLEMENTED — awaiting independent review.** Code-on-disk on a feature branch. Default-off,
broker-free, additive. Does NOT authorize shadow enablement, runtime activation, paper trading, a
service restart, config edits, DB creation, snapshot creation, or migrations.

## What this closes

The shadow readiness refresh
(`/root/deployment_records/shadow_enablement_readiness_refresh_after_completed_bar_provider.md`)
identified `bars_provider` as the remaining binding Gate E/F blocker: `validate_shadow_config`
checks `bars_provider is None` (`bot/universe/shadow_runtime.py:107`) BEFORE the completed-bar
provider, and `_resolve_shadow_runtime_config` hardcoded `bars_provider=None` (main.py), so shadow
failed closed at `bars_provider_missing` even after the completed-bar provider landed. This adds the
first concrete, broker-free `bars_provider` plus its flag-gated, fail-closed config seam.

## bars_provider contract (verified against the actual code)

The evaluator stores and calls `self.bars_provider(rec)` with ONLY the canonical record
(`bot/universe/evaluator.py:121,295`) — a `Callable[[dict], Optional[dict]]`. The returned dict is
consumed for: `bars` (a pandas DataFrame fed to `backtest.breakout_strategy.compute_indicators`),
`corp_action_status` (default `"unavailable"`), `sector`, `fresh_bar` (default `True`),
`admin_paused` (default `False`), `price_unit`, and `spread`. The de-facto shape matches the
existing working fixture (`tests/universe/_fixtures.py::SpyProvider` / `test_evaluator.py::
_uptrend_source`).

**Spec-vs-code divergence (per CLAUDE.md).** The task spec described a per-request model
(`trading_date matches request`, `timeframe matches request`). The runtime passes NO request
date/timeframe — only `rec`. So the provider validates the snapshot record's OWN declared metadata
for internal consistency instead: `timeframe` must equal the provider's configured timeframe, and
the most recent bar's `bar_end_time` must be a completed (`[:10] == trading_date`), non-future
session. This is documented here and surfaced in the review packet so the divergence is deliberate.

**Identity.** Matched on `canonical_instrument_id` — the ONLY identity the evaluator's record
carries (`registry.all_canonical()` over the `canonical_instruments` table, whose columns do not
include `instrument_uid`/`listing_uid`; those live in the separate R2A-1 identity tables).
`instrument_uid`/`listing_uid` are also accepted when present (forward-compat). A real-registry
integration test (`test_real_evaluator_consumes_provider_over_seeded_registry`) proves a
registry-produced record matches and the evaluator computes a USD-normalised price — i.e. the
identity field is correct, not merely fail-closed.

## Provider design — `LocalBarsSnapshotProvider`

`bot/universe/local_bars_provider.py`. Sibling of `LocalCompletedBarSnapshotProvider`; reuses its
`validate_source_path`, `PRODUCTION_DB_BASENAMES`, `_parse_records_from_text`, `_parse_bar_end_time`
and source/path reason codes (single source of truth, no duplication). Properties:

- **broker-free** — imports only stdlib + intra-package helpers + a LAZY `pandas` import inside the
  frame builder; imports NO broker / IBKR / IG / EODHD / live-data API and NOT
  `backtest.breakout_strategy` (it supplies the frame; the evaluator computes indicators);
- **read-only** — opens the snapshot strictly mode `"r"`; never writes;
- **injected-only / default-off** — constructed solely via the flag-gated `build_local_bars_provider`
  factory and the `main.py` seam; no live default, no production source;
- **`is_live = False`** — so `validate_shadow_config` accepts it (a live source would set it True and
  be rejected pending separate approval);
- **fail-closed** — `__call__` returns `None` (logging a stable reason via the structured `resolve`)
  for every bad input; NO exception escapes into the evaluator (catch-all → `provider_error`).

## Snapshot format (v1)

Minimal versioned JSON object / array / JSONL / directory of `.json`/`.jsonl`. Per bars record:
`schema_version` (supported `1`), `canonical_instrument_id` (and optional `instrument_uid` /
`listing_uid`), `trading_date`, `timeframe`, `bar_end_time` (proof of the most recent completed
session), `source`, `version`/`content_hash`, `generated_at`, and `bars` — a non-empty array of
OHLCV objects `{open, high, low, close, volume}` (each strictly numeric; booleans rejected). Optional
pass-throughs: `corp_action_status`, `sector`, `price_unit`, `currency`, `spread`, `fresh_bar`,
`admin_paused`. No credentials / account-IDs / raw broker payloads.

## Config integration

`settings.dynamic_universe_shadow.bars_snapshot_source` (a SEPARATE, unambiguous key from
`completed_bar_snapshot_source`). `main.py`'s `build_bars_provider_from_config(flags, shadow_cfg)`
returns `None` when the flag is off (importing NOTHING from `bot.universe` — lazy-only contract);
when on, it lazily imports `build_local_bars_provider` and builds from the explicit source. Wired
into `_resolve_shadow_runtime_config` (replacing the hardcoded `None`). Matrix: off → no
construction · on + no source → fail closed (`None`) · on + unsafe source → fail closed · on + valid
local source → constructed (tests / future rehearsal only).

## Source-path safety / realpath

Reuses `validate_source_path`: basename check FIRST (rejects an obvious production path without
resolving it), then symlink-resolved basename vs `PRODUCTION_DB_BASENAMES`. For a directory source,
EACH child path is revalidated (basename + realpath) before open (P3-1-style hardening), so a planted
child cannot be read. Tested with a `.json` symlink → a TEMP `backtest.db` (never the real DB).
Operator realpath preflight on the shadow DB path remains a separate manual obligation.

## Fail-closed reason map (→ `None`)

`source_path_missing`, `source_path_unsafe`, `source_path_not_found`, `source_path_unreadable`,
`snapshot_malformed`, `snapshot_schema_unsupported`, `instrument_mismatch`, `timeframe_mismatch`,
`trading_date_mismatch`, `bar_future_timestamp`, `bar_proof_missing`, `bars_missing`,
`bars_malformed`, `provider_error`.

## Hardening carried over from the completed-bar review

- **P3-2 (strict booleans):** `fresh_bar` / `admin_paused` use a strict `bool` check — a string
  `"false"` is NOT coerced truthy; non-bool falls back to the documented default.
- **P3-1 (directory children):** each child path is revalidated against production DB basenames
  before open, not just the configured parent.

## Tests

`tests/universe/test_local_bars_provider.py` — 45 tests: path-safety (missing/unsafe/symlink),
factory off/unsafe/valid, every fail-closed reason, strict-bool, directory + child revalidation,
foreign-record rejection, the config seam (off/no-source/unsafe/valid), broker-free static + fresh-
interpreter import isolation, production-DB no-touch, the valid path feeding the REAL
`compute_indicators`, and a real-`ShadowEvaluator` integration test over a seeded registry.

Focused: **45 passed.** `tests/universe`: **571 passed, 1 skipped** (526 prior + 45 new). W1/W2:
**28**; runtime-wiring: **25**; completed-bar: **35 passed, 1 skipped**. Full suite: failures
confined to `tests/test_breakout_indicators.py` (the repository's pre-existing
missing-ohlcv/backtest.db environment issue, unrelated to the bars provider).

## Still blocked (NOT in this tranche)

A safe, APPROVED local bars snapshot source (a data/ops task) plus Gate E (flag + snapshot/DB path
approval) and Gate F (`cogniflowai-bot` restart window) remain unapproved. Selecting any *live* feed
is a further separate approval. This tranche is code-only and inert: no flag flip, no restart, no DB,
no snapshot files, no provider/broker call.
