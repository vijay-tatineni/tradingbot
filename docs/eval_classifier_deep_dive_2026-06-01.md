# Classifier deep-dive analysis

_Generated: 2026-06-01T13:17:08.592623+00:00_
_Cohort: today's backfill log rows (196 forward-evaluable samples). No new API calls._

## Section 1 — TRENDING vs UNCLEAR alone

Repeats Script 3's Cohen's d calculation but isolates TRENDING vs UNCLEAR — the narrow trade-vs-don't-trade comparison — rather than TRENDING vs (RANGING + UNCLEAR).

- n(TRENDING) = 13, n(UNCLEAR) = 128
- Mean directional move (ATR): TRENDING +0.233, UNCLEAR +0.340
- Mean |move| (ATR): TRENDING +2.000, UNCLEAR +2.419
- Cohen's d (directional, TRENDING − UNCLEAR): **-0.034**
- Cohen's d (|move|, TRENDING − UNCLEAR): **-0.206**

## Section 2 — Confidence calibration (high ≥ 0.75, low < 0.75)

For each regime label, do high-confidence calls precede larger forward moves than low-confidence calls of the same label? If confidence is informative, expect high − low > 0 within TRENDING and the absolute-move column to grow with confidence.

| Regime | n high | n low | High mean dir | Low mean dir | High |move| | Low |move| | Cohen's d (dir, high−low) |
|---|---|---|---|---|---|---|---|
| TRENDING | 9 | 4 | +0.337 | -0.002 | +2.502 | +0.870 | +0.130 |
| RANGING | 2 | 53 | +6.345 | +1.359 | +6.345 | +2.995 | +1.327 |
| UNCLEAR | 0 | 128 | — | +0.340 | — | +2.419 | _one bucket empty_ |

## Section 3 — Per-instrument breakdown

Per-regime forward returns shown only where n ≥ 3. Modal label + agreement come from today's consistency-run log rows. Flag = a per-instrument regime bucket whose mean |move| is ≥ 1.5× the cross-instrument aggregate for that regime.

Cross-instrument aggregate mean |move| per regime: TRENDING = +2.000, RANGING = +3.117, UNCLEAR = +2.419

| Instr | Modal | Agree | Counts T/R/U | TRENDING dir | RANGING dir | UNCLEAR dir | Flags |
|---|---|---|---|---|---|---|---|
| AAPL | TRENDING | 100% | 0/8/6 | — (n=0) | +2.496 (n=8) | +0.834 (n=6) |  |
| ANET | UNCLEAR | 100% | 2/8/4 | — (n=2) | +2.235 (n=8) | -1.393 (n=4) |  |
| ANTO | UNCLEAR | 100% | 0/0/14 | — (n=0) | — (n=0) | +0.438 (n=14) |  |
| AVGO | UNCLEAR | 100% | 3/8/3 | +1.736 (n=3) | +2.876 (n=8) | +1.172 (n=3) |  |
| BARC | RANGING | 100% | 0/3/11 | — (n=0) | -4.257 (n=3) | -0.326 (n=11) |  |
| MSFT | RANGING | 100% | 1/2/11 | — (n=1) | — (n=2) | +0.570 (n=11) |  |
| NBIS | UNCLEAR | 100% | 0/4/10 | — (n=0) | +1.777 (n=4) | +4.111 (n=10) | UNCLEAR: |move|=4.11 ATR vs aggregate 2.42 (n=10) |
| NVTS | UNCLEAR | 90% | 1/4/9 | — (n=1) | +7.219 (n=4) | +2.153 (n=9) | RANGING: |move|=7.22 ATR vs aggregate 3.12 (n=4) |
| PLTR | RANGING | 100% | 0/8/6 | — (n=0) | -0.738 (n=8) | -0.084 (n=6) |  |
| SCCO | UNCLEAR | 100% | 3/0/11 | +1.307 (n=3) | — (n=0) | -0.623 (n=11) |  |
| SGLN | UNCLEAR | 100% | 0/4/10 | — (n=0) | -2.109 (n=4) | -0.670 (n=10) |  |
| SSLN | UNCLEAR | 100% | 2/0/12 | — (n=2) | — (n=0) | -2.889 (n=12) |  |
| SU | RANGING | 100% | 1/6/7 | — (n=1) | +2.510 (n=6) | -0.467 (n=7) |  |
| TSM | UNCLEAR | 100% | 0/0/14 | — (n=0) | — (n=0) | +1.660 (n=14) |  |
