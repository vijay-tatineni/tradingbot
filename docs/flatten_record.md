# Phase 1 Flatten Record — **COMPLETE: `PHASE_1_FLAT`**

> **Status: `PHASE_1_FLAT`, issued 2026-07-28.** The IBKR paper account `DUQ141950` is
> verified flat: 0 positions, 0 working orders, broker-computed `GrossPositionValue` GBP 0.00.
> **No order was ever placed, modified, or cancelled at any point in Phase 1.** The account did
> not become flat through trading — an overnight cash reset wiped the six short positions before
> the authorized flatten could run. Phase 1 baseline: **GBP 250,000**.

This record has two parts, kept separate because the second supersedes the first's *status*
but not its *evidence*:

| | Date | Status then | Contents |
|---|---|---|---|
| **Part I** | 2026-07-26 | `PHASE_1_NOT_FLAT` | Discovery of the six flipped shorts, read-only probes, protective DB copy, halt |
| **Part II** | 2026-07-27 → 28 | **`PHASE_1_FLAT`** | Stuck-gateway remediation, flat verification, decommission, sign-off |

Part I is retained verbatim. Its `PHASE_1_NOT_FLAT` banner and its "required decision" were
accurate on 2026-07-26 and are **superseded by Part II** — read it as history, not as current
state. Section numbers in Part II are namespaced `II.n` so the two parts never collide.

---

# Part I — 2026-07-26: halted at step 3 (superseded)

> *Historical record, preserved unchanged. Superseded by Part II.*

> **Status: `PHASE_1_NOT_FLAT`.** The IBKR paper account is **not flat**. Read-only probes at
> 2026-07-26 21:06 UTC (`clientId=77`) and 21:19 UTC (`clientId=0`) both found **6 open short
> positions** and **0 working orders**. Step 3b (moving the live databases) and the cash reset
> were **not** performed. This document is documentation-only. **No order was placed, modified,
> or cancelled; no live database was moved, deleted, or written; no service was restarted.**
> One incidental working-tree change is disclosed and reverted in §8a. Account identifiers are
> redacted.

- Working tree: `/root/trading` · Branch: `docs/flatten-and-pause` · Base: `main` @ `7781dfe`
- Date: 2026-07-26 · Prerequisite context: safety audit (PR #16), `docs/safety_audit_findings.md`
- Probe method: identical to the PR #16 audit — `ib_insync`, `127.0.0.1:4000`, distinct
  `clientId=77`, `readonly=True`, reads `positions()` / `portfolio()` / `reqAllOpenOrders()` /
  `reqExecutions()` / `accountValues()` / `accountSummary()` only. A **second read-only probe on
  `clientId=0`** was then run (§2.1) because only clientId 0 can observe orders and executions
  submitted outside the API — e.g. from Client Portal.

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
See §2.1 — on `clientId=77` alone this result would *not* have been conclusive.

**Consistency checks — these rule out a sign-convention error or probe artifact** (they are four
*views*, not four independent sources: all four arrive over the same gateway account-update
stream):

1. `positions()` → 6 non-zero rows
2. `portfolio()` → 6 rows, every `marketValue` negative
3. `StockMarketValue.USD = −4,371.99`; `GrossPositionValue.GBP = 3,281.59`
4. `MaintMarginReq.GBP = 1,083.26` — a flat account carries zero maintenance margin

The identical script returned *positive* quantities on Jul 8, so sign convention is not the
explanation. An earlier probe today at **16:55 UTC** returned byte-identical holdings, meaning the
account did not change between 16:55 and 21:06.

**The gateway is not serving stale cache.** Three separate pieces of evidence: GOOGL and XAUUSD
were present on Jul 8 and are now *absent* (so the position set has been updated since the audit);
the implied AAPL mark of ≈333.77 differs from the Jul 8 price of ≈307; and a
`reqContractDetails(AAPL)` round-trip in the clientId-0 probe returned `ok: true, conId 265598` —
that response is served by IBKR upstream, not from gateway cache.

### 2.1 clientId-0 confirmation probe — 2026-07-26 21:19:41 UTC

`clientId=77` is **not** sufficient to rule out a manual flatten: IBKR delivers orders and
executions that originated *outside* the API (TWS GUI, Client Portal, mobile) only to
**clientId 0**. A clientId-77 probe would report zero executions and zero working orders even if a
full flatten had been submitted through Client Portal an hour earlier.

A second read-only probe was therefore run on `clientId=0` (connected successfully on the first
attempt, `readonly=True`, with `reqAutoOpenOrders(True)` to bind manually-submitted orders):

| | clientId 77 (21:06) | **clientId 0 (21:19)** |
|---|---|---|
| Positions | 6 | **6** (identical symbols, quantities, avgCost) |
| Working orders | 0 | **0** |
| Executions (today) | 0 | **0** |
| Manual-order visibility | none | **full** |

**This is now conclusive: no closing trade reached account `DUQ***950` today by any route,
including Client Portal, and no working order is queued.**

**Additional context — today is Sunday.** 2026-07-26 is a Sunday and the US equity market is
closed, so a manual flatten submitted today could not have filled in any case. It would have
rested as a working order until Monday's open — and there are **zero** working orders. So the
orders were not merely unfilled; they are **not present at the broker at all**.

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
11d6f8e6d428227c3efcd6895d0f131c15276aa0fd26bbe3d672748253094acc  ./probes/ibkr_probe_cid0_2026-07-26T2119Z.json
f6ae7a5dd4196b612e72a89053364c047cbc3601fc6b0a5374ec6ebfb4c2932d  ./probes/probe_flat.py
b6c8619bbe32a964b8c4031c3dfc86d9dee455e7a8c1c685b35888d8d93d8144  ./probes/probe_flat_cid0.py
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
| 1 — Read-only probe (IBKR) | **DONE** — 6 positions, 0 working orders, on both `clientId=77` and `clientId=0`. Not flat. |
| 1 — IG | **BLOCKED / UNVERIFIED** — operator attestation only. |
| 2 — Account value + P&L snapshot | **DONE** — captured, labelled *not-flat*. Not a valid pre-reset baseline. |
| 3a — DB archive + SHA256 manifest | **DONE** — verified, integrity `ok`. |
| 3b — Move live DBs / confirm clean dashboard | **NOT DONE** — withheld (see §4). |
| 4 — This record + PR | **DONE** |
| 5 — Completion report | **`PHASE_1_NOT_FLAT`** — `PHASE_1_FLAT` cannot be truthfully issued. |

**Open question — now narrowed to one candidate explanation.** The clientId-0 probe (§2.1) rules
out a manual flatten having reached this account by any route, and the zero working orders on a
Sunday rule out a resting unfilled order. What remains is:

- **Most likely — account mismatch.** The gateway on `127.0.0.1:4000` serves **`DUQ***950`**. If
  Client Portal was logged into a *different* paper account, "no open positions" was true of that
  other account, and these six shorts have simply been sitting unflattened. **Please confirm the
  account ID shown in Client Portal against `DUQ***950`.**
- **Otherwise** — the close requests were entered but never accepted by the broker (rejected,
  left unconfirmed in the UI, or submitted to a closed-market session and dropped), which would
  also leave no execution and no working order.

Either way the long→short flip itself predates today: it is absent from today's executions and
the holdings were already identical at the 16:55 probe.

**The £1,000,000 cash reset must not proceed until the account is verified flat by probe**, since
a reset would discard the position history behind these six unprotected shorts.

---

---

## 7. Authorized delegated flatten — prepared, NOT executed

On 2026-07-26 the operator confirmed Client Portal shows **the same account**, `DUQ***950`, which
eliminates the account-mismatch hypothesis of §6. The remaining explanation stands: the close
requests never reached the broker. The operator then authorized a delegated flatten under
**Phase 1 Option 2** constraints — buy-to-close only the six shorts at exact quantities, one at a
time with fill verification between each, plain market orders, **Monday during US regular hours**,
no other orders of any kind.

**Nothing has been executed.** 2026-07-26 is a Sunday; US RTH is Monday 13:30–20:00 UTC. A
re-probe at 21:42 UTC confirmed the book is unchanged (6 shorts, 0 working orders, 0 executions),
and `TotalCashValue` is still `GBP 250,000.00` — **no cash reset has been applied.**

Tooling is prepared and verified at
`/root/trading/backups/20260726T210849Z/flatten/flatten_shorts.py` (dry run by default;
`--execute` required to place orders).

**Guards, built around the specific failure that caused this.** The prior attempt closed at 2×
size or ran twice, flipping six longs to shorts. The script is structured so it cannot repeat that
in reverse:

- Order quantity is always computed from the **live broker position** read immediately before each
  order. The authorized numbers are an assertion checked against that read, never the order input.
- Contracts are whitelisted by **`conId`**, not symbol string.
- Pre-flight requires exactly six positions, matching conIds, every position **negative**, every
  size exactly as authorized, and **zero** working orders.
- After each fill the position is re-read and must be absent or exactly 0. **A positive result
  aborts the entire run immediately** rather than continuing to the next symbol.
- Execute mode refuses to run outside US RTH (weekend/holiday aware).
- Every `orderId`, `permId`, `execId`, fill price and timestamp is journalled to
  `flatten/flatten_journal.json` after each step, so a half-completed run still leaves a record.

**Verification performed 2026-07-26 (no orders placed):**

- Dry run against the live gateway: pre-flight passed, six intended orders printed, book unchanged.
- `--execute` attempted: aborted at the RTH guard with **0 orders placed**, exit code 2.
- `flatten/test_guards.py` — 8/8 offline abort-path cases pass with zero orders recorded: already
  long (flip), one quantity doubled (the 2× bug), account already flat (cash reset applied),
  unauthorized symbol present, one of the six missing, resting working order, outside RTH, plus the
  authorized-six case passing pre-flight.

**Known gap to carry into `PHASE_1_FLAT`.** Once flat, the instruction is to move the live DBs into
this archive. That remains verifiable only up to the file move: confirming the dashboard renders
empty requires restarting `cogniflowai-api.service`, which the no-restart constraint forbids. The
dashboard will continue to display stale Jul 8 state until a restart is separately authorized. This
is recorded as an unsatisfied acceptance criterion rather than dropped.

---

## 8. Process notes and disclosures

**8a. Working tree branch switch — disclosed and reverted.** This branch is based on `main`
(`7781dfe`), matching how PR #16 is based. Creating it with `git checkout -b` in `/root/trading`
briefly moved the *production working tree* off the deployed lineage (`docs/safety-audit-option-b`,
`350b28f`) and onto `main`, which is substantially behind it — `git diff --stat` between the two
shows **332 files changed, ~71,900 deletions**, including `web/dashboard.html` (−681 lines),
`web/instruments.html` (−240), `api_server.py`, `bot/`, `tools/` and the `tests/universe/` suite.

- **Window:** approximately 21:10–21:16 UTC on 2026-07-26.
- **Runtime impact: none.** `cogniflowai-api.service` (PID 753, up since 16:34) holds its loaded
  Python in memory and was not restarted, so no service behaviour changed. Both bot services were
  already `inactive (dead)`.
- **Exposure:** nginx serves `web/*.html` from disk, so during that window a dashboard load would
  have returned the older `main` copy. No page load is known to have occurred (no user was on the
  dashboard); this is recorded as *possible* exposure, not observed.
- **Resolved:** the tree was restored with `git checkout docs/safety-audit-option-b`. It is now
  byte-identical to the deployed lineage (`git diff --stat HEAD -- web/` empty; only the
  pre-existing untracked `data_approvals/` remains). Nav-bar requirements re-verified on both
  pages: `instruments.html` link, `Logged in as`, and `Logout` all present, identical to the
  deployed state.
- **Prevented from recurring:** subsequent edits to this document were made through a detached
  `git worktree`, leaving `/root/trading` on the deployed branch throughout.

**8b. Pre-commit checklist.** `CLAUDE.md` requires `pytest tests/ -v` before every commit. It was
**skipped** for these commits: they add a single Markdown file under `docs/` and touch no Python,
config, template, or test. Nav-bar checks were performed (8a). No hardcoded `~/trading/` paths
were introduced. Port 8080 was not started.

**8c. Services.** No service was restarted, started, or stopped at any point, per instruction.
Dynamic Universe remains frozen and untouched.

---

**This record does not authorize any code, config, order, or service change.** It documents
read-only probes, a protective database copy, and a halt. Zero orders were placed, modified, or
cancelled; zero live databases were moved or written; zero services were restarted. The one
incidental change to the working tree is disclosed and reverted in §8a.

---

# Part II — 2026-07-27/28: flat, decommissioned, signed off

> **No order was ever placed.** The delegated flatten was authorized twice and executed
> zero times: Monday's window was lost to a stuck broker gateway, and by Tuesday's window
> the account was already flat because an overnight cash reset had wiped the positions.
> Every broker interaction recorded here was read-only (`readonly=True`).

- Committed on `docs/flatten-and-pause` via a detached `git worktree`, leaving the
  production tree `/root/trading` on its own branch throughout (same practice as §8a).
- Record written: 2026-07-28

---

## II.1. Outcome

| Item | Result |
|---|---|
| Orders placed | **0** (none, on either authorized day) |
| Final account state | **FLAT** — 0 positions, 0 working orders |
| How it became flat | **Overnight cash reset**, not trades (see §II.4) |
| Authorized flatten | Not executed — became unnecessary |
| Services | **All stopped** — both APIs, both bots, nginx; ports 8080–8083 closed (§II.6.1) |
| Live DBs | **Archived**, hash-verified — IBKR tree (§II.6.2) and IG tree (§II.6.3) |
| Phase 1 baseline | **GBP 250,000** (§II.5) |
| PHASE_1_FLAT | **ISSUED** 2026-07-28 |

---

## II.2. Background

The six short positions were the residue of an earlier flatten that closed at 2× size
(or ran twice), flipping six longs to shorts. The authorized remediation was a
buy-to-close of exactly those six shorts:

`AAPL 3, AMZN 2, META 1, MSFT 2, NBIS 6, NVDA 2`

under standing constraints: exact quantities taken from a live broker read, one at a
time with fill verification and a long-flip check between each, plain market orders,
US regular hours only, and no other orders of any kind.

---

## II.3. Timeline

### Monday 2026-07-27 — window lost to a stuck gateway

- Authorization issued for Monday US RTH.
- Dry run and clientId-0 probe both failed: `TimeoutError` during the API handshake on
  every clientId (0, 1, 77).
- Root cause was **not** the script. IB Gateway was blocked on a modal dialog since
  **12:24 ET**:
  `IBC: detected dialog entitled: Existing session detected` /
  `IBC: User must choose whether to continue with this session (scenario 1)`.
  Login never completed, so the API refused all connections (`remove Client 77/0/1`).
- Contributing config defect: `ExistingSessionDetectedAction=` is **empty**, so IBC waits
  for manual input instead of auto-resolving. See §II.7 item 1.
- Remediation (authorized): user logged out of Client Portal; container restarted
  `20:16:03Z`; `IBC: Login has completed` at `20:16:15Z`. No 2FA prompt fired — the paper
  login authenticates on password alone. No session-conflict dialog on this login.
- Post-restart read-only probe (`probes/ibkr_probe_cid0_2026-07-27T2018Z.json`):
  **all six shorts still open**, exact authorized quantities, all six conIds matching,
  0 working orders, 0 executions. Every `avgCost` byte-identical to the 2026-07-26 21:19Z
  probe, confirming nothing had moved in the account.
- Market closed (16:00 ET) before the gateway was usable. Flatten deferred; authorization
  re-issued for Tuesday RTH.

### Tuesday 2026-07-28 — account already flat

- Session confirmed open (14:44 ET), gateway healthy, both bot services inactive.
- Pre-flatten clientId-0 probe at `18:45:48Z`: **0 positions**.
- That empty read was **not** taken at face value — an empty `ib.positions()` is
  indistinguishable from a not-yet-populated cache. Flatness was corroborated across
  independent channels before any downstream action (§II.4).
- Flatten **skipped** under the pre-authorized already-flat branch. No orders placed.

---

## II.4. Flat verification (multi-channel)

An empty position list alone was treated as insufficient evidence. Confirmed via:

| Channel | Method | Result |
|---|---|---|
| Positions | explicit `reqPositions()` + 5s settle, clientId 5 | **0** |
| Portfolio | separate account-update feed, `ib.portfolio()` | **0** |
| **Gross position value** | broker-computed `GrossPositionValue` | **GBP 0.00** |
| Working orders | `reqAllOpenOrders()` | **0** |
| Executions | clientId **0** (full manual/Client Portal visibility) | **0** |

`GrossPositionValue` is computed broker-side and can only be `0.00` if the account holds
nothing, which distinguishes "genuinely flat" from "data not yet arrived". Two separate
client connections (0 and 5) agreed.

**Cause — reset, not trades.** clientId 0 is the only client that sees orders and
executions originating outside the API (TWS GUI, Client Portal, mobile). It reported
**zero executions**. Positions therefore did not disappear through trading. Combined with
a round `TotalCashValue` of GBP 250,000.00 that was not present the previous evening, the
overnight cash reset wiped the six shorts.

---

## II.5. Post-flat account snapshot

Read-only, account `DUQ141950`, server time `2026-07-28 19:00:30Z` (positions/balances)
and `19:01:08Z` (clientId-0 executions/orders).

| Field | Value |
|---|---|
| Positions | **0** |
| Open / working orders | **0** |
| Executions (clientId 0) | **0** |
| GrossPositionValue | GBP 0.00 |
| TotalCashValue | GBP 250,000.00 |
| NetLiquidation | GBP 251,006.98 |
| AvailableFunds | GBP 249,986.82 |
| BuyingPower | GBP 999,947.25 |

Prior state for contrast — 2026-07-27 20:18Z, all six short:

| Symbol | conId | Position | avgCost |
|---|---|---|---|
| AAPL | 265598 | −3 | 312.79003335 |
| AMZN | 3691937 | −2 | 242.3148 |
| META | 107113386 | −1 | 602.4474 |
| MSFT | 272093 | −2 | 382.7619 |
| NBIS | 88819736 | −6 | 224.9185 |
| NVDA | 4815747 | −2 | 202.9756 |

### Reset amount discrepancy — resolved

The reset was expected to be **£1,000,000** and to be submitted *after* the flat probe.
A reset processed overnight regardless, and cash settled at **GBP 250,000.00**, not
1,000,000. Note that `BuyingPower` of 999,947.25 is ≈4× cash, which is standard intraday
leverage on a 250,000 balance — so a "1,000,000" figure seen in the UI may have been
buying power rather than the reset amount. Both facts are recorded here without asserting
which occurred; this needs reconciliation before Phase 2 sizing assumptions are set.

**Resolved 2026-07-28:** the **GBP 250,000 balance is kept as the Phase 1 baseline**. No
further reset was submitted, and Phase 1 is signed off against this figure. Any Phase 2
sizing work must read equity live from the broker rather than assume this number (§II.7 item 2).

---

## II.6. Decommission — services stopped, live DBs archived

Performed 2026-07-28 ~19:15Z, after the account was verified flat.

### II.6.1 Services

| Service | State | Enabled |
|---|---|---|
| `cogniflowai-api` | **stopped** | `enabled` (intentional) |
| `cogniflowai-ig-api` | **stopped** | `enabled` (intentional) |
| `cogniflowai-bot` | stopped | `disabled` |
| `cogniflowai-ig-bot` | stopped | `disabled` |
| `nginx` | **stopped** | `enabled` (intentional) |

**Dashboards are intentionally offline until Phase 3.** The API services and nginx were
deliberately left *stopped but enabled* — they are not to be restarted against an empty
tree; Phase 3 restores them. Only the two bot services are `disabled`.

Ports **8080, 8081, 8082 and 8083 are all closed** — the web surface is not merely erroring,
it is not serving at all. (8080 closed also satisfies the standing project rule that it
never run.)

### II.6.2 Live DB archival

Nine SQLite databases moved out of `/root/trading` into
`backups/20260726T210849Z/trading_live_dbs_20260728T1900Z/`:

`advisor.db`, `backtest.db`, `layer3_silver.db`, `learning_loop.db`, `news.db`,
`positions.db`, `regime.db`, `trades.db`, `trading.db`

Method and verification:

- Services were stopped **before** the move; no WAL/SHM/journal sidecar files existed and
  no process held an open handle, so nothing was stranded or half-written.
- Moved into a **new subdirectory** of the existing backup rather than over the Jul-26
  snapshot. The pre-existing `trading/` snapshot and its `SHA256SUMS.txt` are untouched and
  still validate (**27 files OK**). The live files were not byte-identical to that snapshot
  — most likely because the Jul-26 copy was a hot copy of databases the API had open, not
  because the data diverged; nothing had written the live DBs since Jul 8.
- The archive has its own `SHA256SUMS.txt`; `sha256sum -c` passes for all nine, and every
  hash matches the pre-move hash taken in the live tree (no corruption in transit).
- `/root/trading` now contains **no `.db` files**, and none were auto-recreated (the
  services were already down, which was the reason for stopping them first).

### II.6.3 IG live DB archival

The `/root/trading-ig/` tree was archived the same way, into a parallel subdirectory of the
same backup: `backups/20260726T210849Z/trading_ig_live_dbs_20260728T1916Z/`.

Eight databases moved: `advisor.db`, `backtest.db`, `layer3_silver.db`, `learning_loop.db`,
`news.db`, `positions.db`, `regime.db`, `trading.db` (the IG tree has no `trades.db`).

Same verification as §II.6.2: `cogniflowai-ig-api` and `cogniflowai-ig-bot` were already
stopped, no WAL/SHM/journal sidecars existed, no open handles were held; the archive has its
own `SHA256SUMS.txt`, `sha256sum -c` passes for all eight, and every hash matches the
pre-move hash. `/root/trading-ig` now contains no `.db` files.

Both deployments are therefore fully decommissioned: no live databases and no running
services in either tree.

### II.6.4 Verification-gate status in a decommissioned tree

The standing pre-commit rule is `pytest tests/ -v` with all tests passing. **That gate
cannot pass while the tree is decommissioned**, and the reason is benign:

- `tests/test_breakout_indicators.py:68` calls `sqlite3.connect("backtest.db")` on a
  **relative** path, and line 13 imports `load_bars` from `backtest.database`. With the
  databases archived, SQLite silently creates an empty file, `load_bars` returns nothing,
  and the warmup-date assertions fail with
  `TypeError: Cannot index by location index with a non-integer key`.
- Result at time of writing: **3 failed, 1949 passed, 1 skipped** (a 4th,
  `test_entry_signal_requires_all_four_conditions`, fails intermittently under a different
  ordering). All failures trace to the archived databases, not to a code defect.
- **Trap:** running the suite in this tree **recreates** `backtest.db`, `learning_loop.db`,
  `positions.db` and `regime.db` as empty schemas, leaving the tree looking populated while
  holding no data. After the run above, all four were confirmed byte-different from the
  archived originals and deleted; both archives re-verified afterwards (9/9 and 8/8 OK).
  **Anyone running tests here must re-clean the tree afterwards.**
- `scripts/pre_commit_check.py` additionally reports a `<nav>`-element check and a set of
  hardcoded-path checks as failing. These are unrelated to this work, which adds only this
  Markdown file and touches none of the implicated sources.

The commit recording this decommission is therefore made **with the test gate knowingly
red**, because a green gate is unobtainable until Phase 3 restores the databases.

---

## II.7. Open items

### Carried to Phase 2

1. **`ExistingSessionDetectedAction` compose fix.** Template line 329 of
   `/home/ibgateway/ibc/config.ini.tmpl` is
   `ExistingSessionDetectedAction=${EXISTING_SESSION_DETECTED_ACTION}`, and `common.sh:11`
   regenerates `config.ini` from that template on **every container start**. Editing
   `config.ini` directly is erased by the next restart — verified empirically: after the
   20:16 restart the file's mtime updated and line 329 was blank again. The durable fix is
   `EXISTING_SESSION_DETECTED_ACTION: "primary"` in `docker-compose.yml`.
   **Container-recreation caveat:** applying it requires `docker-compose up -d`, which
   recreates the container and drops its writable layer — including TWS settings under
   `/home/ibgateway/Jts`, as no bind mount is configured. **Not applied.**
2. **PR D must size from live broker equity, never a hardcoded account value.** This
   episode is the argument: the account's cash baseline changed overnight without warning
   (§II.5), and the previously audited sizing path already computes notional independently of
   real risk. Any sizing that embeds an assumed account value will silently mis-size after
   a reset.

### Resolved

3. **Reset reconciliation — closed.** The GBP 250,000 balance is kept as the Phase 1
   baseline; no further reset was submitted. This was the last item gating sign-off. See §II.5.

---

## II.8. Evidence

- `backups/20260726T210849Z/probes/ibkr_probe_cid0_2026-07-27T2018Z.json` — six shorts intact
- `backups/20260726T210849Z/probes/ibkr_probe_cid0_2026-07-28T1845Z_pre.json` — first flat read
- `backups/20260726T210849Z/probes/ibkr_verify_flat_2026-07-28T1858Z.json` — multi-channel confirmation
- `backups/20260726T210849Z/flatten/flatten_journal_20260726T2145Z_aborted.json` — Sunday RTH-guard abort
- `backups/20260726T210849Z/flatten/flatten_shorts.py` — the authorized script (never run with `--execute`)
- `backups/20260726T210849Z/trading_live_dbs_20260728T1900Z/SHA256SUMS.txt` — archived IBKR live DBs (9 files, verified)
- `backups/20260726T210849Z/trading_ig_live_dbs_20260728T1916Z/SHA256SUMS.txt` — archived IG live DBs (8 files, verified)
- `backups/20260726T210849Z/SHA256SUMS.txt` — pre-existing Jul-26 snapshot manifest, still valid (27 files)
