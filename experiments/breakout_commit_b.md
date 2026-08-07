# Breakout Strategy — Commit B (IMPLEMENTATION LOCK)

Implements and freezes the v3.2 frozen design (`experiments/breakout_strategy.md`).
Per §8: after this commit, **no code/data/config changes without invalidating the
run**; a bug fix is the only allowed reason to change locked code (and forces a new
Commit B + a 3a rerun from the beginning). `bot/` and `main.py` are untouched (§9).

## Code modules (new; in `backtest/`)
| Module | Role | git blob hash |
|---|---|---|
| `backtest/breakout_strategy.py` | Frozen indicators (SMA50/200, Wilder ATR14/ADX14, 20d-high `shift(1)`) + 4-condition entry + SMA50 trend-break | `09de29ab7f886dbfe29bbf303c5ef27d243ce6dc` |
| `backtest/breakout_sim.py` | Portfolio event loop: ATR stops + monotonic ratchet, risk/notional/heat/cash sizing, 5-cap, mixed-market UTC ordering (§14), END_OF_TEST_LIQUIDATION | `cb39f0a84e09381b2f098745ade83cdd2e985962` |
| `backtest/breakout_metrics.py` | Frozen §14 metric definitions: profit factor, instrument concentration, top-five removal | `eb58519ee9a4102c7d330808507e5b89e2fb9992` |
| `backtest/breakout_run.py` | Phase 3a development-period runner (outcome-blind) | `443897683f3e9f63e06370847ae533ba38a7aad6` |

Reused honest primitives (left untouched; recorded for provenance):
| Module | Reused for | git blob hash |
|---|---|---|
| `backtest/simulator.py` | `CostConfig`, `_adverse_fill`, `_commission`, `classify_instrument` | `a559b81776d729b0edbcacb9545d4b23bfc10944` |
| `backtest/portfolio_sim.py` | `fx_to_usd` static FX | `39e875eef9a6e3e5e771836c1457050406df4ffb` |
| `backtest/database.py` | `load_bars` | `f673787ed5971c2d1513d38d6cb6d31934ac6c5e` |

Lock commit SHA: the commit that introduces this file (resolve via `git log`).

## Dataset snapshot
- Source: IBKR `reqHistoricalData`, `whatToShow=TRADES`, `useRTH=True`, `durationStr="2 Y"`, daily bars (`backtest/download.py`).
- Table `ohlcv`, timeframe `daily`, the 14 frozen symbols.
- **Content hash (sha256 over sorted (symbol,datetime,o,h,l,c,v)): `b36e44e3725e52d75cebc1a0fa807e2430f8861f197da760271420bbf6ac584b`**, **7032 rows**.
- `downloaded_at` range: 2026-03-23 .. 2026-04-22 (NVTS fetched 04-22).
- Bar-extent reminder (from Stage 0 audit): data ends 2026-03-20 (US group) / 04-21 (NVTS); OOS validation covers through Feb 2026 only — re-validate on fresh data before live if 3b passes.

## Exact tickers + exchanges (frozen universe order)
| # | Symbol | Exchange | Currency | sec_type | session-open (UTC) |
|---|--------|----------|----------|----------|--------------------|
| 0 | SGLN | SMART | GBP | STK | 08:00 (LSE) |
| 1 | SSLN | SMART | GBP | STK | 08:00 (LSE) |
| 2 | TSM | SMART | USD | STK | 14:30 (US) |
| 3 | AVGO | SMART | USD | STK | 14:30 (US) |
| 4 | ANET | SMART | USD | STK | 14:30 (US) |
| 5 | SU | SMART | EUR | STK | 08:00 (Euronext) |
| 6 | SCCO | SMART | USD | STK | 14:30 (US) |
| 7 | ANTO | SMART | GBP | STK | 08:00 (LSE) |
| 8 | PLTR | SMART | USD | STK | 14:30 (US) |
| 9 | NBIS | SMART | USD | STK | 14:30 (US) |
| 10 | AAPL | SMART | USD | STK | 14:30 (US) |
| 11 | MSFT | SMART | USD | STK | 14:30 (US) |
| 12 | BARC | SMART | GBP | STK | 08:00 (LSE) |
| 13 | NVTS | NASDAQ | USD | STK | 14:30 (US) |

## Trading calendar + timezone
- Bars are date-only daily sessions. Mixed-market ordering (§14) assigns each
  instrument a **representative** UTC session-open time used ONLY for event
  ordering: Europe (GBP/EUR) 08:00, US 14:30. The invariant that matters —
  Europe opens before US on the same date, so European exits free slots/cash
  before US entries — holds.
- **Deferred (documented, not a strategy rule):** exact per-exchange holiday
  calendars and daylight-saving transitions. These cannot change the ordering
  invariant; they only shift absolute UTC times. To be revisited only if a
  future fidelity concern requires it.

## Corporate actions / adjustment
- OHLC as delivered by IBKR `whatToShow=TRADES`: **split-adjusted**, **NOT
  dividend-adjusted**. No additional corporate-action processing in the pipeline.
- Dividends: not modelled (price series only; no reinvestment).

## FX
- Source: **static fallback rates** mirrored from `bot/portfolio.py` via
  `backtest/portfolio_sim._FX_TO_USD` (USD 1.0, GBP 1.27, EUR 1.08, CHF 1.12,
  JPY 0.0067). No live timestamp rule (a backtest has no IBKR handle).
- Static-rate simplification: FX scales the combined USD equity curve; it does
  not change per-instrument trade mechanics. GBP names are pence-quoted (÷100 to
  pounds before FX).

## Transaction-cost configuration (base preset, `CostConfig.for_class`)
- Half-spread (bps), applied adversely per fill: us_large_cap 1.5 · lse 5.0 ·
  cfd 10.0 · default 3.0. Slippage 2.0 bps. (Per `backtest/simulator.py`.)
- Commission: 0.005/share, $1.00 min/side, 1% notional cap. `gap_fill_at_open=True`.
- 1.5× cost-stress preset (`pessimistic`) reserved for the 3b sensitivity table.

## Frozen scale parameter
- **Initial capital: USD 100,000.** Not a strategy rule — a scale-only
  implementation parameter (no canonical live account-equity figure exists in
  settings). Chosen large enough that integer-share rounding and the $1
  commission floor are not dominant across 14 instruments incl. pence-quoted names.

## §14 metric definitions — implemented, NOT run in 3a
`profit_factor`, `instrument_concentration`, `remove_top_five` are implemented in
`backtest/breakout_metrics.py` and unit-tested on **synthetic** trade lists only
(`tests/test_breakout_metrics.py`). They are outcome metrics and by
pre-registration are **not** computed on dev or OOS data during Phase 3a; they are
reserved for Phase 3b.

## Test coverage
`tests/test_breakout_indicators.py`, `_ratchet.py`, `_lifecycle.py`, `_cash.py`,
`_ordering.py`, `_metrics.py` (28 tests). Full suite: 1289 passed.

**Integration-coverage honesty:** the Phase 3a dev run exercised only `gap_stop`
and `intra_stop` exits. `trend_break`, `END_OF_TEST_LIQUIDATION`, the 5-position
cap block, and same-timestamp universe-order contention are covered by **unit
tests** but were not hit in the dev integration run (low-frequency dev window).
