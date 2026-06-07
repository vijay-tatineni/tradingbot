# Breakout Strategy — Phase 3b Result Archive

This document archives the outcome of the Phase 3b four-window chronological
out-of-sample (OOS) portfolio evaluation of the frozen breakout strategy
(v3.2). It records the verdict, the economics, the exit-path finding, and the
integrity/commit chain exactly and transparently. It is a research archive: it
does **not** authorize Phase 3c, parameter tuning, instrument
selection/removal, live activation, or real-money deployment.

---

## Verdict

```text
Status: PROVISIONAL
Phase 3c eligibility: NO
Real-money eligibility: NO
```

**Reason.** The independent sample is inadequate:

* **31 pooled OOS trades**, below the frozen pre-registered minimum of **100**;
* those 31 trades collapse into only **four portfolio-wide transitive-overlap
  episodes**;
* the **90% episode-block bootstrap CI for total net P&L is
  [−$2,013, +$44,861]** (exact: `[-2,012.84, 44,861.03]`, seed `20260605`,
  10k samples);
* the interval **includes zero**, so the observed positive result is **not
  statistically distinguishable from no edge** at the pre-registered
  confidence level.

The pre-registration governs: on a sub-100 sample, a good-looking profit
factor / return does **not** read as a pass. PROVISIONAL stands by rule, not
by narrative. This earns **one** larger confirmatory test — it does not earn
anything else.

---

## Economic results

Encouraging, but **non-load-bearing**: every number below sits on the
inadequate 31-trade / 4-episode independent sample, so it informs but does not
justify progression.

| Metric | Value |
|---|---|
| Base-cost net P&L | **+$13,737** (`13,737.44`) |
| 1.5× stress net P&L | **+$13,581** (`13,581.37`) |
| Profit factor | **2.954** |
| Sharpe / Sortino | **1.420** / 1.669 |
| Maximum drawdown (base) | **5.78%** (1.5× stress: 5.80%) |
| Calmar | 2.410 |
| CAGR / ann. vol | 13.92% / 9.20% |
| Positive OOS windows | **3 of 4** |
| Top-five-**trade** profit contribution | **66.1%** — yellow flag (50–70%) |
| Maximum single-**instrument** contribution | **29.9%** (gate ≤30%) |
| Top-five-trade-removal result | **+$5.46** — effectively flat, but above the frozen −2% (−$2,000) gate |

Per-window daily marked-to-market attribution: W1 −0.98%, W2 +3.56%,
W3 +6.11%, W4 +4.55%.

1.5× stress scales spread + slippage ×1.5 only (commission is contractual and
unscaled). The PF, Sharpe, windows-positive and bootstrap gates degrade
*mechanically* on a thin sample (flat windows score 0; mostly-zero daily
returns; few episodes ⇒ wide CI) — a spuriously high PF/Sharpe cannot
manufacture a PASS.

---

## Exit-path finding

The 3b OOS run is the first full integration of these code paths. Observed
firing in the real OOS run:

| Exit / control path | Fired? | Count |
|---|---|---|
| Gap stops | **FIRED** | 4 |
| Intraday stops | **FIRED** | 25 |
| End-of-test liquidations | **FIRED** | 2 (ANTO, TSM at 2026-02-28) |
| Five-position-cap blocking | **FIRED** | 21 |
| Same-timestamp contention | **FIRED** | 2 |
| SMA50 trend-break exits | **did NOT fire** | 0 |

**Why SMA50 trend-break did not fire.** The frozen strategy carries a real
SMA50 trend-break exit — `close[i] < sma50[i]` on a completed held bar
(`backtest/breakout_strategy.py:21,144`). In the OOS run, **zero held bars
closed below SMA50**: the **3.0×ATR monotonic ratchet**
(`TRAIL_ATR_MULT = 3.0`, candidate stop = `highest_completed_close − 3.0·ATR14`,
`backtest/breakout_sim.py:17,47`) exited every position first — via the
ratcheted intraday/gap stop — before any close-below-SMA50 condition could be
met on a held bar.

This is a **legitimate data-dependent finding, not a bug**. The SMA50 exit is
still part of the frozen hypothesis and is **not** removed. An ATR-only
strategy (dropping the SMA50 exit) would be a **separate future
pre-registration**, not a modification of this one.

---

## Integrity and commit history

### Commit chain (history must not be — and was not — rewritten)

| Commit | Role |
|---|---|
| `9edec41` | **Commit A** — freeze breakout strategy v3.2 pre-registration (design + audited values). |
| `1113ea2` | **Commit B** — initial engine lock (§2/§2.5/§14). The locked strategy engine files were **not changed after this commit**. |
| `befdce4` | Commit B completion — §4/§5 metrics + 3b harness frozen **BEFORE any OOS run**. This is the **effective strategy-and-metrics implementation lock**. |
| `d5e67e7` | Report **string-formatting-only** correction (pre-OOS; no metric or logic change). |

**`d5e67e7` is the exact successful-run/report commit.** Verified by artifact
provenance, not asserted: `d5e67e7` was committed at `2026-06-07 09:41:23 UTC`;
the OOS report `breakout_3b_oos_2026-06-07.txt` has mtime
`2026-06-07 09:41:29 UTC` — **6 seconds later**. The completed OOS run
therefore occurred *after* the formatting fix, so `d5e67e7` is the commit under
which the successful run/report was produced.

**No metric or threshold was selected after viewing the OOS result.** All
frozen gates (`trades>=100`, `PF>=1.15`, `Sharpe>=0.50`, `net_positive_base`,
`net_positive_1.5x`, `>=3of4_windows_positive`, `no_instrument>30%`,
`top5_removal>=-2%`, `max_dd<=15%`) were fixed in `befdce4`, before any OOS
run. The mechanical verdict is a pure function of those flags.

Provenance of the locked engine files (last commit touching each):

* `backtest/breakout_strategy.py` — last changed `1113ea2`
* `backtest/breakout_sim.py` — last changed `1113ea2`
* `backtest/breakout_metrics.py` — last changed `1113ea2`
* `backtest/breakout_report.py` — last changed `1113ea2`
* `backtest/breakout_run_3b.py` (3b harness) — introduced `befdce4`, formatting fix `d5e67e7`

### Preserved artifacts

The **OOS report is the durable record** of the run. The raw trade ledger, the
daily marked-to-market equity curve, and the episode (transitive-overlap)
assignments are **deterministic in-memory intermediates** of that single
seeded run; they were not persisted to separate files. They are
**deterministic by construction** (seed `20260605`; all inputs pinned by hash
below) and so reproducible in principle — this archive does **not** re-run or
re-verify them post-hoc, deliberately, to preserve the original report and its
`09:41:29` mtime (the `d5e67e7` provenance proof). To reproduce independently,
run the frozen harness against an isolated copy of the inputs (redirect output
to a scratch path so the original report is not clobbered):

```bash
python3 -m backtest.breakout_run_3b      # frozen engine + backtest.db, seed 20260605
```

The report embeds the derived summaries: per-window daily-MTM attribution,
per-instrument net P&L and profit share, the 4-episode count, and the 90%
episode-block bootstrap CI.

* Bootstrap seed: **`20260605`** (`backtest/breakout_report.py:24`,
  `BOOTSTRAP_SEED`), 10,000 samples, deterministic.
* OOS window: **2025-03-01 .. 2026-02-28**, full frozen 14-instrument
  universe, one global rule, no selection, no tuning.
* Universe (14, from the per-instrument panel): SSLN, SGLN, ANTO, ANET, NBIS,
  BARC, AVGO, MSFT, TSM, AAPL, SCCO, PLTR, SU, NVTS.

### Artifact hashes (SHA-256, working tree == `d5e67e7`)

```text
aab6aa955b911af4d717cfd1a00b9f575b2b7edf4f0ef4be53236521c6057b5a  backtest/breakout_strategy.py
e832edabb0d5eccba48679476dc7e6d823be5227ef99fc4158c771e7d2bfeea0  backtest/breakout_sim.py
f4dc9b038df15285826aac6668db804ea02739ffc0224fe5c7c920dd89941a2d  backtest/breakout_metrics.py
f6a448929127fab26bc5442c1f9d4184c68fc43fb92e489437f87282264eb8a4  backtest/breakout_report.py
5c3a2d68153097bb29a8f36658d0d920a91666a035849f8bbd71f84fdea4e7b4  backtest/breakout_run_3b.py
30a181e006b954a603da80827f43349ddaa20f56e3a60f5b2cad83fa7bc5927d  backtest/simulator.py   (cost-config source: CostConfig)
c00d5dd03a62a4b2dc8dd8fb15ce792df1fa296b167b781b737ecc18e6081008  backtest/results/breakout_3b_oos_2026-06-07.txt   (OOS report — gitignored artifact)
a44bdc2f5291d8aa39a0f248cdd1d55639dd4c8aec231b91985d75df29e2226e  backtest/results/breakout_3a_dev_2026-06-07.txt   (3a dev/IS report — gitignored artifact)
a67845e64414d2e27bee0b95eec61a201f0e002c1bffe6671c08917107d6af6e  backtest.db   (OHLCV dataset, gitignored)
190fe49565119d50ddd274edb8a92d887188d8e84ab3931e240cd11f90e90326  instruments.json   (frozen universe / config)
```

Git blob IDs for the version-controlled inputs (pinned at `d5e67e7`):

```text
09de29ab7f886dbfe29bbf303c5ef27d243ce6dc  backtest/breakout_strategy.py
cb39f0a84e09381b2f098745ade83cdd2e985962  backtest/breakout_sim.py
eb58519ee9a4102c7d330808507e5b89e2fb9992  backtest/breakout_metrics.py
354b714f9a87f2cd4b3e98d68bc578378782d17c  backtest/breakout_report.py
43011c3446578bae3c8225ab14e8669eed006e32  backtest/breakout_run_3b.py
5481d3058b110d67a324396649d3c649d11f142a  instruments.json
```

> Note: `backtest.db` and `backtest/results/*` are gitignored, so they are
> pinned here by SHA-256 (content hash) rather than by git object. The cost
> model is built in `backtest/breakout_run_3b.py::_cost_for` on top of
> `CostConfig.for_class(...)` in `backtest/simulator.py` (base; 1.5× stress
> scales half-spread + slippage ×1.5, commission unscaled).

---

## What this result does and does not authorize

**Authorizes:** exactly one larger pre-registered confirmatory extension using
additional unseen history, plus frozen forward paper logging — to be designed
separately.

**Does NOT authorize (prohibited):** Phase 3c; building/activating Claude or
deterministic regime overlays; changing breakout, ADX, SMA, ATR, risk or exit
parameters; removing losing instruments; promoting winning instruments;
extending this OOS window; rerunning variants to improve the outcome;
activating real-money or live strategy behavior.

This archive is committed on `backtest-honesty/breakout-strategy`. No strategy
change is pushed to the production line.
