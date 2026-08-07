# Phase 1b Survivors-Only Portfolio Loop — 2026-06-05

The decision-relevant follow-up named at the end of the full-universe run
(`portfolio_loop_2026-06-04.md`): run **only the selected survivors** through the
same single-clock loop. Two purposes:

1. **Remove the cap-contamination confound.** With 5–6 instruments and a 5-cap,
   the cap almost never binds, so each name's portfolio PF stops omitting
   order-dependent capped-out signals.
2. **Read the real focused-portfolio return** on deployable capital, with the 8
   known-dead names no longer hogging slots and losing money.

Same honest mechanics as before (Fixes 1+1b+2+3 via the shared `OpenPosition`
stepper), same 5-cap, $1,000 notional, one-per-instrument, instruments.json
tie-break order. Run two ways so the borderline SCCO call doesn't hinge on an
arbitrary 1.20 cutoff:

- **Set A** (strict, full-14 PF ≥ 1.20): SGLN, SSLN, ANET, PLTR, MSFT
- **Set B** (strict + SCCO): the above + SCCO

Reproduce: `PYTHONPATH=/root/trading python3 backtest/results/survivors_loop_run.py`

---

## ⚠ READ FIRST — this is a selected-survivor run (in-sample selection)

We chose these 5–6 names **after seeing the full-universe results**. That is
textbook in-sample selection: we picked the winners and are now measuring the
winners. **The clean PFs below are NOT out-of-sample evidence and do NOT show the
edge is real.** They answer one question only:

> *"What is the realistic return of the focused portfolio we'd actually deploy?"*

They do **not** answer *"is the edge real?"* That question is settled only by
**Phase 2 walk-forward** on these survivors — train on past windows, test on
unseen future windows, and check whether the selection holds up out-of-sample.
And as §"Sample-size problem" below shows, every survivor is so thin that even
Phase 2 may not be able to answer it per-instrument. Treat everything here as the
*ceiling* a future OOS test has to live up to, not as a result.

A second, weaker selection sits underneath this one: the full-14 PFs we selected
*on* were themselves cap-contaminated. So we selected on a noisy signal, then
re-measured — and (see ANET, MSFT) two of the five don't survive their own
re-measurement.

---

## Harness consistency check (free validation)

This script recomputes the full-14 portfolio live to get each name's contaminated
PF, rather than hardcoding yesterday's table. The live recomputation reproduces
the committed `portfolio_loop_2026-06-04.md` **exactly** — portfolio PFs
(2.05 / 1.84 / 1.58 / 1.87 / 1.21 / 1.02) and `blocked_by_cap` (4 / 21 / 23 / 11 /
39 / 28) both match to the digit. The loop is deterministic and the comparison
below is self-consistent, not cross-run drift.

---

## Set A — per-instrument: focused PF vs contaminated full-14 PF

Sorted by focused (survivors-only) PF. `Full14 PF` = same name's PF inside the
all-14 loop; `Full14 blkCap` = entries the cap dropped for it there.

| Sym | Cur | Prelim PF | Full14 PF | **Focused PF** | Prelim Trd | Full14 Trd | **Focused Trd** | Focused blkCap | Full14 blkCap | Win% | Net P&L | Comm |
|-----|----|----|----|----|----|----|----|----|----|----|----|----|
| SGLN | GBP | 9.51 | 2.05 | **2.18** | 464 | 16 | 16 | 0 | 4 | 56% | £399 | £32 |
| SSLN | GBP | 1.93 | 1.84 | **1.86** | 138 | 32 | 35 | 0 | 21 | 66% | £438 | £70 |
| PLTR | USD | 1.28 | 1.58 | **1.43** | 340 | 39 | 42 | 0 | 23 | 40% | $380 | $84 |
| ANET | USD | 1.58 | 1.87 | **1.17** | 59 | 11 | 15 | 0 | 11 | 27% | $80 | $30 |
| MSFT | USD | 1.06 | 1.21 | **1.00** | 186 | 24 | 31 | 0 | 39 | 42% | –$1 | $62 |

**The cap was flattering the marginal names, not hiding their edge.** The naive
expectation — "de-contaminate and the PFs firm up" — is wrong for half the set.
When the cap stops binding, each name's trade list is **recomposed** (not merely
augmented): entering an earlier, previously-capped signal re-sequences every
downstream entry and exit, so the focused list is a *different* set of trades, not
full-14's set plus restored losers. For ANET / PLTR / MSFT that recomposed list
nets a lower PF (**1.87→1.17, 1.58→1.43, 1.21→1.00**). Conservation holds on every
row (`taken + blk_open + blk_cap = prelim`), so nothing is invented — these are
real signals re-sequenced by the relaxed cap.

The recomposition is visible even where the *count* is unchanged: SGLN takes
**16 trades in both Set A and Set B**, yet PF moves 2.18→2.12 and win% 56→50 (one
win became a loss). The only difference between the sets is adding SCCO, which
shifted slot contention enough to move 2 SGLN signals from `blocked_by_open` to
`blocked_by_cap` — so SGLN took a *different* 16 trades. Same count, different
list. That is why the focused PF can't be predicted from the full-14 PF plus a
"restored trades" correction; the whole sequence reshuffles.

Read the deltas by sample size, because most of these moves are within noise:

- **PLTR (42 trd) 1.58 → 1.43** and **MSFT (31 trd) 1.00**: enough trades that the
  direction is meaningful. MSFT lands at *exactly breakeven* (net –$1) — **in the
  focused portfolio MSFT is not a survivor; it's dead flat.** Its full-14 1.21,
  the basis for the prior writeup's "MSFT climbs in" reshuffle, was cap-shielding.
- **SGLN (16 trd) 2.05 → 2.18** and **ANET (15 trd) 1.87 → 1.17**: tiny-sample
  noise. Don't narrate these as measured cap-distortion. The honest statement for
  ANET is *"1.87 was an 11-trade fluke; 15 trades says ~1.17; both samples are too
  small to mean anything"* — not "ANET got worse." Either way **ANET no longer
  clears the 1.20 bar it was selected on.**

So of the 5 strict survivors, the focused run leaves **3 above 1.20 (SGLN, SSLN,
PLTR)** and pushes **2 below (ANET 1.17, MSFT 1.00)**. Two of the five don't
survive their own de-contaminated re-measurement — the single most important line
in this run, and a direct consequence of having selected on a contaminated table.

### Did `blocked_by_cap` drop to ~zero? (yes — but read it precisely)

- **Set A `blocked_by_cap = 0`.** This is *structural, not empirical*: 5 names
  into a 5-cap means a 6th position can never exist to be blocked. Don't cite it
  as "confirmed de-contamination."
- The **empirical** de-contamination evidence is **cap-saturation: 29.3% → 1.9%**
  (timestamps sitting at 5/5 fell from 893/3,050 to 58/2,987). The cap now binds
  in brief bursts only.
- The genuinely empirical zero is **Set B** (6 names, so a 6th *can* be blocked):
  `blocked_by_cap = 5` across the whole 2-year run — negligible. Confirmed.

---

## Set B — adding SCCO (the borderline call)

| Sym | Cur | Prelim PF | Full14 PF | **Focused PF** | Focused Trd | Focused blkCap | Win% | Net P&L | Comm |
|-----|----|----|----|----|----|----|----|----|----|
| SGLN | GBP | 9.51 | 2.05 | **2.12** | 16 | 2 | 50% | £388 | £32 |
| SSLN | GBP | 1.93 | 1.84 | **1.86** | 35 | 1 | 66% | £439 | £70 |
| PLTR | USD | 1.28 | 1.58 | **1.43** | 42 | 0 | 40% | $380 | $84 |
| SCCO | USD | 1.33 | 1.02 | **1.16** | 43 | 0 | 67% | $107 | $86 |
| ANET | USD | 1.58 | 1.87 | **1.17** | 15 | 0 | 27% | $80 | $30 |
| MSFT | USD | 1.06 | 1.21 | **1.00** | 31 | 2 | 42% | –$0 | $62 |

**The two-set design answers its own question, and the answer reverses the prior
reshuffle.** SCCO was the one name the cap *hurt* in full-14 (1.33 prelim → 1.02
contaminated → **1.16 focused**): the cap had been blocking its winners, not
shielding losers. In the focused run **SCCO (1.16) > MSFT (1.00)** — the exact
reverse of the full-14 "MSFT in, SCCO out" tier swap the prior writeup already
flagged as its shakiest conclusion. SCCO is also the *best-sampled* of the
marginal names (43 trades).

And the borderline call **doesn't hinge on an arbitrary cutoff because adding SCCO
barely moves the portfolio**: +$94 combined P&L for +$143 of drawdown, `blk_cap`
0→5. Whether SCCO is "in" or "out" changes the deployable-capital return by
~2 points and the drawdown by ~3 points and nothing else. That immateriality —
not a 1.16-vs-1.20 line call — is the clean resolution.

---

## Portfolio-level

| | full-14 (unselected) | **Set A** | **Set B (+SCCO)** |
|---|---|---|---|
| Total trades | 582 | **139** | **182** |
| Combined P&L (USD, static FX) | +$259.50 | **+$1,522** | **+$1,616** |
| Return on $5,000 deployable | 5.2% | **30.4%** | **32.3%** |
| (per year, ~2yr) | 2.6%/yr | 15.2%/yr | 16.2%/yr |
| Realized-trade max drawdown | $756 (15.1%) | **$439 (8.8%)** | **$582 (11.6%)** |
| Cap-saturation (% at 5/5) | 29.3% | **1.9%** | **2.4%** |
| Avg concurrency | ~2.8/5 | **1.59/5** | **1.70/5** |
| Peak concurrent | 5/5 | 5/5 | 5/5 |

**Lead with the dollars, not the percent: +$1,522 (Set A) / +$1,616 (Set B) over
~2 years.** The 30% "return on deployable capital" is the most over-claimable
number in this run, for two reasons:

1. **Low utilization.** Avg concurrency is 1.59/5 — only ~32% of the $5,000 is
   working at any time; the rest is *burst reserve* you must hold because the cap
   *does* still hit 5/5 occasionally. You cannot size deployable down to the ~$1,600
   that does the work without losing capacity for the hot-period bursts. So 30% on
   $5k is the honest committed-capital figure; the dollars are the durable fact.
2. **The 5.2% → 30% jump is not "the capital works harder."** Utilization actually
   *fell* (2.8 → 1.6 avg). The gain comes entirely from **removing the 8 dead names
   that were filling idle slots and losing ~$1,260 between them**, not from any
   improvement in capital efficiency. Reframe: same strategy, fewer self-inflicted
   losses — not a newly-discovered 15%/yr deployment.

Drawdown caveat unchanged from the full run: **realized-trade** drawdown (P&L
booked at exits only, exit-timestamp-ordered) — **not** mark-to-market. With holds
up to ~200 bars, true intra-trade MTM drawdown is materially larger than $439/$582.

---

## The sample-size problem (foregrounded — this gates Phase 2 viability)

Phase 2 is a walk-forward: ~6-month train / 3-month test, rolling over ~2 years ≈
**5–6 out-of-sample windows**. The question is whether each survivor produces
enough OOS trades to compute a *per-window* PF that means anything. It does not.

Two projections bracket the per-window OOS trade count (neither is precise; the
truth is in between, and is worse than both — see clustering note):

| Sym | Focused Trd | per-window (total/5)* | per-window (×0.75 ÷ 6)** | Verdict |
|-----|----|----|----|----|
| ANET | 15 | 3.0 | 1.9 | ❌ no credible per-window PF |
| SGLN | 16 | 3.2 | 2.0 | ❌ no credible per-window PF |
| MSFT | 31 | 6.2 | 3.9 | ❌ too thin |
| SSLN | 35 | 7.0 | 4.4 | ❌ too thin |
| PLTR | 42 | 8.4 | 5.2 | ⚠ marginal |
| SCCO | 43 | 8.6 | 5.4 | ⚠ marginal |

\* optimistic: every trade lands in some test window, 5 windows.
\** train-discounted: first 6mo is train-only (~75% of the span is ever OOS),
6 windows.

**No survivor clears a credible per-window sample.** The two best PFs (SGLN 2.18,
ANET 1.17) come from the *thinnest* samples (16, 15 trades → 2–3 OOS trades per
window). Even the best-sampled names (PLTR, SCCO at ~42–43) give only ~5–8 OOS
trades per window — a single trade swings a per-window PF by tenths. And the
projections **overstate** coverage: these are trend-following entries that
**cluster in trends**, so the OOS trades won't spread evenly — some windows will
hold 0–1 trades and others a burst, making the worst windows uncomputable
entirely. A per-instrument, per-window PF is not a measurable quantity on this
data.

**Implication for Phase 2.** A naive per-instrument walk-forward will return
noise dressed as a PF curve. To get a real OOS read, Phase 2 has to change shape:
pool OOS trades across windows (one aggregate OOS PF per name, not 6), or test at
the **portfolio** level (one combined OOS equity curve — ~140–180 trades is
workable where 15 per name is not), or extend history. Reporting six per-window
PFs per name would be false precision. **Flag this before building Phase 2**, not
after.

---

## Bottom line

- **Realistic focused-portfolio return:** **+$1,522 / +$1,616 over ~2yr** (Set A /
  Set B), ~30–32% on $5k committed but only ~32% utilized; the win is removing
  ~$1,260 of dead-name losses, not capital efficiency. Drawdown $439 / $582
  realized-trade (true MTM larger). This is the *selected-survivor ceiling*, **not
  OOS evidence the edge is real.**
- **The selection partly dissolves on its own de-contaminated re-measurement:**
  of the 5 strict survivors, only **PLTR (1.43, 42 trd)** and **SSLN (1.86, 35
  trd)** are both ≥1.20 and reasonably sampled. **SGLN (2.18)** has a great PF on a
  thin 16-trade sample. **ANET (1.17)** and **MSFT (1.00)** fall below 1.20 and are
  **unconfirmed** — ANET on noise, MSFT to dead-flat.
- **SCCO call resolved:** SCCO 1.16 > MSFT 1.00 in the focused run (reversing the
  full-14 reshuffle), and including it is immaterial to the portfolio (+$94 P&L,
  +$143 DD). Best-sampled of the marginals; keep it as a marginal candidate, drop
  MSFT's claim to be "in."
- **Phase 2 viability is the binding constraint, not PF.** Every survivor is too
  thin for a per-instrument per-window walk-forward. Phase 2 must pool trades or go
  portfolio-level, or it will report noise. Decide that before building it.
