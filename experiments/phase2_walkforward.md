# Phase 2 — Nested Portfolio-Level Walk-Forward (PRE-REGISTRATION)

**Status: FROZEN DESIGN, pending one confirmation. Pre-registration only — no
walk-forward code exists yet and no OOS result has been seen.** This document is
committed *before* the test is built so selection bias cannot enter through how the
test is constructed. Once the first OOS result is produced, any change to anything
below is logged as a dated **deviation** with rationale in the "Deviation log"
section — the original text is never edited away.

> **⚠ ONE OPEN DECISION — confirm before any code runs (see §2 Stage 1).**
> Everything in this file is frozen *except* how parameters are handled during
> selection. The registered default is **freeze the live per-instrument params and
> walk-forward the instrument *selection* only** (zero parameter degrees of
> freedom). The alternatives are a minimal per-instrument grid or a single global
> set. This is the one genuinely user-facing selection-logic fork; it is flagged
> for sign-off rather than silently locked. Finalizing it now is still valid
> pre-registration — it happens before any code, any window, any result.

Branch: backtest-honesty · bot/ and main.py untouched · this is analysis-harness
design, not a live-trading change.

## Why this exists

Phase 1b (`backtest/results/survivors_loop_2026-06-05.md`) produced clean
focused-portfolio PFs but flagged them as **in-sample selection, not evidence the
edge is real**: we picked the survivors after seeing results. Phase 2 is the
out-of-sample test that the regime-filter experiment
(`experiments/regime_filter_2026-06-01.md`) already names as its promotion
criterion #1 — *"Layer 1 has positive after-cost expectancy (proven in Phase 2)."*
If Phase 2 fails, the regime layer is moot; if it passes only thinly, that bounds
every downstream claim.

The whole validity of Phase 2 rests on the design being fixed before results are
seen. That is the point of this file.

---

## 1. Data span and window structure (FROZEN)

**Available history:** ~2024-03 → 2026-03 (~24 calendar months); NVTS offset
2024-04 → 2026-04. Bar counts differ by timeframe (4hr vs daily) but the
**walk-forward grid is defined on the calendar, not on bar index**, so all
instruments share one window clock. T0 = the first calendar month for which every
*candidate-eligible* instrument has data (see §4 eligibility).

**Registered structure: 12-month train / 3-month test, rolling by 3 months.**

| Window | Train (in-sample) | Test (OOS) |
|--------|-------------------|------------|
| W1 | T0 … T0+12mo | T0+12 … T0+15 |
| W2 | T0+3 … T0+15 | T0+15 … T0+18 |
| W3 | T0+6 … T0+18 | T0+18 … T0+21 |
| W4 | T0+9 … T0+21 | T0+21 … T0+24 |

→ **4 OOS test windows**, each 3 months, tiling the *second* 12 months of history
(months 12–24). Train windows overlap (rolling), test windows are disjoint and
non-overlapping — every OOS trade is counted in exactly one window. The 4 windows
map one-to-one onto the "report by quarter" decision criterion.

### Honest finding registered up front: 4 windows is near the viability floor

12-month train is the conservative choice — it gives the most stable in-sample
selection — but over only 24 months it yields just **4 OOS windows and ~12 months
of OOS coverage** (the first 12 months are consumed as train-only and never
tested). This is *few*. We register two consequences before seeing anything:

1. **We will not shorten the train window to manufacture more OOS windows.**
   6mo-train/3mo-test would give 6 windows, but trades selection stability for
   sample count — a form of design-gaming the pre-registration exists to prevent.
   12/3 is frozen. If the result is thin, "history is insufficient" is a
   legitimate **finding**, not a problem to engineer around.
2. **Insufficient history is a registered possible outcome.** See the decision
   tree §6: a positive-but-thin result is explicitly classified *promising, not
   scalable — extend history before claiming edge.*

### Power analysis (design-time, from Phase 1b aggregate rates — NOT an OOS result)

Phase 1b's focused portfolio (5–6 names) booked ~139–182 trades over ~24 months ≈
**6–7.6 portfolio trades/month**. The OOS region here is ~12 months, so *if* a
comparably-sized universe is selected each window, expect on the order of
**~70–100 pooled OOS trades** — straddling the 100-trade preferred threshold from
below. This is a planning estimate from known in-sample rates (the per-window
selection varies, so the true count could land either side); it is **not** a
result. Registered expectation: **the pooled OOS sample will likely be under 100,
so even a positive result is provisional by our own §6 criteria.**

---

## 2. Selection inside each training window — TRAINING DATA ONLY (FROZEN)

Everything in this section uses **only** the train sub-window's bars. Test-window
bars are never touched during selection. Two stages.

### Stage 1 — parameters (⚠ OPEN DECISION — registered default = freeze live params)

**Timeframe is a data property, not a tunable** — it stays per-instrument as
configured in instruments.json, in every option below.

**Why a parameter *search* is dangerous here, and why the default avoids it.**
With only 4 OOS windows and ~70–100 expected pooled OOS trades (§1), every added
degree of freedom inflates in-sample overfitting on the already-thin 12-month
training windows. A naive "median PF across all 14 instruments ≥ bar" gate is also
**unsatisfiable**: on the known prelim PFs the 14-name median is ≈ 0.90 (only 5 of
14 clear 1.10), because most of the 14 are junk that Stage 2 exists to discard —
so scoring parameters by "good across all 14" selects nothing. And a *single
global* (stop, TP) silently **homogenizes** the strategy: the live configs span
TP = 3 (MSFT) to 20 (PLTR) and stop 1.0–5.0; one global TP would test a *different*
parameterization than Phase 1b did, breaking comparability with the in-sample PFs
that motivated Phase 2 in the first place.

**Registered default — Option A: no parameter search.** Freeze each instrument's
**live instruments.json (stop, TP, timeframe)** and walk-forward the *instrument
selection* only. This is the most conservative reading of "favour stable regions,
do not select by max PF" (there is nothing to overfit), it keeps the OOS run
directly comparable to Phase 1b (same params), and it minimizes degrees of freedom
— the right call on thin data. The walk-forward then tests the one thing Phase 1b
did in-sample: **does selecting instruments on a training window hold up out of
sample.**

**Alternatives considered (require explicit sign-off to adopt instead):**

- **Option B — minimal per-instrument grid.** Each name picks its own (stop, TP)
  from a small grid that *covers its live value*, chosen by a stability plateau
  (maximin over grid neighbours, NOT argmax PF); a name with no stable region that
  window is dropped. Adds DoF but stays per-instrument so it doesn't homogenize.
  Adopt only if we explicitly want to test parameter robustness too, accepting the
  overfitting cost on thin windows.
- **Option C — single global (stop, TP).** Rejected as the default for the
  homogenization/comparability/satisfiability reasons above; documented only so the
  rejection is on record.

The literal Phase 2 brief said "select instruments *and parameters*," so Option A
is a deliberate, flagged departure — surfaced for confirmation, not assumed.

### Stage 2 — instrument inclusion (threshold, not ranking)

At the frozen Stage-1 parameters, include each of the 14 instruments that, on the
**train window**, meets BOTH:

- **after-cost PF ≥ 1.20** (the same bar used throughout the project), and
- **≥ 20 train-window trades** (minimum-sample gate — excludes names too thin to
  have a meaningful in-sample read; in short/early windows this will exclude the
  thin daily names, by design).

Inclusion is a **threshold bar applied to every name independently** — NOT a
top-k ranking and NOT "the 5 highest PF." Any number of names may pass. **If no
name clears the bar in a window, that window selects nothing and contributes zero
OOS trades** — a faithful, informative outcome.

**Trade-count basis (important):** the ≥ 20-trade gate is measured on the
**independent** per-instrument training run (no cap, no one-per-instrument — the
churny count, like the prelim read), whereas the OOS *result* counts
**portfolio-loop** trades (capped, one-per-instrument — far sparser, like Phase
1b). So a name can clear the 20-trade in-sample gate yet contribute only ~3 OOS
trades to the pooled result. This is expected, not a bug, and is the mechanical
reason §1's pooled-OOS power estimate is low.

The live **5-cap + one-position-per-instrument + instruments.json tie-break order**
arbitrates contention during the test window exactly as in Phase 1b and live
(`bot/layer1.py`). We do not pre-trim to 5 at selection; the cap is part of the
honest OOS mechanics. (If many names pass and the cap binds in-test, that
re-introduces cap contention into the OOS number — faithful to live, so we **keep**
it and **report per-window cap-saturation** so its effect is visible.)

### Freeze step (audit trail)

For each window, the chosen `(stop, TP, instrument list)` is written to a
committed artifact **before** the test window is run, so the frozen selection is
inspectable and cannot be retro-edited. The selected universe is **allowed to
change window-to-window** — that is faithful to what live research would do
(re-select on each new training window). No reselection occurs within a window.

---

## 3. Testing in the following window — TEST DATA ONLY (FROZEN)

For each window, run the **portfolio event loop** (`backtest/portfolio_sim.py`:
5-cap, one-per-instrument, honest Fix 1+1b+2+3 costs/sizing, instruments.json
order) over the **test sub-window only**, on **only the frozen Stage-2 selection**
at the frozen Stage-1 parameters. Positions are opened and closed within the test
window (a position open at the test-window boundary is marked out at the last
test bar, same as Phase 1b end-of-data handling). **Record every OOS trade.** No
reselection, no parameter change, no peeking at later windows.

---

## 4. Aggregation (FROZEN)

- **Pool ONLY the genuinely-OOS test-window trades** across W1–W4. In-sample
  training trades **never** count toward the result.
- Eligibility per window: an instrument is a candidate in a window only if it has
  full data coverage across **both** that window's train and test sub-windows
  (NVTS offset handled here; no partial-coverage instruments).
- Report, in addition to the pooled headline: per-window (= per-quarter) PF and
  net, per-instrument contribution, and per-test-window cap-saturation %.

---

## 5. Decision criteria — portfolio level, PRE-REGISTERED, FROZEN

All thresholds below are fixed now and not tunable after results.

1. **Sample size:** ≥ 100 pooled OOS trades preferred. **< 100 → result is
   provisional even if positive** (and per §1 power analysis, < 100 is the likely
   case).
2. **OOS profit factor > 1.15–1.20 after base costs** (pooled, all test windows).
   Below 1.15 is not a pass; 1.15–1.20 is a marginal pass read together with the
   robustness checks; the headline bar is 1.20.
3. **Positive across multiple time periods:** report PF and net by quarter
   (the 4 test windows). A result driven by one quarter with the others flat or
   negative is not "robustly positive."
4. **No single instrument carries the result:** remove the single largest
   net-P&L instrument from the pool; if pooled PF drops below 1.0 (or net goes
   non-positive), **flag concentration** — the edge is one name, not a portfolio.
5. **Cost stress:** recompute pooled OOS PF at **base / 1.5× / 2×** slippage+spread
   (commission held at the real IBKR schedule — it is not a stress knob). Uses the
   existing preset machinery (`CostConfig`, multipliers base=1.0, pessimistic=2.0
   already exist; **1.5× is a new intermediate multiplier to be added when code is
   built — registered here as the value 1.5 on half_spread_bps + slippage_bps**).
   A pass should remain > 1.0 (ideally > 1.15) at 2×.
6. **Fragility — top-5 removal:** remove the 5 most profitable OOS trades from the
   pool; if the remaining pooled P&L **vanishes** (PF ≤ 1.0 or net ≤ 0),
   **flag low confidence** — the result is a handful of lucky trades.
7. **Confidence intervals — by episode / block bootstrap.** Trend trades cluster,
   so naive per-trade resampling overstates precision; correlated trades are
   treated as ~one episode. Registered:
   - **Episode definition:** a maximal cluster of OOS trades whose holding periods
     overlap in calendar time at the portfolio level (concurrent or back-to-back
     within the same instrument's trend) collapses to one episode for resampling.
   - **Primary:** block bootstrap resampling **whole episodes** (with replacement)
     to build a CI on pooled OOS PF.
   - **Secondary:** block bootstrap over the 4 test-window blocks.
   - **Registered caveat:** with only 4 OOS windows and clustered trades, these CIs
     will be **wide**; a CI comfortably excluding 1.0 is required to call the result
     "robust," and we expect this to be hard to achieve on 24 months of history.

---

## 6. Decision tree (FROZEN)

Read after all of §5 is computed:

- **Clearly negative** (pooled OOS PF < ~1.0, or fails base-cost): → **STOP.**
  Layer 1 has no out-of-sample edge; this also triggers abandonment criterion #1
  of the regime experiment (kills the strategy, not just the filter).
- **Flat / ambiguous** (PF ~1.0–1.15, or positive but fails most robustness
  checks): → **PAUSE, reconsider scope.** Do not proceed to paper validation.
- **Positive but thin** (PF > 1.15–1.20 **but** < 100 OOS trades OR fragile to
  top-5 removal OR concentrated in one name OR CI includes 1.0): → **PROMISING,
  NOT SCALABLE.** Do not claim a real edge. **Extend history first** (more data →
  more OOS windows) before any capital or paper-trading claim.
- **Positive and robust** (PF > 1.20 base, ≥ 100 OOS trades, positive across
  quarters, survives 2× cost, survives top-5 removal, no single-name dependence,
  CI excludes 1.0): → **CONTINUE to paper validation**, then evaluate the regime
  filter (hands off to `regime_filter_2026-06-01.md` promotion criteria).

---

## 7. Pre-registration integrity

- This file is frozen at its committing commit on `backtest-honesty`. The freeze
  precedes any walk-forward code.
- Before the first OOS run, the (yet-to-be-written) walk-forward implementation
  must be diff-reviewed against this document; the per-window frozen selections
  (§2 freeze step) are committed before their test windows run.
- Any post-freeze change to window structure, selection logic, thresholds, or
  decision tree is recorded below with date and reason — never by silently editing
  the text above.

### Deviation log

_(none — design frozen at pre-registration)_
