# Phase 1b Portfolio Loop — Full-Universe Run — 2026-06-04

How much Layer-1 edge survives realistic portfolio trading. Single clock across
all 14 active instruments, **5-position cap**, **$1,000 notional**, one position
per instrument, honest mechanics (Fixes 1+1b+2+3) via the shared `OpenPosition`
stepper. Compared against the committed preliminary per-instrument read
(`preliminary_read_2026-06-04.md`).

NOT Phase 2: no walk-forward, no OOS split. A conservative **upper bound** on
turnover/commission (loss-limit breakers, max_entries_per_cycle, and reentry
cooldown are not modelled — each would only reduce entries further).

Reproduce: `PYTHONPATH=/root/trading python3 backtest/results/portfolio_loop_run.py`

## Accounting check: signal conservation

For every instrument, `taken + blocked_by_open + blocked_by_cap = preliminary
trade count` exactly (all 14 verified). Every signal is bucketed exactly once —
nothing lost or invented. (This is an *accounting* check. Logic correctness comes
from the byte-identical refactor verification and the skeleton's sane-sequence /
synthetic cap tests, not from conservation.)

## ⚠ Cap contamination — read before trusting any portfolio PF

The cap binds 29% of the time (below), so an instrument's portfolio PF is **not
intrinsic**: it omits the signals dropped while the portfolio was full, and *which*
signals get dropped is order-dependent. Contamination = `blk_cap / baseline` (% of
an instrument's signals never tested):

| high contamination | | low contamination | |
|----|----|----|----|
| NBIS | 57% | SSLN | 15% |
| NVTS | 48% | SU | 14% |
| ANTO | 33% | BARC | 12% |
| **SCCO** | **30%** | PLTR | 7% |
| AVGO | 24% | SGLN | 1% |
| **MSFT** | **21%** | | |
| ANET | 19% | | |

**The tier reshuffle at the 1.20 line is the shakiest conclusion in this run.**
SCCO (fell out, 1.33→1.02) and MSFT (climbed in, 1.06→1.21) are the two most
cap-contaminated names in the top group — a third of SCCO's and a fifth of MSFT's
signals were never tested. PLTR (7%) and SGLN (1%) PFs are trustworthy; the
boundary cases are not.

## Results (sorted by portfolio PF ↓; * = preliminary "winner")

| Sym | Cur | Prelim PF | Port PF | Prelim Trd | Port Trd | blk_open | blk_cap | Win% | Net P&L | Comm |
|-----|----|----|----|----|----|----|----|----|----|----|
| *SGLN | GBP | 9.51 | **2.05** | 464 | 16 | 444 | 4 | 50% | £379 | £32 |
| *ANET | USD | 1.58 | **1.87** | 59 | 11 | 37 | 11 | 36% | $262 | $22 |
| *SSLN | GBP | 1.93 | **1.84** | 138 | 32 | 85 | 21 | 66% | £401 | £64 |
| *PLTR | USD | 1.28 | **1.58** | 340 | 39 | 278 | 23 | 44% | $478 | $78 |
| MSFT | USD | 1.06 | **1.21** | 186 | 24 | 123 | 39 | 50% | $40 | $48 |
| *SCCO | USD | 1.33 | **1.02** | 94 | 35 | 31 | 28 | 66% | $13 | $70 |
| AAPL | USD | 0.78 | 0.94 | 245 | 36 | 169 | 40 | 39% | –$17 | $72 |
| BARC | GBP | 0.98 | 0.94 | 470 | 36 | 376 | 58 | 53% | –£45 | £117 |
| AVGO | USD | 0.73 | 0.86 | 242 | 79 | 105 | 58 | 27% | –$136 | $158 |
| ANTO | GBP | 0.59 | 0.79 | 312 | 75 | 135 | 102 | 31% | –£139 | £150 |
| SU | EUR | 0.54 | 0.67 | 264 | 20 | 207 | 37 | 25% | –$136 | $40 |
| TSM | USD | 0.83 | 0.63 | 283 | 77 | 160 | 46 | 22% | –$285 | $154 |
| NBIS | USD | 0.47 | 0.58 | 94 | 37 | 3 | 54 | 11% | –$166 | $74 |
| NVTS | USD | 0.44 | 0.28 | 142 | 65 | 9 | 68 | 14% | –$540 | $144 |

## Four-tier summary (after collapse)

- **PF ≥ 1.20 after collapse: 5** — but trust-tier them by sample size + cap
  contamination rather than reading "5 survive" as 5 equal results:
  - **PLTR 1.58 (39 trd, 7% capped)** — most trustworthy survivor.
  - **SSLN 1.84 (32, 15%)** — solid.
  - **SGLN 2.05 (16, 1%)** — clean PF but thin sample.
  - **ANET 1.87 (11, 19%)** — thin *and* contaminated.
  - **MSFT 1.21 (24, 21%, barely over)** — shakiest; a *positive signal*, not a
    confirmed survivor (thin, contaminated, just over the line).
  Membership shifted vs the preliminary 5 (MSFT in, SCCO out) — but per the cap
  caveat above, that boundary swap is the least reliable part of the table.
- **Marginal 1.00–1.20: 1** — SCCO 1.02.
- **Sub-breakeven < 1.00: 8** — AAPL 0.94, BARC 0.94, AVGO 0.86, ANTO 0.79,
  SU 0.67, TSM 0.63, NBIS 0.58, NVTS 0.28.
- **⚠ Thin sample (<30 portfolio trades — Phase 2 OOS risk): 4** — ANET 11,
  SGLN 16, SU 20, MSFT 24. The two highest-PF survivors (SGLN, ANET) have the
  thinnest samples; walk-forward OOS splits will have very few trades each.

## Middle-tier resolution (the question Phase 1b existed to answer)

- **MSFT 1.06 → 1.21**: crosses 1.20 — a *positive signal*, not proof. The
  upper-bound argument cleanly bounds turnover/commission (live trades less, pays
  less) but does **not** cleanly bound PF, and MSFT is thin (24 trades) and 21%
  cap-contaminated. Treat as a candidate, not a confirmed survivor.
- **AAPL 0.78 → 0.94**, **BARC 0.98 → 0.94**: improved (BARC's commission collapsed
  £1,527 → £117) but still sub-1.0. Genuinely unresolved; reentry cooldown is the
  named next lever.
- **TSM 0.83 → 0.63**: got worse under realistic turnover — the surviving trades
  have a worse profit/loss ratio. Not a candidate.

## Winner shrinkage

- SGLN 9.51 → 2.05 (464 → 16 trades) — ~77% of apparent edge was overlap inflation;
  a strong real edge remains.
- SSLN 1.93 → 1.84, ANET 1.58 → **1.87** (rose), PLTR 1.28 → **1.58** (rose),
  SCCO 1.33 → **1.02** (fell out).
- Several PFs *rose* under the loop: dropping the mid-trend re-entries (kept by the
  overlapping baseline) and the commission relief from lower turnover both help
  trend-followers. The collapse is not uniformly downward.

## Portfolio-level (all 14, no instrument selection)

- Total trades across universe: **582** (vs ~3,473 baseline independent trades).
- Combined P&L (USD, static FX GBP 1.27/EUR 1.08): **+$259.50** over ~2 yr.
- Deployable capital (5 slots × $1,000): $5,000 → **return on deployable capital ≈ 5.2%** over ~2yr (~2.6%/yr).
- **Realized-trade** drawdown (P&L booked at exits only, exit-timestamp-ordered):
  **$756 (15.1% of deployable)**. NOT mark-to-market — with holds up to 200 bars,
  true intra-trade MTM drawdown is materially larger.
- Peak concurrent: 5/5.

### Does the cap bind, or does one-per-instrument do all the work?

- **blocked_by_open = 2,162** vs **blocked_by_cap = 589.** One-per-instrument is
  the dominant turnover reducer, but the **cap is NOT negligible** — it rejected
  589 entries.
- The portfolio sat **at the 5-cap 29.3% of the time** (893 of 3,050 timestamps):

  | positions held | % of timestamps |
  |----|----|
  | 0 | 17.0% |
  | 1 | 12.0% |
  | 2 | 14.5% |
  | 3 | 11.7% |
  | 4 | 15.5% |
  | **5 (at cap)** | **29.3%** |

- **Implication:** binding is *bursty*, not saturating — the portfolio holds 0–1
  positions 29% of the time too (avg ≈2.8/5), so the cap saturates only during
  broad-trend hot spells when many names signal at once. With all 14 enabled, the
  8 sub-breakeven names compete for slots and displace winner entries during those
  spells. The +$259 combined P&L is the *unselected* portfolio; there IS room to
  add instruments (they'd just face contention in hot periods), and a survivors-only
  portfolio would largely de-contaminate the per-instrument PFs (cap rarely binds
  with 5–6 names). Tighter selection, not a bigger cap, is the lever.

## Decision-relevant next step (not yet run)

The all-14 P&L is the *unselected* portfolio with 8 known-dead names hogging slots.
The run that answers the go/no-go is **survivors-only through the loop** — which
also removes the cap-contamination confound (5–6 instruments → cap rarely binds →
clean per-instrument PFs + the real deployable-capital P&L). Deferred pending
interpretation of this table.
