# Preliminary Full-Universe Honest Read — 2026-06-04

**Status:** Preliminary per-instrument read. **NOT** Phase 2.
No portfolio cap, no walk-forward, no OOS split. This is the baseline the
portfolio-loop (Phase 1b) results will be compared against.

**Method:** Each of the 14 active instruments run once, independently, on its
own real `instruments.json` config (timeframe, trail_stop_pct, take_profit_pct,
long_only, currency) through the committed honest simulator
(`backtest/simulator.py` via `run_simple_backtest`):

- **Fix 1** — next-bar-open entry + entry-bar-inclusive scan (no same-bar lookahead)
- **Fix 1b** — trailing stop checks prior-bar stop before ratcheting
- **Fix 2** — base commission ($0.005/share, $1 min/side, 1% cap) + half-spread/slippage + gap-through fills
- **Fix 3** — `target_notional = $1,000` position sizing

**Window:** all instruments ~2024-03 → 2026-03 (~2.0 yr); NVTS 2024-04 → 2026-04.
Bar-count differences are 4hr-vs-daily, not calendar span.

Reproduce: `PYTHONPATH=/root/trading python3 backtest/results/preliminary_universe_read.py`

## Results (sorted by after-cost profit factor ↓)

| Sym | TF | Cur | Stp% | TP% | Trades | Win% | Net P&L | PF | Commission | Cm/Tr | Floor% | Avg hold (bars) |
|-----|----|----|----|----|----|----|----|----|----|----|----|----|
| SGLN | 4hr | GBP | 5.0 | 10.0 | 464 | 78% | £27,068 | 9.51 | £928 | £2.00 | 100% | 70 |
| SSLN | daily | GBP | 5.0 | 4.0 | 138 | 67% | £1,800 | 1.93 | £276 | £2.00 | 100% | 5 |
| ANET | daily | USD | 5.0 | 15.0 | 59 | 42% | $817 | 1.58 | $118 | $2.00 | 100% | 4 |
| SCCO | daily | USD | 5.0 | 3.0 | 94 | 69% | $439 | 1.33 | $188 | $2.00 | 100% | 2 |
| PLTR | 4hr | USD | 5.0 | 20.0 | 340 | 36% | $2,252 | 1.28 | $680 | $2.00 | 100% | 9 |
| MSFT | 4hr | USD | 1.5 | 3.0 | 186 | 45% | $90 | 1.06 | $372 | $2.00 | 100% | 7 |
| BARC | 4hr | GBP | 5.0 | 4.0 | 470 | 54% | –£147 | 0.98 | £1,527 | £3.25 | 2% | 22 |
| TSM | 4hr | USD | 1.5 | 5.0 | 283 | 28% | –$495 | 0.83 | $566 | $2.00 | 100% | 2 |
| AAPL | 4hr | USD | 1.5 | 3.0 | 245 | 33% | –$396 | 0.78 | $490 | $2.00 | 100% | 6 |
| AVGO | 4hr | USD | 1.5 | 5.0 | 242 | 29% | –$697 | 0.73 | $484 | $2.00 | 100% | 2 |
| ANTO | 4hr | GBP | 1.0 | 15.0 | 312 | 28% | –£1,038 | 0.59 | £624 | £2.00 | 100% | 2 |
| SU | 4hr | EUR | 4.0 | 12.0 | 264 | 27% | –$2,559 | 0.54 | $528 | $2.00 | 100% | 18 |
| NBIS | daily | USD | 1.0 | 12.0 | 94 | 11% | –$602 | 0.47 | $188 | $2.00 | 100% | 0 |
| NVTS | 4hr | USD | 1.0 | 3.0 | 142 | 16% | –$855 | 0.44 | $320 | $2.25 | 70% | 0 |

Notes:
- **SU is EUR** (shown with `$` — cosmetic; the formatter only special-cases GBP)
  and maps to the "default" 3.0 bp cost class, not the US 1.5 bp class.
- P&L is in each instrument's own currency (£/$); **not** FX-converted or summed —
  cross-currency totals here would be apples-to-oranges. The portfolio loop will
  produce a single FX-converted equity curve.

## Four-question summary

1. **PF > 1.20 (the Phase 2 bar): 5** — SGLN, SSLN, ANET, SCCO, PLTR.
2. **Above 1.0 (marginally positive): 6** — those five plus MSFT (1.06). Only MSFT
   sits in the thin 1.00–1.20 band.
3. **Sub-breakeven like AAPL: 8** — BARC (0.98, essentially flat), TSM, AAPL, AVGO,
   ANTO, SU, NBIS, NVTS.
4. **Commission floor — dominant or instrument-specific?** Universal *mechanism*,
   decisive only for thin-edge names. The $1/side floor pins **100% of trades for
   13 of 14** (mean 91%) — every name pays a flat ~$2 round-trip toll.
   - Trivial for winners: commission/|net P&L| = 0.03 (SGLN), 0.30 (PLTR).
   - Decisive for marginals: **BARC, TSM, AAPL are gross-positive but net-negative
     purely on cost** (BARC +£1,380 gross → –£147; TSM +$71 → –$495; AAPL +$94 → –$396).
   - **BARC is the lone floor exception** (2% pinned, £3.25/trade): $1,000-notional
     sizing on a cheap, high-share-count name pushes per-share cost above the floor;
     turnover × per-share cost (470 trades) kills it, not the floor.

## Three-tier classification

- **Clearly good** (robust to the caveats below): SGLN, PLTR, SSLN, ANET, SCCO.
- **Clearly dead** regardless of cost (deep sub-1.0 *and* gross-negative):
  SU 0.54, ANTO 0.59, NBIS 0.47, NVTS 0.44. The portfolio loop will not rescue these.
- **Uncertain middle:** MSFT, BARC, TSM, AAPL — the names this read cannot settle,
  and exactly where a portfolio event loop (lower turnover) changes the inputs.

## Two caveats (why the middle tier is unresolvable here)

1. **This read is optimistic on P&L, pessimistic on commission for churny names.**
   With no portfolio cap, every signal opens an independent trade — SGLN takes 464
   trades with 70-bar avg holds over only 1,509 bars, so trades overlap massively and
   aggregate P&L is **not** deployable capital. But overlap cuts the other way on cost:
   the per-trade sim pays *more* total commission than a real portfolio loop (one
   position per instrument + cooldowns → far fewer trades) ever would. So the
   middle-tier commission bills (BARC £1,527, TSM $566, AAPL $490) are **inflated** —
   the portfolio loop's lower turnover is the one thing that could flip them.

2. **PF point estimates survive overlap; statistical weight does not.** PF is a
   per-trade ratio, so the values above are valid, but clustered signals are
   *correlated* — SGLN's 464 trades may be ~a dozen-odd independent trend episodes in
   costumes. Effective N ≪ trade count, so PF 9.51 is far softer evidence than N=464
   implies. Walk-forward / OOS (the real Phase 2) is still required before trusting any
   of these.

## Decision

**Build Phase 1b (the portfolio event loop).** Five names clear the bar, four are
dead, four are unresolvable without it — a legitimate go-signal, treated as
**resolving** the middle tier (realistic turnover/commission), **not** guaranteeing a
rescue (retained gross edge is unknown until the loop runs).
