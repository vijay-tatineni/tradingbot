# Historical-Data Feasibility — Dynamic Universe Hybrid v1

> **Design/feasibility only. Outcome-blind.** This document evaluates **data availability
> and quality only**. It does **not** compute or reference strategy returns, profit factor,
> Sharpe, rankings, or breakout outcomes (task §8/§15). No data was purchased, no new
> dataset downloaded, no paid service called. Part of `design/dynamic-universe-hybrid-v1`;
> master index `experiments/dynamic_universe_hybrid_v1_DRAFT.md`. Covers deliverable
> **item 7**.

---

## 1. Headline verdict

> **Current repository data is insufficient to construct a point-in-time dynamic
> universe. A proper historical research-data source (with corporate actions,
> delisted securities, and permanent identifiers) is required and is deferred to
> explicit operator approval.** The existing `backtest.db` is adequate only for the
> already-frozen fixed-14 breakout work — not for a survivorship-free dynamic universe.

Every supporting fact below was verified directly against the repository (read-only
introspection), not inferred.

---

## 2. Current data inventory (verified)

### 2.1 `backtest.db` — the only historical OHLCV store

Schema of the single price table (`PRAGMA table_info(ohlcv)`, defined in
`backtest/database.py:26`):

```text
ohlcv(symbol TEXT, timeframe TEXT, datetime TEXT,
      open REAL, high REAL, low REAL, close REAL, volume INTEGER,
      downloaded_at TEXT)
PRIMARY KEY (symbol, timeframe, datetime)
```

Verified facts:

| Property | Value (verified) |
|---|---|
| Distinct symbols | **29** |
| Symbols | AAPL, AMZN, ANET, ANTO, ASML, AVGO, BARC, CEG, CRWD, CVX, FCX, GOOGL, META, MRVL, MSFT, MU, NBIS, NVDA, NVTS, PLTR, SCCO, SGLN, SHEL, SSLN, SU, TSM, VRT, XAGUSD, XAUUSD |
| Timeframes | `daily`, `4hr` |
| Daily date range | **2024-03-21 → 2026-04-21** (~2 years) |
| Adjustment | **RAW / unadjusted** — no `adj_close`, `split`, or `dividend` column exists |
| Corporate actions | **none stored** |
| Listing/delisting dates | **none** |
| Permanent identifiers | **none** (symbol string is the key) |
| Source | IBKR `reqHistoricalData`, `durationStr="2 Y"`, `useRTH=True` (`backtest/download.py:70`) |
| CFD volume | sentinel `-1` for XAGUSD/XAUUSD (volume not applicable) |

### 2.2 Live IBKR fetch path

`bot/data.py:DataFeed.get(contract, days=300, bar_size='1 day')` →
`ib.reqHistoricalData(... durationStr=f'{days} D', whatToShow='TRADES'|'MIDPOINT',
useRTH=True, timeout=15s)` (`bot/data.py:57`). Freshness guard: rejects a feed whose last
bar is >4 days old (`bot/data.py:74`). This is a **recent-bar** path (300 days default),
not a multi-year research path.

### 2.3 IG fetch path — present but blocked for equities

`bot/brokers/ig.py:fetch_bars` calls `ig.fetch_historical_prices_by_epic(...)`
(`ig.py:207`) with a 2.0s inter-request floor and a documented weekly allowance. **Not
persisted** to `backtest.db`. Critically, per `docs/TECH_DEBT.md` ("IG demo account lacks
cash-equity historical-data entitlement", **Status: Blocking**):

> "The IG demo account ... rejects historical-bar requests on `*.CASH.IP` epics with
> `unauthorised.access.to.equity.exception`. This affects all 10 configured instruments ...
> Currently operating in mode 3 by default" (IG runs but does not trade or classify).

**Consequence:** IG cannot serve as a historical research-data source for equities today,
independent of any quality consideration. (Task §7 already states IG is initially an
execution/reconciliation gateway and "do not use IG as the primary broad-universe
historical research-data source." The entitlement gap reinforces this.)

### 2.4 Adjacent data handling

| Concern | Status in repo | Reference |
|---|---|---|
| Corporate actions (splits/dividends) | **ABSENT** — no adjustment code anywhere; bars stored as delivered | confirmed across `backtest/`, `bot/` |
| Currency | pence↔pounds only for GBP; **no FX conversion** between currencies | `bot/currency.py` (`GBP_PENCE_CURRENCIES={"GBP"}`) |
| Timezone / calendar | LSE / US / EUR sessions + holidays (DST-aware) | `bot/market_hours.py`, `bot/bar_schedule.py` |
| Sector / industry | **ABSENT** in `instruments.json` / `instruments_ig.json` | confirmed across all `layer1_active` keys |
| Indicators (frozen) | Wilder ATR14, Wilder ADX14, SMA50, SMA200, 20-day high implemented | `backtest/breakout_strategy.py:37,61,125,126,130` |

---

## 3. Requirement-by-requirement feasibility (against current/available providers)

"Provider" here = **what the system can obtain today without a new paid subscription**:
(a) IBKR historical API (already integrated), and (b) the existing `backtest.db` snapshot.
No purchase or sign-up is proposed; gaps that need a paid vendor are flagged as **operator
approval required**.

| Requirement (task §8) | Feasible now? | Finding |
|---|---|---|
| 5–10 years daily OHLCV | **Partial / No** | IBKR can serve multi-year daily bars for *currently-listed* contracts (durationStr supports years), but `backtest.db` holds only ~2y. IBKR depth varies by contract and **excludes delisted instruments**. |
| Raw **and** split-adjusted prices | **No** | Only RAW is stored and IBKR `reqHistoricalData` delivers a single series; there is no second adjusted series and no adjustment metadata. |
| Splits & dividends | **No** | Not available through the integrated path; not stored. |
| Listing / delisting dates | **No** | Not available from IBKR historical bars; not stored. |
| Inactive / delisted securities | **No** | IBKR historical API is keyed on live contracts; delisted names are generally unavailable. `backtest.db` contains **only survivors**. |
| Ticker changes | **No** | No permanent-id mapping; a rename silently breaks the symbol key (see NBIS↔`YNDX` EPIC anomaly in `canonical_instrument_registry_design.md`). |
| Stable permanent identifiers | **No** | Symbol string is the only id. IBKR `conId` is available at qualify time but not persisted. |
| Historical liquidity (ADV20 etc.) | **Partial** | Volume is present for equities (integer); **absent for CFDs** (`-1`). ADV20 in $ also needs a price×volume calc and FX for non-USD — FX is absent. |
| Point-in-time universe construction | **No** | Requires listing/delisting dates + delisted names + as-of membership. None exist. **Impossible from current data.** |

---

## 4. Survivorship bias — explicit limitation (task §8)

`backtest.db`'s 29 symbols are **today's surviving instruments**. Using them as a
"historical dynamic universe" would:

1. **Exclude every delisted / acquired / failed name** that would have been a candidate in
   2024–2026 → upward selection bias in any universe-level study.
2. **Encode look-ahead survivorship**: membership is defined by "still trading in 2026,"
   which is information unavailable at any historical decision date.
3. Be **unfixable by adding more current symbols** — the missing mass is the *non-survivors*,
   which the current providers do not supply.

**Design rule (binding):** Do **not** use today's surviving instruments as a historical
dynamic universe without documenting this bias (task §8). Any future point-in-time study
must source delisted securities + as-of universe membership from a proper provider, under
operator approval. Until then, dynamic-universe research is **shadow-only and
forward-looking** (DRAFT.md §13), where survivorship bias does not apply because membership
is decided in real time, not reconstructed.

### Secondary correctness hazard: RAW prices + no corporate actions

Independent of survivorship, RAW unadjusted prices corrupt the **frozen breakout maths**
across any split date: SMA50, SMA200, the 20-day-high breakout level, and the Wilder ATR/ADX
recurrences all read `close`/`high` directly (`backtest/breakout_strategy.py:125–130`). An
un-adjusted split (e.g. a 10:1) injects an artificial gap that the strategy would read as a
real price move. This is a **data-correctness** problem for this exact strategy, not a
performance nicety — and it is reported here as a data fact, with **no** outcome estimate
attached.

---

## 5. Proposed data architecture (design only — nothing acquired)

### 5.1 Primary research-data source — DEFERRED, operator approval required

Requirements a suitable provider must meet: ≥5y daily OHLCV, raw **and** adjusted series,
split/dividend events, listing/delisting dates, delisted/inactive coverage, ticker-change
history, stable permanent ids, and historical liquidity. Candidate categories (named for
the operator decision, **not** endorsed or signed up): survivorship-bias-free equity history
vendors (e.g. point-in-time fundamental/price vendors), or an exchange/data-vendor feed with
a delisting archive. **No selection is made here.** Item 17 / DRAFT.md carries this as the
top open decision.

### 5.2 IBKR recent-bar reconciliation source — feasible now

IBKR (already integrated, `bot/data.py`) is well-suited as the **recent-bar
reconciliation** feed: confirm that the chosen research provider's recent daily bars agree
with the broker's bars near the trade boundary, and supply the live/next-session price for
execution and exit management. It is **not** the universe-history source.

### 5.3 Canonical raw-bars schema (proposed, superset of today's `ohlcv`)

```text
raw_bars
─────────────────────────────────────────────────────────────────────────
canonical_instrument_id TEXT NOT NULL    -- registry key, NOT a ticker string
timeframe               TEXT NOT NULL    -- 'daily' (v1)
session_date            TEXT NOT NULL    -- exchange-local trading date
open_raw,high_raw,low_raw,close_raw  REAL NOT NULL   -- unadjusted
volume                  INTEGER          -- NULL where unavailable (not -1 sentinel)
adj_factor              REAL             -- cumulative split/dividend factor (NEW)
close_adj               REAL             -- adjusted close = close_raw * adj_factor (NEW)
source                  TEXT NOT NULL    -- 'IBKR' | '<research_provider>'
ingested_at             TEXT NOT NULL
dataset_snapshot_id     TEXT NOT NULL    -- FK → dataset_manifest (point-in-time pinning)
PRIMARY KEY (canonical_instrument_id, timeframe, session_date, source)
─────────────────────────────────────────────────────────────────────────
```

### 5.4 Corporate-actions schema (proposed — currently absent)

```text
corporate_actions
─────────────────────────────────────────────────────────────────────────
canonical_instrument_id TEXT NOT NULL
ex_date                 TEXT NOT NULL
action_type             TEXT NOT NULL    -- SPLIT | DIVIDEND | SYMBOL_CHANGE | DELIST
ratio_or_amount         REAL             -- e.g. 10.0 for 10:1 split; cash for dividend
old_symbol, new_symbol  TEXT             -- for SYMBOL_CHANGE
source                  TEXT NOT NULL
ingested_at             TEXT NOT NULL
PRIMARY KEY (canonical_instrument_id, ex_date, action_type)
─────────────────────────────────────────────────────────────────────────
```

### 5.5 Dataset snapshot manifest + hashing (point-in-time pinning)

Reuses the integrity discipline already practised in the Phase 3b archive
(`experiments/breakout_3b_result.md`: dataset SHA-256, code hashes, seed). Proposed:

```text
dataset_manifest
─────────────────────────────────────────────────────────────────────────
dataset_snapshot_id     TEXT PRIMARY KEY   -- e.g. 'dyn_univ_2026-06-08'
created_at              TEXT NOT NULL
provider                TEXT NOT NULL
universe_as_of_date     TEXT NOT NULL      -- the point-in-time membership date
n_instruments           INTEGER NOT NULL
raw_bars_sha256         TEXT NOT NULL      -- hash of the materialised bars export
corp_actions_sha256     TEXT NOT NULL
notes                   TEXT
─────────────────────────────────────────────────────────────────────────
```

Every research run pins a `dataset_snapshot_id` so results are reproducible and a later
universe change cannot silently alter a past study (the survivorship-trap antidote).

### 5.6 Idempotent incremental downloader (design only)

Pattern mirrors existing idempotent ingestion: `backtest/download.py:store_bars` already
upserts on `(symbol,timeframe,datetime)` PK and stamps `downloaded_at`; the regime scheduler
dedupes per trading day via `cache.has_for_day` (`scheduler.py:84`). Proposed downloader:
keyed on `(canonical_instrument_id, timeframe, session_date, source)`, resumable, rate-limit
aware (IBKR pacing `ib.sleep(12)` at `download.py:109`; IG 2.0s floor at `ig.py:43`), and
**never** re-downloads a pinned snapshot. **Not implemented in this task** (task §15 "do not
download a large new dataset").

### 5.7 Data-quality rules (proposed)

```text
DQ1  ≥250 valid completed daily bars before an instrument is structurally eligible (task §2)
DQ2  latest completed bar fresh (reuse bot/data.py:74 >4-day staleness reject)
DQ3  monotonic non-duplicate session_date per (instrument, source)
DQ4  no non-positive OHLC; high≥max(open,close); low≤min(open,close)
DQ5  volume present for equities (CFDs may be null, never the -1 sentinel)
DQ6  unresolved split/symbol-change near an active position → DATA_INELIGIBLE (state machine)
DQ7  cross-source agreement: research-provider recent bars reconcile to IBKR within tolerance
DQ8  adj_factor present and continuous wherever corporate_actions exist
```

DQ failures drive the `DATA_INELIGIBLE` state (see `dynamic_universe_state_machine.md`),
blocking new entries while leaving open-position exits fully managed.

---

## 6. Summary for the operator

* The system can fetch **recent** multi-year daily bars from IBKR for *live* names, and
  already stores ~2y for 29 survivors — enough only for the frozen fixed-14 work.
* It **cannot** build a survivorship-free, corporate-action-correct, point-in-time dynamic
  universe with current data. That needs a proper research-data provider (deferred,
  approval required) plus the raw-bars / corporate-actions / manifest schemas above.
* IG is unusable as a historical equity source today (demo entitlement block) and is
  designed as execution/reconciliation only.
* Until a provider is approved, dynamic-universe work stays **shadow-only and
  forward-looking**, sidestepping survivorship bias entirely.
