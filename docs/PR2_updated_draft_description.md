# Dynamic Universe IBKR Shadow v1 — foundation + review hardening (DRAFT, default-off, un-wired)

**Status: Draft / Not ready for review.** Default-off (`enable_dynamic_universe_shadow=False`),
un-wired (no `main.py`/`api_server.py` import), broker-free, additive. Merging this
foundation does **not** authorize runtime enablement.

## Independent senior review outcome
`APPROVE_WITH_NON_BLOCKING_FINDINGS` — P0: 0, P1: 0, P2: 2, P3: 9.
Verbatim report preserved at `docs/PR2_independent_review_report.md`.

## P2-1 resolved — atomic migration transaction
`bot/universe/db.migrate` now owns the transaction explicitly: `isolation_level=None`
+ `BEGIN IMMEDIATE` → all schema/index statements → `PRAGMA user_version` → `COMMIT`,
with `ROLLBACK` + re-raise on any error. `executescript()` is not used (it forces an
implicit COMMIT). SQLite DDL and `PRAGMA user_version` are transactional and roll back
together (verified). `BEGIN IMMEDIATE` serializes concurrent migrations. Docs corrected
(`docs/universe_db_schema.md`, `migrations.py`).

## P2-2 resolved — explicit USD normalization and fail-closed FX handling
New `bot/universe/fx.py`: injected, broker-free `FxRateProvider` (returns
`FxQuote(rate, as_of)`; USD assumed 1.0, no provider call). The evaluator now
distinguishes `price_local/adv20_local/currency/price_unit/fx_to_usd/fx_effective_date`
from USD-normalized `price_usd/adv20_usd`; eligibility compares the **USD-normalized**
values to `MIN_PRICE_USD` ($10) / `MIN_ADV20_USD` ($20M). GBX = `currency=GBP,
price_unit=GBX` (÷100 then ×GBPUSD). Every FX problem **fails closed** (never falls back
to local): `fx_conversion_unavailable`, `fx_rate_invalid`, `fx_rate_stale`,
`currency_unknown`, `price_unit_unknown`, `gbx_gbp_unit_ambiguous`,
`normalized_price_invalid`, `normalized_adv20_invalid`. Freshness: the rate must be
effective on the trading date or a prior valid FX session ≤ 4 days earlier; a future
rate is rejected. Hypothetical risk sizing stays local (deferred, documented).

## New focused test result
`pytest tests/universe` → **129 passed** (was 82; +47 new tests covering migration
atomicity/rollback/concurrency, full FX normalization incl. $10 / $20M boundaries, and
rehearsal FX-fixture isolation).

## Minor post-review cleanup (both resolved)
The final targeted review (`P2_FIXES_APPROVED`, P0/P1/P2: 0; 2 minor P3 items) flagged two
documentation/test-hygiene items, both fixed here with **no runtime-logic change**:
* **eligibility docstring** now states `price`/`adv20_usd` are **USD-normalized** (and
  documents the `fx_reason` fail-closed key);
* **rehearsal FX fixture** is now created **fresh per `run_rehearsal()` run** (no shared
  module-level mutable provider); a new test asserts repeated runs are isolated and
  produce identical output.

## New full-suite result
`pytest tests` → no new failures. The only failures are the pre-existing,
environment-driven breakout-indicator tests (missing market-data `ohlcv` table). In a
clean deterministic environment (no `ohlcv` table) the hardening branch reproduces the
**identical 4 failures** as base `ffd6d23`.

## Baseline-failure comparison
`git diff ffd6d23..HEAD` touches only `bot/universe/*`, `tests/universe/*`, `docs/*` —
`backtest/`, `tests/test_breakout_indicators.py`, and `conftest` are byte-identical.
Clean-environment run (no `ohlcv` table), base and hardening **identical**:
```
test_entry_signal_requires_all_four_conditions
test_warmup_date_matches_audit[AAPL-2025-01-06]
test_warmup_date_matches_audit[NVTS-2025-02-06]
test_warmup_date_matches_audit[SGLN-2025-01-08]
→ 4 failed, 4 passed — all pandas.errors.DatabaseError: no such table: ohlcv
```
The full-suite breakout count is flaky on the **base branch too** (base run 1: 4 failed;
base run 2: 3 failed) due to shared `backtest.db` ohlcv state + test ordering — not this PR.
Zero failures outside `tests/test_breakout_indicators.py`; zero failures in the added
`tests/universe/` (129/129). A separate pre-existing test-isolation issue (breakout tests
depend on ambient gitignored `backtest.db`/`ohlcv` state) is captured in
`docs/breakout_test_isolation_issue.md` for a future fix — it is unrelated to this PR.

## Pre-enable blocker register
`docs/dynamic_universe_pre_enable_blockers.md` records every P3 finding (P3-1…P3-9) plus
the deferred FX-sizing item, each with risk / current behavior / why-the-disabled-merge-
is-safe / required correction / required test / owner. Mandatory pre-enable blockers:
P3-2, **P3-3**, P3-4, P3-5, P3-6, **P3-7**, **P3-8**, **P3-9**, and FX-normalized sizing.

## Pre-enable blockers remain
Merging this default-off foundation does not authorize runtime enablement. All mandatory
items in `docs/dynamic_universe_pre_enable_blockers.md` must be resolved and independently
reviewed before any shadow flag is enabled outside isolated tests.

---
*Keep this PR as Draft / Not ready. Do not mark ready for review, merge, enable
auto-merge, deploy, enable the feature, or wire `main.py`.*
