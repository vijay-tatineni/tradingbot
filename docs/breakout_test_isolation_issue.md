# Issue draft — `tests/test_breakout_indicators.py` depends on ambient `backtest.db` / `ohlcv` state

> **Draft only — not opened automatically.** Pre-existing, unrelated to the Dynamic
> Universe PR. Recorded here so a future change can fix the test isolation.

## Summary
`tests/test_breakout_indicators.py` reads market data from a `backtest.db` resolved
**relative to the current working directory** (`sqlite3.connect("backtest.db")` in the
module-scoped fixture; `backtest.database.load_bars` queries an `ohlcv` table). Because
`*.db` is gitignored, the file's presence and contents vary by checkout/CWD, so the
test outcome depends on **ambient repository state**, not on the code under test.

## Observed behavior
- **Clean environment (no `backtest.db` / no `ohlcv` table):** 4 failures —
  `test_entry_signal_requires_all_four_conditions` + `test_warmup_date_matches_audit[AAPL-2025-01-06 / NVTS-2025-02-06 / SGLN-2025-01-08]`,
  all `pandas.errors.DatabaseError: Execution failed … no such table: ohlcv`.
- **Ambient `backtest.db` with an empty `ohlcv` table:** 3 failures, and a *different*
  exception shape (`TypeError: Cannot index by location index with a non-integer key`),
  because `test_entry_signal_requires_all_four_conditions` then passes on the empty frame.
- The 3-vs-4 failure count and the exception class therefore shift with ambient DB state
  and test ordering.
- **Reproduces on the base branch (`breakout-strategy @ ffd6d23`)** — the same code,
  same flakiness. It is **not** introduced by the Dynamic Universe foundation
  (`git diff ffd6d23..HEAD` leaves `backtest/`, `tests/test_breakout_indicators.py`, and
  `conftest` byte-identical).

## Impact
- Non-deterministic CI/local results for the breakout-indicator tests.
- Masks/obscures real regressions (a true failure could blend into the ambient-state noise).
- Confuses reviewers comparing failure counts across environments.

## Required future fix
- Replace the CWD-relative `backtest.db` connection with an **explicit, deterministic
  temporary-database fixture** (e.g. a `tmp_path`-scoped SQLite DB seeded with known
  `ohlcv` rows, injected via a fixture/parameter), and remove all dependence on ambient
  repository state.
- Make the tests either (a) seed the fixture deterministically and assert real values, or
  (b) skip explicitly with a clear reason when no market-data fixture is provided —
  never depend on an incidental on-disk DB.

## Scope note
Out of scope for the Dynamic Universe hardening PR. **Do not** modify the breakout tests
or commit a database to mask the dependency as part of that PR. Track and fix separately.
