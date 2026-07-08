# Trading Bot Safety Audit — Findings (Option B)

> **Read-only audit. Documentation-only output.** No order was placed, modified, or cancelled;
> no database was written; no code, config, service, or feature flag was changed. Live broker
> access was a single read-only IBKR probe (distinct clientId, `readonly=True`, positions/orders/
> executions only). Account identifiers are redacted.

- Working tree: `/root/trading` · Base branch: `breakout-strategy` · Deployed HEAD: `c6a1c00554a28187a748997c17d42ab2c29379da`
- Audit branch: `docs/safety-audit-option-b` · Date: 2026-07-08
- Prerequisite context: `docs/trading_bot_strategy_status_review.md` (PR #15)

---

## 1. Summary table

| Check | Verdict | One-line finding |
|---|---|---|
| **1 — Broker-side stops (IBKR paper)** | **UNPROTECTED (synthetic-only)** | 8 open positions at the broker, **0 working orders** — no broker-held stop exists for any position; protection depends entirely on the bot process staying alive. |
| **1 — Broker-side stops (IG demo)** | **BLOCKED (live) / UNPROTECTED (code path)** | Live fetch blocked by the known demo market-data entitlement issue; IG writer submits `MARKET` opens with all stop params null → synthetic-only by construction. |
| **1b — Position-tracking divergence** | **FINDING** | Broker holds 8 positions; the bot's `positions.db` tracks only 4. **AMZN, META, NVDA, XAUUSD are open at the broker but untracked** → no broker stop *and* no synthetic stop. |
| **2 — Sizing wiring (both accounts)** | **EQUAL-NOTIONAL** | `bot/sizing.py` *is* invoked live, but it computes `int(target_notional/price)` (~$1000), not ATR/stop-distance risk. Empirically confirmed on 7 recent entries. |
| **2c — Pre-order validation gate** | **ACTIVE** | `validate_order` wired on all three entry branches; consecutive-loss auto-disable also present and wired (threshold 3). |
| **3 — Exit cadence / dead band** | **~72h (4hr names) / UNBOUNDED (daily names)** | Tier-2 trail/TP only runs in a 5-min post-bar-close window that, for daily-timeframe names, falls *after* the market has closed → trail/TP never evaluates; ~65h weekend blind spot for all tiers. |
| **4 — Safety guards** | **INTACT (no drift)** | XAU/XAG hard-disabled (config-flip-proof), ANET exits-only, consecutive-loss auto-disable all intact. |

**Headline:** The guardrails that were the subject of Check 4 are intact, but the bot's **loss-containment model is structurally fragile**: no broker-held stops anywhere, several live positions untracked (no stop of any kind), equal-notional (not risk-based) sizing, and an exit-cadence defect that leaves daily-timeframe positions with their trailing stop **never evaluated**.

---

## 2. Detailed findings

### Check 1 — Broker-side protective stops

**1a. Code path (both writers): synthetic-only, no bracket/stop ever submitted.**
- IBKR entry is a bare market order — no `parentId`, `bracketOrder`, child STP, `auxPrice`, or `transmit` handling: `bot/orders.py:50-56` (`order.orderType='MKT'` … `self.ib.placeOrder(contract, order)`); delegated from `bot/brokers/ibkr.py:81-84`. `OrderManager.place`/`handle_signal` (`orders.py:43,149`) only ever build this one MKT order.
- IG open explicitly nulls every stop parameter: `bot/brokers/ig.py:280-297` — `create_open_position(order_type="MARKET", guaranteed_stop="false", stop_level=None, stop_distance=None, trailing_stop=None, …)`.
- A full-tree sweep for `bracket|parentId|ocaGroup|StopOrder|auxPrice|stopPrice|transmit` in order construction returned **zero** hits.
- Stops are tracked in Python only: `positions.db.open_positions.trail_stop` via `PositionTracker` (`bot/position_tracker.py:207-219`, ratcheted `243-275`), enforced by the bot loop (`check_exit` `position_tracker.py:277-329`; `check_emergency_stop` `331-363`), and exits are sent as fresh market orders (`bot/layer1.py:323` `broker.close_position(...)`). **If the bot process dies, positions have no stop at the broker.**

**1b. Live verification (IBKR paper, account `DU****950`) — read-only probe, clientId 77, `readonly=True`:**
- **Positions: 8.**

  | Symbol | secType | qty | avgCost | Tracked in positions.db? |
  |---|---|---|---|---|
  | MSFT | STK | 2 | 377.58 | yes (but entry_price=0.0 — data defect) |
  | AMZN | STK | 2 | 253.43 | **NO** |
  | AAPL | STK | 3 | 307.64 | yes |
  | NBIS | STK | 6 | 156.05 | yes |
  | XAUUSD | CFD | 1 | 5192.0 | **NO** (config `unmanaged_positions`) |
  | META | STK | 1 | 607.23 | **NO** |
  | NVDA | STK | 2 | 211.77 | **NO** |
  | GOOGL | STK | 3 | 333.65 | yes |

- **Open/working orders: 0** (`reqAllOpenOrders()` → count 0; `openOrders()` → 0). **No broker-held stop exists for any of the 8 positions.**
- Recent executions: 1 — `AAPL BOT 3 @ 307.31` (permId 435049656), matching today's entry.
- **Cross-check verdict: UNPROTECTED** — every position is synthetic-only, and 4 of 8 (AMZN, META, NVDA, XAUUSD) are not in `positions.db` at all, so they have **no stop of any kind** (the bot only enforces stops for symbols in its tracker). MSFT is tracked but with `entry_price=0.0` (a tracker data defect that distorts its synthetic stop math).

**1c. Live verification (IG demo): BLOCKED.** The IG deployment (`/root/trading-ig/`, `ig_acc_type:"demo"`) logs `insufficient bars: got 0, need 200` for every instrument continuously (`/root/trading-ig/bot_stdout.log`, e.g. 2026-07-07 22:39–23:56), i.e. the known demo market-data entitlement issue. IG is a separate deployment outside this tree's broker path (`instruments.json → broker:"ibkr"`); a fresh REST session was not initiated (out of scope, avoids demo session-limit risk). Code-path finding stands: IG is synthetic-only (1a).

### Check 2 — Sizing wiring trace

**2a. Static.** Quantity for a live entry is computed at `bot/layer1.py:244` `entry_qty = calculate_qty(inst, price, self.cfg.default_target_notional)` and injected at `:245` `inst['qty'] = entry_qty`. Both writers then read that mutated value: IBKR `bot/orders.py:159` `qty = inst['qty']` (placed `:170`/`:191`); IG `bot/brokers/ig.py:406` `qty = inst.get("qty",1)` → `create_open_position(size=qty)` `:288`. `bot/sizing.py:12-50` `calculate_qty` returns `int(target/price)` (`:45`); the fixed-qty fallback (`:31`) is dead because `default_target_notional=1000` is set in both `instruments.json` and `instruments_ig.json`. `bot.sizing` is imported only by `bot/layer1.py:30` (not backtest, not the shadow `bot/universe/sizing.py`).

**So sizing.py is wired, but the model is equal ~$1000 notional — there is no ATR/stop-distance risk term.** Verdict is identical for IBKR and IG.

**2b. Empirical (7 most recent entries, `learning_loop.db`, read-only).** Every entry matches `int(1000/price)` (target notional in the instrument's own currency), not risk-based sizing:

| id | symbol | entry | qty | qty×entry | int(1000/price) | matches equal-notional? |
|---|---|---|---|---|---|---|
| 71 | AAPL | 307.31 | 3 | $921.9 | 3 | ✔ |
| 70 | AAPL | 308.46 | 3 | $925.4 | 3 | ✔ |
| 69 | AVGO | 439.40 | 2 | $878.8 | 2 | ✔ |
| 68 | TSM | 416.92 | 2 | $833.8 | 2 | ✔ |
| 65 | MSFT | 420.85 | 2 | $841.7 | 2 | ✔ |
| 67 | SU | 272.60 | 3 | €817.8 | 3 | ✔ |
| 66 | BARC | 4.43* | 225 | £996.8 | 225 | ✔ (*443 pence) |

A genuine risk-based size would scale inversely with stop distance (e.g. AAPL trail 1.5% ≈ $4.6/share → qty set by a $-risk budget); instead qty is fixed by notional. **Verdict: EQUAL-NOTIONAL (sizing.py invoked but not risk-based), both accounts.**

**2c. Gate + auto-disable.** Pre-order validation gate is **active**: `_validate_entry` (`bot/layer1.py:577-594`) calls `validate_order` (`bot/order_validator.py:19`, enforcing qty/notional caps, price>0, max-open-positions, daily/weekly loss limits) on all three entry branches (`layer1.py:458` long, `:502` short, `:415` re-entry). Consecutive-loss **auto-disable is present and wired** (contra an initial narrow read): `bot/plugins/learning_loop.py:_check_auto_disable` (threshold `max_consecutive_losses=3`) fires from `post_trade → _record_exit` (`:180-181` `if outcome=='LOSS': self._check_auto_disable(symbol)`), plugin registered at `main.py:303`. One gap: the shortable-CFD reversal-flip entry (`layer1.py:373`) bypasses `_validate_entry`, but that path is dormant (long-only equities; shortable CFDs disabled).

### Check 3 — Exit cadence and dead band

**3a. Cadence.** Main loop = one cycle / `check_interval_mins=1` → 60s (`bot/config.py:63-64`, sleep `main.py:619`). Two tiers (`bot/position_tracker.py:1-18`, `bot/layer1.py:267-388`):
- **Tier 1 (emergency stop + per-instrument $ loss limit): every ~1 min, market-open only** (`layer1.py:269-276`).
- **Tier 2 (trailing stop + take-profit + peak update): only in a 5-min window after a bar-close boundary** (`is_bar_close`, `WINDOW_MINUTES=5` in `bot/bar_schedule.py:37`; gated `layer1.py:303-333`).
- **All checks skipped when market closed** (`layer1.py:205-209`; weekend hourly idle `main.py:500-508`).
- Bar interval per instrument: `4hr` or `daily` (`layer1.py:212-214`); exit price is the RTH bar close, not live ticks.

**Structural defect:** the daily/last-4hr bar-close boundaries equal the market-close times, while `is_open()` uses strict `< CLOSE`. So the 5-min Tier-2 window opens only *after* `is_open()` is already False. US daily boundary 16:00 vs open `<16:00` (`market_hours.py:113` vs `bar_schedule.py:28,33,113-115`). Result: **daily-timeframe names (SSLN, ANET, SCCO, NBIS) have their trailing stop and take-profit evaluated zero times**; US 4hr names get Tier-2 ~once/day (12:00 ET; the 16:00 close is missed).

**3b. Worst-case dead band (arithmetic).**
- Daily names: **unbounded** — Tier-2 never runs; trail/TP never checked for the life of the trade.
- US 4hr names: one Tier-2 eval/day (~12:00 ET). Breach just after Friday 12:05 → next eval Monday 12:00: `Fri 12:05 → Mon 12:00 = 71h55m ≈ 4,315 min (~72h)`. Intraday: `~1,435 min (~24h)`.
- All-tier weekend blackout (US): `Fri 16:00 ET → Mon 09:30 ET = 65.5h = 3,930 min` with **no exit of any tier** — not even the emergency stop.

**3c. RTH gap exposure.** All exit bars fetched RTH-only: `useRTH=True`, `barSizeSetting='1 day'/'4 hours'` (`bot/data.py:56-64`). Overnight/weekend prints are invisible; the first RTH bar can open past a synthetic stop. **The entire active equity/ETF universe is gap-exposed** (SSLN, ANET, SCCO, NBIS most, being daily; plus all 4hr names across the ~65h weekend window). The only near-24h instruments (XAU/XAG CFDs) are disabled for *entry* — though note a live XAUUSD CFD position exists (Check 1b) and is naked.

**3d. Emergency band vs intended risk.** Emergency stop is measured from **entry** at `emergency_stop_pct` (default `2×trail`), checked every cycle; trailing stop is from **peak** at `trail_stop_pct`, checked only at bar close. The gap is realized risk. Worst cases (notional ≈ $1,000): **NVTS trail 1.0% vs emergency 10.0% → ~9 pts, ~10× intended**; NBIS (daily) 1.0% vs 5.0% → 5× *and* trail never checked; TSM/AVGO/AAPL/MSFT 1.5% vs 5.0% → ~3.3×. **Yes — a zone exists where realized loss exceeds intended per-trade risk, up to ~10×.**

### Check 4 — Hard-disable and safety guards (INTACT, no drift)

- **XAUUSD/XAGUSD hard-disabled — INTACT.** `validate_hard_disabled_instruments` (`bot/guardrails.py:91-132`, fail-closed on missing `enabled`) is wired at bot startup (`main.py:880,918-923,937` → `sys.exit(1)`), `Config()` construction (`bot/config.py:46-51` → `ConfigGuardrailError`), and every API write (`api_server.py:250-255` backstop + per-route 409 pre-checks `:300,374,871,903`). Config state: both `enabled:false, hard_disabled:true, disabled_reason:"no_cfd_market_data_paper_account"`. A config flip to `enabled:true` makes the bot refuse to start and every save 409. IG config omits XAU/XAG entirely. *Nuance:* the guard sits at the config/startup layer, not at order submission — but the instrument can never reach `enabled=true`, so the trading path can't reach it. (Operational note, not guard drift: a legacy XAUUSD CFD position is still open at the broker and unmanaged — see Check 1b.)
- **ANET exits-only — INTACT.** `enabled:true, allow_new_entries:false` (`instruments.json`); enforced at `bot/layer1.py:241` then gated on every entry branch (`:345,398,443-447,487-491`); `validate_no_edge_guardrails` (`guardrails.py:66-88`) additionally forbids ANET being enabled without `allow_new_entries:false` or `experiment:true`.
- **Consecutive-loss auto-disable — INTACT.** `bot/plugins/learning_loop.py:_check_auto_disable` (threshold `max_consecutive_losses=3`, set in both configs), wired via `LearningLoop` plugin (`main.py:303`) → `post_trade`/`_record_exit` (`:180-181`) → `_disable_instrument` writes `enabled:false`.

---

## 3. Go/no-go input (facts only — decision not made here)

**Question:** Is the current bot safe enough to keep running unattended in paper/demo mode?

Facts bearing on it (this section makes no recommendation):

**Against unattended running:**
1. **No broker-held stops anywhere.** All protection is synthetic and dies with the process/connection (Check 1a; IBKR live confirms 8 positions / 0 orders).
2. **Untracked live positions.** 4 of 8 IBKR positions (AMZN, META, NVDA, XAUUSD) are not in `positions.db` → **no stop of any kind**, synthetic or broker. MSFT is tracked with `entry_price=0.0` (broken synthetic-stop math).
3. **Daily-timeframe positions never get a trailing stop or take-profit** — the Tier-2 window falls after market close (Check 3a). Their only backstop is the wider emergency stop.
4. **Dead band up to ~72h (4hr names) / unbounded (daily names); ~65h weekend window with no exit check of any tier** (Check 3b).
5. **Sizing is equal-notional, not risk-based** — position risk is not normalized to stop distance, so realized loss per trade varies widely and the emergency band reaches ~10× intended risk on some names (Checks 2, 3d).
6. **RTH-only data** leaves the whole universe gap-exposed past synthetic stops (Check 3c).

**In favor / mitigating:**
7. Configured safety guards are **intact**: XAU/XAG hard-disabled and config-flip-proof, ANET exits-only, consecutive-loss auto-disable active (Check 4).
8. A pre-order validation gate (qty/notional caps, max-open-positions, daily/weekly loss limits) runs before every entry (Check 2c).
9. Per-instrument $ loss limits and an every-cycle emergency stop provide a coarse every-minute backstop **while the process is alive and the market is open** (Check 3a).
10. This is a **paper** account (IBKR `DU****950`); IG demo is data-blocked and effectively not trading (Check 1c). No real capital is at risk in paper/demo.

**Proposed follow-up tasks (NOT implemented here; listed with rough blast radius):**
- **F1 — Attach broker-held stops at entry** (bracket/attached STP for IBKR, `stop_distance` for IG). Blast radius: high — changes every entry submission path (`orders.py`, `brokers/ibkr.py`, `brokers/ig.py`); needs paper soak.
- **F2 — Reconcile tracker vs broker positions** (adopt/stop or flag untracked broker positions; fix MSFT `entry_price=0.0`). Blast radius: medium — `position_tracker.py` + a reconciliation step; read broker positions on startup.
- **F3 — Fix the Tier-2 bar-close window** so daily/last-bar trail/TP evaluates before `is_open()` flips false. Blast radius: medium — `bar_schedule.py`/`market_hours.py` boundary logic; affects all instruments' exit timing.
- **F4 — Decide sizing model** (keep equal-notional explicitly, or move to stop-distance risk sizing). Blast radius: medium — `sizing.py` + config; changes position sizes.
- **F5 — Close/hedge the naked legacy XAUUSD CFD** or bring it under management. Blast radius: low-medium — operational, plus `unmanaged_positions` policy.

---

## 4. Repository and branch status

- Production/deployed working tree: `/root/trading`
- Base branch: `breakout-strategy`
- Audit branch: `docs/safety-audit-option-b`
- Current deployed HEAD before this audit branch: `c6a1c00554a28187a748997c17d42ab2c29379da`
- Documentation-only commit: `4b1f6a98b5adbf335265c6388534bb14eec9ed89`
- PR: `PR_LINK_PLACEHOLDER`
- Status: documentation-only branch; no strategy, runtime, config, DB, snapshot, broker order, or service changes.

**This audit does not authorize any code, config, order, or service change.** It records read-only findings only. Live broker access was limited to one read-only IBKR probe (positions/open orders/executions); zero orders were placed, modified, or cancelled; zero database writes were performed.
