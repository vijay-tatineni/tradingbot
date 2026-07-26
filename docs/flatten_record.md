# Phase 1 Flatten Record — **HALTED AT STEP 3**

> **Status: `PHASE_1_NOT_FLAT`.** The IBKR paper account is **not flat**. A read-only probe at
> 2026-07-26 21:06 UTC found **6 open short positions** and 0 working orders. Steps 3b (moving the
> live databases) and the cash reset were **not** performed. This document is documentation-only.
> **No order was placed, modified, or cancelled; no live database was moved, deleted, or written;
> no code, config, service, or feature flag was changed.** Account identifiers are redacted.

- Working tree: `/root/trading` · Branch: `docs/flatten-and-pause` · Base: `main` @ `7781dfe`
- Date: 2026-07-26 · Prerequisite context: safety audit (PR #16), `docs/safety_audit_findings.md`
- Probe method: identical to the PR #16 audit — `ib_insync`, `127.0.0.1:4000`, distinct
  `clientId=77`, `readonly=True`, reads `positions()` / `portfolio()` / `reqAllOpenOrders()` /
  `reqExecutions()` / `accountValues()` / `accountSummary()` only.

---

## 1. Headline

The operator reported flattening all IBKR paper positions manually and verified "no open
positions" in Client Portal. **The broker does not agree.** The account holds six short equity
positions whose sizes are the exact negation of the six longs recorded in the Jul 8 audit.

| | Jul 8 audit (long) | 2026-07-26 21:06 UTC (now) | Interpretation |
|---|---|---|---|
| MSFT | +2 | **−2** | flipped |
| AMZN | +2 | **−2** | flipped |
| AAPL | +3 | **−3** | flipped |
| NBIS | +6 | **−6** | flipped |
| META | +1 | **−1** | flipped |
| NVDA | +2 | **−2** | flipped |
| GOOGL | +3 | *absent* | genuinely closed |
| XAUUSD (CFD) | +1 | *absent* | genuinely closed |

Every one of the six flipped by exactly its own size. That is the signature of **sell orders at
2× position size, or sell-to-close submitted twice** — the positions were reversed, not closed.
GOOGL and the naked legacy XAUUSD CFD (audit finding F5) did close cleanly.

---

## 2. Step 1 — Read-only probe (IBKR paper, account `DUQ***950`)

Probe at **2026-07-26 21:06:00 UTC** (server time). Connected: yes. Managed accounts: 1.

**Positions: 6 (expected 0).**

| Symbol | secType | conId | Position | avgCost | marketValue (USD) | unrealizedPnL (USD) |
|---|---|---|---|---|---|---|
| AAPL | STK | 265598 | **−3** | 312.79003335 | −1,001.31 | −62.94 |
| AMZN | STK | 3691937 | **−2** | 242.3148 | −463.10 | +21.53 |
| META | STK | 107113386 | **−1** | 602.4474 | −594.20 | +8.25 |
| MSFT | STK | 272093 | **−2** | 382.7619 | −762.80 | +2.72 |
| NBIS | STK | 88819736 | **−6** | 224.9185 | −1,137.00 | +212.51 |
| NVDA | STK | 4815747 | **−2** | 202.9756 | −413.58 | −7.63 |
| | | | | | **−4,371.99** | **+174.44** |

**Working orders: 0.** `openTrades()` → 0, `openOrders()` → 0, `reqAllOpenOrders()`/`orders()` → 0.
Consistent with the audit's Check 1 finding: **no broker-held stop protects any of these six
short positions.** A naked short has unbounded loss on an up-move, so the exposure is now
*worse* in character than the long book the audit assessed.

**Executions (current trading day): 0.** `reqExecutions(ExecutionFilter())` returned zero fills.
The same call on Jul 8 did surface that day's AAPL fill, so the filter works as expected.
**No closing trade reached this account today.**

**Four independent confirmations that the account is not flat** (this is not a probe artifact):

1. `positions()` → 6 non-zero rows
2. `portfolio()` → 6 rows, every `marketValue` negative
3. `StockMarketValue.USD = −4,371.99`; `GrossPositionValue.GBP = 3,281.59`
4. `MaintMarginReq.GBP = 1,083.26` — a flat account carries zero maintenance margin

The identical script returned *positive* quantities on Jul 8, so sign convention is not the
explanation. An earlier probe today at **16:55 UTC** returned byte-identical holdings, meaning the
account did not change between 16:55 and 21:06.

### IG demo — NOT VERIFIED (attestation only)

IG flatness could **not** be independently confirmed and is **not** recorded as verified:

- The IG bot's own shutdown log is explicit: `/root/trading-ig/bot_stdout.log`,
  2026-07-08 21:55:19 — `"Positions remain open on IG. Manage manually."`
- Live IG fetch remains blocked by the known demo market-data entitlement issue documented in the
  audit (Check 1c): continuous `insufficient bars: got 0, need 200`. No fresh REST session was
  initiated (out of scope; avoids demo session-limit risk).
- The operator's check in the IG web platform is recorded as an **operator attestation**, not a
  probe result. Status: **BLOCKED / UNVERIFIED**.

---

## 3. Step 2 — Account value and P&L snapshot

**Captured before any further action.** Labelled honestly: this is a **pre-flatten, not-flat**
snapshot taken with six positions still open. It is *not* the closing P&L snapshot the phase
called for, and it must not be used as the pre-reset baseline until the account is actually flat.

Account `DUQ***950`, 2026-07-26 21:06:00 UTC:

| Tag | Value |
|---|---|
| NetLiquidation | **GBP 247,661.52** |
| TotalCashValue | GBP 250,000.00 |
| CashBalance | GBP 250,000.00 · USD 0.00 · BASE 250,000.00 |
| GrossPositionValue | GBP 3,281.59 |
| StockMarketValue | USD −4,371.99 · BASE −3,281.59 |
| **UnrealizedPnL** | **BASE +130.94 · USD +174.44 · GBP 0.00** |
| **RealizedPnL** | **BASE 0.00 · USD 0.00 · GBP 0.00** |
| AccruedCash | BASE 943.11 (GBP 954.44 · USD −15.09) |
| EquityWithLoanValue | GBP 246,702.96 |
| AvailableFunds / ExcessLiquidity | GBP 245,595.00 / GBP 245,623.82 |
| BuyingPower | GBP 982,379.99 |
| InitMarginReq / MaintMarginReq | GBP 1,107.96 / GBP 1,083.26 |
| ExchangeRate (USD→BASE) | 0.750594 |

**Closing order IDs / executions: none.** No closing orders or executions were observed for this
account on the probe date, so there are none to record. The section is intentionally empty rather
than omitted.

Full probe JSON (both of today's probes) is preserved in the archive below — see
`probes/`. Once the £1,000,000 cash reset is performed, this JSON is the only remaining way to
reconstruct these six positions.

---

## 4. Step 3 — Database archive (copy completed; **live DBs NOT moved**)

**Archive directory:** `/root/trading/backups/20260726T210849Z/`
(`backups/` is gitignored; the archive lives on disk, the manifest is recorded here.)

Copies were taken with `sqlite3 ".backup"` — not `cp` — because the API server holds these
databases open in WAL mode and a bare `cp` without the `-wal` sidecar can drop recent commits.
Zero-byte files were copied directly. `pragma integrity_check` returns `ok` for
`trading/positions.db`, `trading/learning_loop.db`, and `trading-ig/positions.db`.

### SHA256 manifest

```
70d7631c3fd6ac39d30959f93b065f27c3afe729f96a0b395ffc6b17570b5c1b  ./probes/ibkr_probe_2026-07-26T1655Z.json
4f699ecb3e013ddfc91a6d3d14c0ac58819f02f61dd0914dc264f7fdee92ddd9  ./probes/ibkr_probe_2026-07-26T2106Z.json
f6ae7a5dd4196b612e72a89053364c047cbc3601fc6b0a5374ec6ebfb4c2932d  ./probes/probe_flat.py
46511b2890dfd8c15c8cfb4da68a2a8391cfb5a771015a029494e7aede0e43c8  ./trading-ig/advisor.db
046bd35fda69356f9886755a88f91cfd285305b660a0e5413624a512b33333e4  ./trading-ig/backtest.db
964d7717480689df9ebdf7df60fe11c3cdd3370c7d388ba2f2d95e33347b6c83  ./trading-ig/instruments_ig.json
6dead57a229cfc51af972203ca16818b348d0db5bd9683e98e99425079bea06b  ./trading-ig/layer3_silver.db
6ff8db6131641c2339a4e5443b0bece40ec1f4fbf0427f0ae9a2189ea741bbb3  ./trading-ig/learning_loop.db
8666b50342b901828b03782ac8f108d82518352926d9745cf3c4a1d8834f021c  ./trading-ig/news.db
64a6c7d4923ea22a8b559b5d3df58c082b9decf41992ec395b114ebe32d6a4c8  ./trading-ig/positions.db
286aeb4a139d3ae4752947ede384d2ce151b32f3c34f3caed432d7e8b94b4b41  ./trading-ig/regime.db
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  ./trading-ig/trading.db
a58a714791a5b2aee24bc8e8698816e616b50528cc53f7ba46a30159a8e8c51c  ./trading/advisor.db
2e9cbff2490422c04c3974db620f8de42d615d972f18427c6681296f64089999  ./trading/backtest.db
4c1b8231084058546e40c29557d15210a88654e9c86cf9794cb3b7b211ed4384  ./trading/instruments.json
30530464633b5a68e632f566f1d143578cecb9dab1dd624a7b90d8130d92f196  ./trading/instruments_ig.json
baa7da3cce8796ed27da0a60f654fc1e89ff26ea8f2ebaf67878ca0105fff357  ./trading/layer3_silver.db
09b75932fe4889934278b62221ae20099abf791d5442ac8a48e84c468527bfe4  ./trading/learning_loop.db
27aece75029c3545d6922782d2ac6c295839056d144dca2126d7f29c08597d79  ./trading/news.db
36d182643471da54beae41f3ac243209a654dabcb16ddce9a66369d365d349ce  ./trading/positions.db
f1225fd756ff8745be4ac6e5b5639894fad25f625cd7262d6ff5bb7519cdcae3  ./trading/regime.db
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  ./trading/trades.db
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  ./trading/trading.db
```

(`e3b0c442…52b855` is the SHA256 of the empty file — `trades.db` and both `trading.db` files are
0 bytes.)

### What was deliberately NOT done

**The live databases were not moved.** The phase called for moving them into the archive so the
bot rebuilds clean state on restart. That step was withheld because:

1. **It is premature while the account is not flat.** "Clean state" is only meaningful once the
   broker is flat. Discarding the tracker now would destroy the last on-disk link to the position
   history behind the six open shorts, ahead of a cash reset that erases the broker-side record.
2. **It cannot be verified without a service restart.** `cogniflowai-api.service` (PID 753) has
   been running since 16:34 UTC and holds these files open. Confirming an empty dashboard would
   require a restart, and the instruction for this phase was **do not restart any services**.

The copy half of step 3 is complete and is purely protective, so it was carried out in full.

---

## 5. Dashboard / `positions.db` staleness — CONFIRMED, with a correction

The operator's reading is **confirmed**: the dashboard's 3 positions and
"last update 2026-07-08 21:54 UTC" are stale state from before the Phase 0 stop.

- `positions.db.pnl_cache.updated_at` = `2026-07-08T21:54:28.319216+00:00` for all three currency
  rows (EUR −6.30, GBP +37.92, USD +380.17) — the last bot cycle before the stop.
- `positions.db` mtime is 2026-07-08 21:54; `cogniflowai-bot.service` is `inactive (dead)`, so
  nothing has written trading state since.
- **3 tiles vs 4 DB rows explained:** `open_positions` holds **four** rows — GOOGL, NBIS, AAPL,
  MSFT. GOOGL is `enabled: false` in `layer1_active`, so the dashboard filters it out. Nothing is
  missing; the DB simply holds one more than is displayed.

**Correction to "stale":** it is worse than stale, and this matters for any restart plan. The
tracker holds **four longs** (GOOGL +3, NBIS +6, AAPL +3, MSFT +2 — MSFT still carrying the
`entry_price = 0.0` defect from audit finding F2) while the broker holds **six shorts**. On
AAPL, MSFT and NBIS the tracker is **directionally inverted** against reality, and META, AMZN and
NVDA are open at the broker with no tracker row at all. If the bot were restarted against this
state it would be reasoning about a book that does not exist and could act on the wrong side.

---

## 6. Phase status and required decision

| Step | Status |
|---|---|
| 1 — Read-only probe (IBKR) | **DONE** — 6 positions, 0 working orders. Not flat. |
| 1 — IG | **BLOCKED / UNVERIFIED** — operator attestation only. |
| 2 — Account value + P&L snapshot | **DONE** — captured, labelled *not-flat*. Not a valid pre-reset baseline. |
| 3a — DB archive + SHA256 manifest | **DONE** — verified, integrity `ok`. |
| 3b — Move live DBs / confirm clean dashboard | **NOT DONE** — withheld (see §4). |
| 4 — This record + PR | **DONE** |
| 5 — Completion report | **`PHASE_1_NOT_FLAT`** — `PHASE_1_FLAT` cannot be truthfully issued. |

**Open question that discriminates between the two explanations:** which account did the operator
see in Client Portal? The gateway on `127.0.0.1:4000` serves `DUQ***950`. If Client Portal was
showing a *different* paper account, this is a benign account-mismatch and the six shorts have
been sitting unflattened. If it was showing this account, the flatten orders never reached the
broker and the long→short flip predates today. Zero executions today is consistent with both.

**The £1,000,000 cash reset must not proceed until the account is verified flat by probe**, since
a reset would discard the position history behind these six unprotected shorts.

---

**This record does not authorize any code, config, order, or service change.** It documents a
read-only probe, a protective database copy, and a halt. Zero orders were placed, modified, or
cancelled; zero live databases were moved or written; zero services were restarted.
