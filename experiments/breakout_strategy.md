# CogniflowAI — Breakout Strategy Validation (v3.2, FINAL FROZEN PRE-REGISTRATION)

**Status:** FINAL. This is the last document round. Design + all implementation-determinism rules frozen. After this: Commit A → implement+test → Commit B → Phase 3a → explicit decision before 3b. Further details resolved in Commit B only as they implement the already-frozen design. A genuine bug/contradiction may reopen the doc; an ordinary implementation choice does not. Any strategy-rule change creates a NEW pre-registration, never a silent edit.
**Date:** 2026-06-05
**Hypothesis:** A globally-parameterized breakout trend-following system inspired by Donchian-style systems (adds MA + ADX filters and ATR exits; not a Turtle strategy).
**Why:** Triple-confirmation failed Phase 2 OOS (pooled PF 1.06/105 trades, single-instrument-dependent, fragile, unstable selection — not investable).

---

## 1. Gated sequence

**3a — Plausibility & implementation check.** Confirm execution correctness, exits/sizing match spec, estimate frequency, catch broken economics. MUST NOT produce a shortlist, remove instruments, change parameters, or promote winners.

**3b — Four-window chronological OOS portfolio evaluation (only after 3a confirms clean implementation).** Full frozen 14-instrument universe, ONE global rule, NO training-window selection, NO parameter optimization. Pooled OOS trades only. (Deliberately NOT called 'nested walk-forward' — there is no in-window selection to nest. The name is fixed to prevent anyone later adding selection the plan forbids.)

**3c — Three-arm overlay (only if 3b passes).** A: base. B: + frozen rule filter. C: + Claude overlay (risk-reduction only).

---

## 2. Frozen strategy

### Entry (all true, completed bars)
- Close > preceding 20-day **intraday high**, excluding current bar
- Close > SMA200
- SMA50 > SMA200
- ADX(14) > 25

### Indicators — exact
- **SMA** 50 and 200 (simple, not EMA)
- **Wilder ATR(14)**
- **Wilder ADX(14)**
- All computed on data available at **signal-bar close** only
- Exact internal formula/library version frozen by code hash at commit

### Execution
- Enter at **next session open + spread + adverse slippage**

### Exits (first to trigger)
**Initial stop** (fixed at entry): `active_stop = entry_fill − 2.0 × signal_bar_ATR14`

**Trailing stop — MONOTONIC RATCHET (can only rise, never fall):**
```
After each completed bar:
  update highest_completed_close_since_entry
  candidate_trail = highest_completed_close_since_entry − 3.0 × current_ATR14
  active_stop = max(previous_active_stop, initial_stop, candidate_trail)
  # active_stop becomes effective for the FOLLOWING session
```
The active stop may rise or hold; it must NEVER fall, even if ATR spikes.

**Intra-session stop fill:**
```
if session_open <= active_stop:  fill at session_open − adverse costs
elif session_low <= active_stop: fill at active_stop − adverse costs
```
(Gap-through handled by the open<=stop branch — fills at open, not the stop level.)

**Trend-break exit:** if completed close < SMA50 and no stop already exited → sell at **next session open − half-spread − adverse slippage**; commission deducted separately from cash/P&L. (Adverse execution reduces a long-sale price, matching the stop-fill convention.)

(No take-profit / target exists in this strategy.)

### Re-entry rules
- No same-session re-entry
- Maximum one entry per instrument per signal bar
- A new entry requires a LATER completed bar that independently satisfies all entry conditions
- Open positions ignore subsequent entry signals

### Frozen risk configuration
- Risk per trade: **0.50%** of current marked-to-market portfolio equity
- `risk_budget = equity × 0.005`; `quantity = risk_budget ÷ (entry_fill − initial_stop)`
- Max notional per instrument: **20%** of equity (reduce quantity if exceeded)
- Max open positions: **5**
- Max aggregate initial stop risk: **2.50%**
- No leverage; integer shares only; skip if quantity < 1

### Frozen parameters (chosen blind, never tuned)
| Param | Value |
|---|---|
| Breakout lookback | 20-day intraday high (excl. current bar) |
| SMA fast/slow | 50 / 200 |
| ADX threshold | > 25 |
| ATR period | 14 (Wilder) |
| Initial stop | 2.0 × ATR (fixed at entry) |
| Trailing stop | 3.0 × ATR (ratchet, recalc each bar, monotonic) |
| Risk/trade | 0.50% equity |
| Max notional/instrument | 20% |
| Max positions | 5 |
| Max portfolio heat | 2.50% |

**One global parameter set. All instruments. No per-instrument tuning, ever.**

---

## 2.5 Execution determinism (frozen — so two correct implementations agree)

### Complete daily event lifecycle (frozen)
**At session open:**
1. Apply gap stops to positions held before the session (open ≤ active_stop → fill at open − adverse costs).
2. Execute scheduled SMA50 trend-break exits (sell at open − half-spread − slippage; commission separate).
3. Recalculate marked-to-market equity, available cash, available slots.
4. Process pending entry orders (from prior completed bar) in **frozen universe order** — never by a ranking signal like highest ADX (that would add a strategy rule).
5. Initial stops for newly entered positions become active immediately.

**During the session:**
6. For every open position INCLUDING those entered at today's open: if session low ≤ active_stop, execute the stop at the frozen adverse fill. **A position entered at today's open CAN be stopped out later the same session.**

**At session close:**
7. Mark all surviving positions to market.
8. Update highest completed close since entry.
9. Recalculate ATR and candidate trailing stop.
10. Ratchet active_stop upward (max of prev/initial/candidate); the updated stop is effective NEXT session.
11. Calculate trend-break and entry signals for the next session.

### Sequential sizing with no-leverage cash constraint (frozen)
Risk budgets use the same post-exit equity snapshot, but available cash updates sequentially per accepted entry:
```
quantity = min(risk_based_quantity, notional_cap_quantity, available_cash_quantity)
available_cash_quantity = floor((available_cash − est_entry_commission) / entry_fill)
```
After each accepted entry, deduct its fill notional + commission before processing the next candidate. This guarantees non-negative cash — five 20% positions plus commissions cannot create leverage.

### Frozen universe order (deterministic tie-break — RECORD EXACT LIST IN doc AT COMMIT A)
The 14 instruments in frozen `instruments.json` array order. Earlier-listed wins contested slots. Arbitrary-but-neutral, faithful to live. **The exact ordered 14-symbol list is written into this document before Commit A.**

> **[RECORDED AT COMMIT A — frozen universe order]** Committed `instruments.json` `layer1_active` array order, `enabled` instruments only (the array holds 29 entries; the 14 enabled ones in their array positions are the frozen universe). Earlier-listed (lower #) wins contested slots.
>
> | # | Symbol | Exchange | Currency |
> |---|--------|----------|----------|
> | 1 | SGLN | SMART | GBP |
> | 2 | SSLN | SMART | GBP |
> | 3 | TSM | SMART | USD |
> | 4 | AVGO | SMART | USD |
> | 5 | ANET | SMART | USD |
> | 6 | SU | SMART | EUR |
> | 7 | SCCO | SMART | USD |
> | 8 | ANTO | SMART | GBP |
> | 9 | PLTR | SMART | USD |
> | 10 | NBIS | SMART | USD |
> | 11 | AAPL | SMART | USD |
> | 12 | MSFT | SMART | USD |
> | 13 | BARC | SMART | GBP |
> | 14 | NVTS | NASDAQ | USD |

### OOS period & development split (FROZEN RULE — dates recorded at Commit A after the data audit)
- **OOS period:** the final 12 complete calendar months of the common dataset, split into four contiguous 3-month windows.
- **Development period (Phase 3a only):** ALL data before the OOS start date.
- **The four OOS window date ranges are written into this document at Commit A**, derived mechanically from the data audit (NOT chosen to affect outcomes).

> **[RECORDED AT COMMIT A — OOS split, derived mechanically from the outcome-blind data audit (2026-06-06); no returns / PF / Sharpe / rankings / signal outcomes inspected]**
> - Common dataset end: **2026-03-20** (earliest last-bar across the 14 — the US-session group binds it; NVTS extends later but the US names stop here)
> - OOS end: **2026-02-28** (last complete calendar month ≤ common dataset end)
> - OOS start: **2025-03-01**
> - Four contiguous 3-calendar-month OOS windows:
>   - **W1: 2025-03-01 … 2025-05-31**
>   - **W2: 2025-06-01 … 2025-08-31**
>   - **W3: 2025-09-01 … 2025-11-30**
>   - **W4: 2025-12-01 … 2026-02-28**
> - Development period (Phase 3a only): **2024-03-21 … 2025-02-28**

### Eligibility requirement (FROZEN — no escape hatches)
Every frozen instrument must have ≥ 200 completed prior bars AND all indicators (SMA200/SMA50/ATR14/ADX14/20-day-high) fully defined before the OOS start date.
**If any instrument fails this:** do NOT shorten warm-up, stagger eligibility, remove the instrument, or move the OOS boundary after seeing results. **Acquire more historical data before running Phase 3b.** (12 months OOS is less powerful than 18, but with ~2 years of data it preserves a credible untouched holdout. An underpowered honest test beats a larger contaminated one.)

> **[RECORDED AT COMMIT A — eligibility result: PASS (all 14)]** Every one of the 14 frozen instruments has ≥ 200 completed bars before OOS start (2025-03-01) and all required indicators (SMA200 / SMA50 / ATR14 Wilder / ADX14 Wilder / 20-day high) fully defined before that date. Binding constraint per instrument is SMA200 (the 200th bar). Tightest case: **NVTS** — 215 prior bars, 200th bar / first-all-indicators-defined on **2025-02-06** (~15 sessions of slack). Established names reach 200 bars by ~2025-01-06/08. Warm-up confirmed; no warm-up shortening, eligibility staggering, instrument removal, or boundary move was required or performed.

### Window-boundary mechanics
- Pre-OOS bars used ONLY for indicator warm-up; no trades before the first OOS date.
- Portfolio starts entirely in cash at OOS start.
- Positions carry across contiguous OOS window boundaries — NOT liquidated/reset at quarter boundaries.
- A boundary-crossing position contributes daily P&L to the window each day falls in (daily MTM attribution).
- Window return from the daily MTM equity curve during that window.


---

## 3. Trade frequency is empirical
The four-condition entry may be restrictive (ADX rises after trends begin). If 3a shows low frequency, that is a recorded finding — NOT a reason to lower ADX or shorten the breakout. The frozen rule is not loosened to manufacture trades.

---

## 4. Phase 3b pass criteria (frozen — ALL must hold)
- 100+ pooled genuinely-OOS trades (under 100 → provisional)
- OOS profit factor ≥ 1.15 (base costs)
- OOS Sharpe ≥ 0.50
- Positive net under base costs
- Positive net under 1.5× cost stress
- ≥ 3 of 4 OOS windows positive
- No single instrument > 30% of total profit
- After removing 5 best OOS trades: net P&L not worse than **−2% of initial OOS equity**
- **Max drawdown ≤ 15% (hard fail > 15%; target ≤ 10%)**
- Episode-block bootstrap CI reported

### Drawdown (frozen)
- From **daily marked-to-market portfolio equity** (cash + open positions + all costs)
- Target ≤ 10%; caution 10–15% (needs strong results + concentration check); **hard fail > 15% under base costs — not offsettable by any other metric**
- Stressed (1.5×) drawdown reported as robustness info; the 15% hard gate applies to base-cost run

### Sharpe (frozen)
- Daily MTM portfolio returns, annualized ×√252, includes cash + open positions, net of all costs, risk-free = 0%

### Bootstrap (frozen)
- 90% confidence level
- Deterministic random seed (recorded)
- Episode = trades grouped by **portfolio-wide transitive overlap**: if Trade A overlaps Trade B and B overlaps C, all three are one episode even if A and C don't directly overlap. Overlap = holding periods intersect in time. (Exact definition fixed in code at Commit B.)
- Sample count: 10,000 (recorded)

### Concentration (interpretation, NOT a gate)
Top-five-trade profit contribution: ≤ 50% acceptable · 50–70% yellow flag · > 70% serious investability concern. Informs interpretation; does not replace the −2% pass/fail rule.

---

## 5. Primary reporting — portfolio metrics
OOS CAGR · annualized vol · Sharpe · Sortino · max drawdown (§4) · Calmar · PF · trade count · avg exposure / time in market · turnover · worst rolling 12 months · profit contribution by instrument · profit contribution by quarter. PF is diagnostic only.

---

## 6. Universe scope
Frozen 14 (equities/tech/metals; small, sector-concentrated). Pass conclusion is scoped: "this global rule validated on this specified universe" — NOT "breakout validated generally." No replacements after seeing results. Diversified universe is a later separate test.

---

## 7. Phase 3c — Claude overlay (only if 3b passes)
**Historical testing anonymized:** Claude receives only numeric feature packets (`instrument_id: "ASSET_07"`, indicator values) — NO ticker, name, date, news, or outcome text. Store per call: model version, prompt version+hash, temperature, feature packet, input hash, raw + parsed output, cost.
**Evidence standard:** Arms A/B historical OOS acceptable; Arm C historical anonymized = exploratory only; **final Claude promotion requires fresh FORWARD paper-shadow evidence.**
**Claude constraints:** may ONLY reduce/block risk — never create trades, select direction, change stops, resize, tune per-instrument, override limits, read incomplete bars, or rewrite its prompt. Invalid output → UNCLEAR.
**Promotion (ALL):** base passed 3b · improves net OOS expectancy · improves Sharpe or materially cuts drawdown · positive after costs · beats Arm B · 30+ Claude-vs-rule disagreements · not one-instrument-reliant · doesn't over-thin trades · 100+ candidates · fresh forward paper evidence for live gate.

---

## 8. Two-stage freeze (you cannot hash code before it exists)

**Commit A — DESIGN PRE-REGISTRATION (before implementation):**
- Commit this v3.2 document verbatim. No strategy-rule changes after this commit.
- Record the frozen 14-symbol universe order (the tie-break, §2.5).

**Data audit (before Commit A — mechanical, NO outcomes):** report per instrument: earliest+latest date, completed-bar count, date each instrument first has all required indicators defined, missing-data gaps. This audit contains NO returns, PF, Sharpe, rankings, or strategy outcomes. The four OOS window dates and the eligibility check are derived from it and written into the doc at Commit A.

**Commit B — IMPLEMENTATION LOCK (after code + tests, before running 3a):**
- Implement strategy; complete unit + parity tests.
- Populate and record: dataset snapshot/hash · exact 14 tickers + exchanges · trading calendar + timezone per instrument · corporate-action treatment · split-adjusted OHLC method · dividend treatment · FX source + timestamp rule · transaction-cost config file/hash · signal-generator + simulator + indicator-formula code commit hash.
- Commit before running Phase 3a. No code/data/config changes after this commit without invalidating the run.

**If Phase 3a finds a bug:** mark the run invalid · fix · new Commit B (implementation lock) · rerun 3a from the beginning. (A bug fix is the ONLY allowed reason to change locked code.)

---

## 9. Does NOT do
Build overlay before base validates · let 3a produce a shortlist · tune per-instrument · let Claude create/direct trades · touch bot/ or main.py · promote to live or take investment on backtest results.

---

## 10. Honest expectations
Best: modest OOS edge on trending names, validates. Middle: better than triple-confirmation but break-even after costs (common for simple breakouts in chop). Worst: fails like triple-confirmation. PF near 1.0 after honest costs = no edge; do not round up. This is the **2nd** hypothesis tested — track the count; it must not become the first of twenty.

---

## 11. Scale-and-invest bar
Robust OOS edge (not provisional/single-instrument) · FCA implications understood before outside capital · operational hardening before real money · concentration watched as investability flag. At scale commission floor shrinks (improves economics IF edge real). No near-term outcome is "raise money"; best is "justify more paper validation + more history."

---

## 12. First Claude Code tasks (two-stage freeze)
**Commit A (now):** commit this v3.2 spec verbatim to `experiments/breakout_strategy.md`; record the frozen 14-symbol universe order. No design changes after.
**Then implement:** build breakout signal on the existing honest backtester (next-bar fills, costs, ATR-risk sizing WITH caps, MONOTONIC ratchet stop, per-session processing order §2.5, 5-cap, re-entry rules, warm-up eligibility). Unit + parity tests including a ratchet-monotonicity test.
**Commit B (after tests, before run):** populate + record all §8 hashes. Immutable thereafter.
**Run 3a — DEVELOPMENT PERIOD ONLY (pre-OOS data), never touches the four OOS windows.** Plausibility/implementation check.
3a reports ONLY: signal counts, entry/exit trace samples, sizing calculations, stop-ratchet checks, slot-contention behaviour, frequency.
3a must NOT report: portfolio profit factor, winners by instrument, net P&L rankings, survivor lists. (Preserves the OOS windows as genuinely unseen.)
NO shortlist, NO instrument removal, NO winner promotion.
STOP. No 3b without explicit go; 3b runs the full frozen 14 on the untouched OOS period, never a 3a-derived shortlist.


---

## 13. OOS date algorithm (explicit, outcome-blind)
```
Common dataset end = latest date through which ALL 14 instruments have a
                     complete expected trading session.
OOS end           = last complete calendar month ending on or before
                     common dataset end.
OOS period        = the 12 complete calendar months ending at OOS end.
Windows           = four consecutive 3-calendar-month windows.
Development period = all data before OOS start.
```
Derived from the data audit. No returns or signal outcomes inspected during the audit. The resulting universe order, exchanges/currencies, four OOS date ranges, and warm-up confirmation are written into this document before Commit A.

## 14. Commit-B implementation definitions (frozen at implementation lock, not design changes)

**Mixed-market event ordering.** Instruments trade on different exchanges and do not open simultaneously. Process events by actual session-open timestamp in UTC:
```
For each exchange-session event chronologically in UTC:
  - execute exits for instruments opening at that timestamp
  - recalculate equity, cash, slots
  - process entries opening at that timestamp
Frozen universe order is the tie-break ONLY when multiple instruments
share the same event timestamp and compete for limited slots.
```
If the existing simulator assumes one universal daily open for all exchanges, that is a fidelity bug to document and fix before Commit B.

**Final open positions.** At the OOS endpoint, synthetically liquidate remaining positions at frozen adverse exit costs, labelled `END_OF_TEST_LIQUIDATION`. Keeps net P&L, PF, and trade counts internally consistent.

**Top-five removal.** From the ORIGINAL completed OOS run: rank closed trades by net P&L, remove the five largest net winners arithmetically. Do NOT rerun the portfolio (rerunning changes slot availability/sizing → a different counterfactual).

**Instrument concentration.** `instrument_contribution = positive net P&L for instrument ÷ sum of positive instrument net P&Ls` (bounded, interpretable even when some instruments lose).

**Profit factor.** `sum of positive net closed-trade P&L ÷ absolute sum of negative net closed-trade P&L`, including commission, spread, slippage, FX, and END_OF_TEST_LIQUIDATION exits.

**Trade-count expectation (retained).** A 12-month OOS may produce <100 trades. If so, the result is provisional by pre-registration. Do NOT extend the window, lower ADX, or change the threshold in response.

---

## Commit A — stale-data note

Dataset ends 2026-03-20; OOS covers through Feb 2026, not the present. If 3b passes, re-validation on fresh data is required before live consideration.
