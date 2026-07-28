# Observation Window v2 — Pre-Registration

> **Pre-registration.** Criteria were fixed and merged **before** the Phase 3 restart on
> 2026-07-28, so they cannot be adjusted after seeing results. The window itself starts per
> §7.1 — the first full session after all seven §8 checks pass, not the restart evening.
> Per `docs/phase2_remediation_spec.md` §Phase 3, the success criteria are fixed **before**
> services come back up, so they cannot be adjusted after seeing results.
> All figures below were signed off by the operator on 2026-07-28.

- Baseline equity: **GBP 250,000** (Phase 1 close, `docs/flatten_record.md` §II.5)
- Risk per position: **1% of live equity, global** (`risk_fraction = 0.01`)
- Account: IBKR paper `DUQ141950` · Broker stops live (PR B) · Risk sizing live (PR D)
- Phase 2 complete: PRs A, B, C, C2, D merged

---

## 1. Why pre-register

The regime layer is on trial. Without a decision rule fixed in advance, any result can be
read as supporting whatever we already believe — a window that ends with "let's watch a bit
longer" is a window that never concludes. This document fixes what will be measured, for how
long, what counts as too little data, and what each outcome obliges us to do.

It also records that **the system being measured is not the system that was
walk-forward tested**. Phase 2 changed exit semantics and sizing. Measured expectancy is
therefore *not* directly comparable to pre-Phase-2 backtest results, and no comparison
between them may be made without restating that caveat.

---

## 2. What changed under the system since it last ran

### 2.1 Exit semantics (accepted, PR C + C2)

| | Before | After |
|---|---|---|
| Daily names, Tier-2 (trail/TP) | **never evaluated** | **every cycle** |
| Daily worst-case dead band | unbounded | **1 minute** (1-min cycle interval) |
| US/LSE 4hr names | 1 evaluation/session | **2** (added a pre-close window) |
| Confirmation latency, daily names | n/a | **~3 minutes** |

The daily bar-close window *was* the market close, and the bot returns early when the market
is shut, so the two never overlapped. Trailing stops and take-profits were dead code for
every daily-timeframe instrument.

**Consequence for measurement:** daily names will now exit on trail/TP where previously they
could only exit on the tier-1 emergency stop or a signal reversal. Expect shorter holding
periods and a different loss distribution — smaller average losses, more of them. This is
the intended fix, but per-trade statistics change for reasons **unrelated to the regime
layer**, and the §6 decision must not be confounded by it.

### 2.2 Sizing (PR D)

Equal-notional `int($1000/price)` is replaced by fixed-fractional risk sizing against the
**synthetic trail stop** distance, using equity read live from the broker at sizing time.
Position sizes differ substantially from any prior period — tight-stop names size up,
wide-stop names size down — so **absolute P&L is not comparable to history**. Expectancy in
**R multiples** (per unit of risk) is the comparable measure, which is why §4 specifies it.

If equity cannot be read, **new entries are blocked for that cycle** and an alert fires;
exits are unaffected. Those cycles are recorded (§4.3) because they bias trade count
downward.

### 2.3 Protective stops (PR B)

Every entry carries a broker-held stop at the emergency level, submitted atomically with the
entry. This is what makes the closed-market gap survivable.

### 2.4 Stop-ratio cap (pre-window safety change, PR #25)

**A deliberate configuration change made specifically for this window, after drafting §3 of
this document revealed the exposure.**

Because positions are sized on the *trail* stop but the broker holds the *emergency* stop, a
process or host death converts an intended 1% risk into a loss scaled by
`emergency_pct / trail_pct`. A sweep found **13 instruments above 3×** — not the 3 first
identified — with NVTS at **10×**.

All are capped at `emergency = trail × 3`:

| Trail | New emergency | Instruments |
|---|---|---|
| 1.0% | 3.0% | ANTO, NBIS, NVTS, CVX, MU, XAGUSD |
| 1.5% | 4.5% | AAPL, AVGO, MSFT, TSM, SHEL, VRT |
| 2.5% | 7.5% | CEG |

Trail stops were **not** touched, so the primary exit and position sizing are unchanged;
only the backstop narrowed. A test asserts the invariant against the real config so it
cannot silently regress.

**This is a change to the system under measurement and is recorded here as such.** It was
made before the window opens, not during it.

---

## 3. Process-death worst case, per instrument (post-cap)

At 1% risk on GBP 250,000 — **enabled instruments**, after the §2.4 cap:

| Symbol | Ccy | Trail | Emergency | Multiple | Loss if only the broker stop fires |
|---|---|---|---|---|---|
| NVTS | USD | 1.0% | 3.0% | 3.00× | GBP 7,500 |
| ANTO | GBP | 1.0% | 3.0% | 3.00× | GBP 7,500 |
| NBIS | USD | 1.0% | 3.0% | 3.00× | GBP 7,500 |
| AAPL | USD | 1.5% | 4.5% | 3.00× | GBP 7,500 |
| AVGO | USD | 1.5% | 4.5% | 3.00× | GBP 7,500 |
| MSFT | USD | 1.5% | 4.5% | 3.00× | GBP 7,500 |
| TSM | USD | 1.5% | 4.5% | 3.00× | GBP 7,500 |
| SGLN | GBP | 5.0% | 10.0% | 2.00× | GBP 5,000 |
| SSLN | GBP | 5.0% | 10.0% | 2.00× | GBP 5,000 |
| ANET | USD | 5.0% | 10.0% | 2.00× | GBP 5,000 |
| SCCO | USD | 5.0% | 10.0% | 2.00× | GBP 5,000 |
| PLTR | USD | 5.0% | 10.0% | 2.00× | GBP 5,000 |
| BARC | GBP | 5.0% | 10.0% | 2.00× | GBP 5,000 |
| SU | EUR | 4.0% | 5.0% | 1.25× | GBP 3,125 |

| | Before cap | After cap |
|---|---|---|
| Worst single position | GBP 25,000 (10% of equity) | **GBP 7,500 (3%)** |
| NVTS + ANTO + NBIS concurrent | GBP 50,000 (20%) | **GBP 22,500 (9%)** |

This is the residual accepted risk of the window: **a process death with the maximum
permitted concurrent exposure costs at most ~9% of the account**, against a 3% intended
risk. It is bounded, recorded, and accepted.

---

## 4. Metrics

Recorded per closed trade; computed at window end and at each weekly review.

### 4.1 Taken trades

| Metric | Definition |
|---|---|
| **Expectancy (R)** | Mean P&L per trade in units of initial risk. **Primary measure** — the only one comparable across the sizing change. |
| **Expectancy (GBP)** | Mean P&L per trade, for absolute context only. Not comparable to history. |
| **Profit factor** | Gross profit / gross loss. |
| **Win rate, avg win/loss (R)** | Distribution shape behind expectancy. |
| **Trade count** | Overall and **per instrument** (§5). |
| **Max drawdown (GBP and %)** | Against the GBP 250,000 baseline. |
| **Exit-reason split** | trail / take-profit / emergency / broker-stop / signal reversal / reconciliation. Directly tests whether §2.1 did what it claims. |

### 4.2 Regime-blocked trades (counterfactual)

From the shadow logs, for every entry the regime filter blocked: what would the trade have
returned under the same tier-1/tier-2 exit logic?

| Metric | Definition |
|---|---|
| **Counterfactual expectancy (R)** | Mean R of blocked trades had they been taken. |
| **Counterfactual profit factor** | As above. |
| **Blocked count** | Overall and per instrument. |
| **Block reason split** | Which rule blocked it. |

**The comparison that decides §6** is `E_taken` vs `E_blocked`. The regime layer earns its
place only if what it blocks is worse than what it lets through.

### 4.3 Integrity checks (not performance, but the window is void without them)

- Reconciliation alerts: **expected zero**. Any divergence means the measured book and the
  tracked book disagree, so trade records may be wrong.
- Positions opened without a broker-held stop: **expected zero**.
- Orphaned stop orders: **expected zero**.
- Equity-unreadable cycles: recorded. Entries are blocked during them, so they bias trade
  count downward and must be reported alongside it.

---

## 5. Minimum sample size

Below these, the window is **inconclusive by definition** — not "suggestive", not "leaning
towards". No keep/replace/retire decision may be taken.

| Claim | Minimum |
|---|---|
| Any overall expectancy claim | **30 closed trades** |
| Regime keep/replace/retire (§6) | **30 taken AND 30 blocked** |
| Any per-instrument claim | **10 closed trades for that instrument** |
| Any claim about a single exit reason | **10 occurrences of it** |

Rationale: trade-level P&L is heavy-tailed, and expectancy estimates from fewer than ~30
observations are dominated by a couple of outliers. 30 is a floor for a *directional* read,
not a threshold for statistical significance — which this design cannot deliver in a
reasonable calendar window and does not claim.

If the floor is unmet at window end, the only permitted outcomes are: extend by the §7
increment, or stop and record **inconclusive**. Never "decide anyway".

---

## 6. Regime layer: keep / replace / retire

### 6.1 The statistic

Let

```
D = E_blocked − E_taken        (both in R, from §4.1 and §4.2)
```

`D > 0` means the filter is blocking trades **better** than those it allows.

**Decisions are made on a bootstrap confidence interval for `D`, never on the point
estimate.** A point estimate at n=30 on heavy-tailed data will happily sit either side of
any threshold by luck alone.

| Parameter | Value |
|---|---|
| Method | Non-parametric bootstrap, percentile method |
| Resamples | **10,000** |
| Interval | **95%** |
| Resampling | Independent resampling of the taken and blocked trade sets, `D` recomputed per resample |
| Seed | Fixed and **recorded in the result** so the interval is reproducible |

### 6.2 The decision rule

Band: **±0.10R** — the smallest difference worth acting on at these sample sizes.

| Outcome | Condition on the 95% CI for `D` | Action |
|---|---|---|
| **KEEP** | CI lies **entirely below −0.10R** | The filter demonstrably blocks worse trades. Retain; re-evaluate next window. |
| **RETIRE** | CI lies **entirely above +0.10R** | The filter blocks trades better than it allows — it is costing money. Remove it. |
| **REPLACE** | CI lies **entirely within ±0.10R** | The filter is demonstrably not discriminating. Its complexity earns nothing; replace with a simpler rule or none. |
| **EXTEND** | CI **straddles** either band edge | Not yet decidable. Extend per §7 toward the 10-week hard stop. |
| **INCONCLUSIVE** | §5 floor unmet, **or** CI still straddling at the hard stop | Stop and record. **No change to the layer.** |

The CI must *clear* the band for KEEP or RETIRE; a CI merely whose midpoint clears it is not
sufficient. This deliberately makes "we don't know yet" a first-class outcome rather than a
forced choice.

### 6.3 Hard override

**If `E_taken ≤ 0` the strategy does not trade live, regardless of the regime verdict.**

A filter that improves a losing system still leaves a losing system. This override is
independent of §6.2 and is evaluated first.

---

## 7. Timing

### 7.1 When the clock starts

**The window dates from the first full trading session after all seven §8 checks pass —
not from the first cycle after restart.**

Services were restarted on **2026-07-28 at 18:21 ET, with markets closed**. Cycles ran and
four §8 checks passed that evening, but items 2, 3 and 5 cannot be observed without a live
session: no entry can be placed, and Tier-2 cannot evaluate, while every instrument is shut.
Dating the window from that evening would count hours in which the system could not have
done the thing being measured.

| | |
|---|---|
| Restart (services up, cycling) | 2026-07-28, 18:21 ET |
| **Window start** | **first full session on 2026-07-29, once §8 items 2, 3 and 5 are confirmed** |
| Recorded start timestamp | *to be filled in when §8 completes* |

If §8 does not complete on 2026-07-29 — no entry occurs, so items 2 and 3 stay open — the
clock still starts once **item 5 (Tier-2 evaluations)** is confirmed, since that is the check
that proves the measured system is the fixed one. Items 2 and 3 are then confirmed at the
first entry and recorded in §10 without moving the start date.

### 7.2 Duration

| | |
|---|---|
| Nominal duration | **4 calendar weeks** from the §7.1 window start |
| Hard minimum | 4 weeks **and** the §5 sample floor |
| Extension trigger | §5 floor unmet, **or** §6.2 returns EXTEND |
| Extension increment | **2 weeks**, at most **twice** (10 weeks total) |
| Hard stop | **10 weeks** — record the result, including "inconclusive" |
| Interim reviews | Weekly, **read-only**: report only, no parameter changes |

**No parameter, instrument, or filter change during the window.** A window whose system
changes mid-flight measures nothing. If a change is unavoidable (a safety defect), the
window **restarts**, and that restart is recorded here with its reason.

---

## 8. Pre-restart verification

To be confirmed on the first cycle, before the window is considered started:

1. Reconciliation runs and is **silent** — no divergence, no unprotected position, no orphan.
2. The **first entry carries a broker-held stop** at the emergency level, verified against
   the broker's working orders, not just a log line.
3. **Sizing matches the risk formula** against live equity — recompute one trade by hand.
4. Account equity reads successfully; no equity-unavailable entry block on cycle 1.
5. Daily-timeframe names show **Tier-2 evaluations in the logs** within the first session.
   Their absence means the PR C fix is not live.
6. The **stop-ratio cap is live** — spot-check that a capped instrument's broker stop sits at
   the new level, not the old one.
7. Services: bots re-enabled, APIs and nginx restarted per `docs/flatten_record.md` §II.6.1.

The deferred `CLAUDE.md` restart rule from PR #19 and every Phase 2 PR since is satisfied by
this restart.

### 8.1 Status as of the 2026-07-28 restart

| # | Check | Status |
|---|---|---|
| 1 | Reconciliation silent | ✅ no divergence, no unprotected position, no orphan; tracker 0 vs broker 0 |
| 2 | First entry carries a broker-held stop | ⏸ pending first entry |
| 3 | Sizing matches the risk formula | ⏸ pending first entry |
| 4 | Equity reads; no entry block | ✅ `Account equity: 251,006.98 (live)` every cycle |
| 5 | Tier-2 evaluations in logs | ⏸ markets closed at restart; due 2026-07-29 |
| 6 | Stop-ratio cap live | ✅ no instrument exceeds 3× |
| 7 | Services restarted | ✅ api → nginx → bot (re-enabled) |

IG (`cogniflowai-ig-api`, `cogniflowai-ig-bot`) remains **stopped** — see §11.

---

## 9. Restart record — database policy applied

The Phase 1 archive was not restored wholesale. Each database was classified as **history**
(safe, restore) or **live state / counter** (stale, start fresh), because stale state
re-entering a flat account creates divergence or contaminates a fresh measurement.

| Database | Classification | Action | Why |
|---|---|---|---|
| `regime.db` | history | **restored** | 12,143 shadow decisions, 518 classification cache rows |
| `news.db` | history | **restored** | 47,771 headlines |
| `backtest.db` | history | **restored** | 55,025 OHLCV bars, 85 walk-forward results |
| `advisor.db` | history | **restored** | dated append-only `advisor_reports` archive |
| `positions.db` | live state | **fresh** | held 4 stale LONGs (GOOGL, NBIS, AAPL, MSFT) against a flat broker — would fire 4 phantom divergences and block entries |
| `learning_loop.db` | counter state | **fresh** | see §11 |
| `layer3_silver.db` | live state | **fresh** | `silver_scalper_state` held SSLN intraday state from 2026-07-08 (`day_low`=`day_high`=4314.0); SSLN is enabled, so a three-week-old intraday range would corrupt the scalper. Its history table `layer3_trades` was empty — nothing to preserve |

All restores were verified by hash against the archive, and the archive itself re-verified
intact (9/9) afterwards. Code deployed: `/root/trading` moved from `350b28f` to `c44fe1f`
(17 commits), which is what actually put PRs A–D and the cap into production.

---

## 10. Sign-off log

| Item | Decision | Date |
|---|---|---|
| Stop-ratio exposure (NVTS 10×, ANTO/NBIS 5×) | **Capped at 3× for all instruments exceeding it** (PR #25); recorded in §2.4 as a deliberate pre-window safety change | 2026-07-28 |
| `risk_fraction` | **1%, global** | 2026-07-28 |
| Sample floors (§5), ±0.10R band (§6), durations (§7) | **Accepted** | 2026-07-28 |
| Decision statistic | **Refined**: bootstrap CI, not point estimates. KEEP/RETIRE only when the CI clears the band; a straddling CI at 4 weeks extends toward the 10-week hard stop | 2026-07-28 |
| `E_taken ≤ 0` hard override | **Confirmed as written** (§6.3) | 2026-07-28 |
| Window start date | **First full session after §8 completes**, not the restart evening (§7.1) | 2026-07-28 |
| `advisor.db` / `layer3_silver.db` | Classified by the established rule: advisor **restored** (history), layer3_silver **fresh** (stale intraday state) — §9 | 2026-07-28 |

Phase 3 services were restarted on 2026-07-28 after this document was merged. The window
itself starts per §7.1.

---

## 11. Post-window follow-ups

Named, deferred, and **not to be actioned during the window** — changing the system
mid-flight would void the measurement (§7.2).

### 11.1 Consecutive-loss counter has no era boundary

`LearningLoop._check_consecutive_losses` reads:

```sql
SELECT outcome FROM trades WHERE symbol = ? AND outcome IS NOT NULL
ORDER BY id DESC LIMIT 5
```

There is no filter on time, system version, or window. `_check_auto_disable` then writes
`enabled: false` into `instruments.json` at the threshold (default 3), so **outcomes produced
by one version of the system can auto-disable an instrument under a later, different one**.

This is why `learning_loop.db` was started fresh (§9). At restart the archive held 36
outcomes whose newest was 2026-07-08, and **SU already carried two trailing losses** — a
single loss under the new system would have disabled it on old-system evidence. Seven other
symbols carried one.

Starting fresh sidesteps the problem for this window; it does not fix it. The counter will
accumulate the same latent issue across any future system change.

**Proposed fix (post-window):** give the trades table an era/window marker and filter the
counter to the current era, so auto-disable only ever reasons about outcomes the current
system produced. Do not implement during the window.

### 11.2 IG demo market-data entitlement

`cogniflowai-ig-api` and `cogniflowai-ig-bot` remain **stopped**. The IG deployment logs
`insufficient bars: got 0, need 200` continuously — the known demo entitlement issue. IG is
out of scope for this window and is not measured by it.

Consequence: nginx still serves the IG vhost on **port 8083** with no backend behind it, so
requests there error. Accepted and left as-is.

### 11.3 Databases not carried into this window

`learning_loop.db` (§11.1) and `layer3_silver.db` (§9) were started fresh. Their archived
contents remain in `backups/20260726T210849Z/trading_live_dbs_20260728T1900Z/`, hash-verified
and untouched, should either be wanted later.
