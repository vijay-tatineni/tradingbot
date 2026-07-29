# Trading System — Independent Review Pack

**Pinned commit:** [`c44fe1f`](https://github.com/vijay-tatineni/tradingbot/tree/c44fe1f) ·
**Branch:** `breakout-strategy` (the integration branch; `main` is stale) ·
**Compiled:** 2026-07-29

Every code link below points at `c44fe1f`, the exact tree described here. Branch links rot;
these will not.

---

## 0. How to read this document

### 0.1 Scope

This reviews the **trading system**: what it trades, how it decides, how it sizes, how it
exits, and what protects it. A remediation programme (Phases 1–3) ran immediately before this
document; it is summarised in §6 because it explains why the safety machinery exists, but the
system — not the process — is the subject.

**The first question a reviewer will ask is "does the strategy have an edge?" The honest
answer is that this document cannot tell you.** The walk-forward validation is inherited and
was not re-run or verified here (§0.2), and the live observation window designed to answer it
had not started when this was written (§7).

### 0.2 Evidence classes — read the markers

| Marker | Meaning |
|---|---|
| ✅ **Verified** | Measured directly against this tree, the live broker, or a live process, in the session that produced this document. The verification command is given in §9. |
| 📄 **Code-read** | Read from source at `c44fe1f`. Accurate as to what the code says; not a claim the code is correct. |
| ⚠️ **Inherited** | Asserted by the repository, an earlier audit, or config, and **not independently verified here**. Treat as a claim to test, not a fact. |

A reviewer who cannot separate these will either over-trust the document or discard it. The
distinction is load-bearing, so it is applied per claim rather than in a blanket disclaimer.

---

## 1. What the system is

📄 A multi-layer equity/ETF trading bot against **IBKR paper account `DUQ141950`**, running
one cycle per minute ([`instruments.json` `check_interval_mins: 1`]).

| Layer | Purpose | Code |
|---|---|---|
| **Layer 1 — Active Trading** | The main strategy. Triple Confirmation entries, two-tier exits, re-entry logic. | [`bot/layer1.py`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/layer1.py) |
| **Layer 2 — Accumulation ETFs** | RSI + Williams %R dip buying, every 6th cycle. Sells 50% on overbought. | [`bot/layer2.py`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/layer2.py) |
| **Layer 3 — Silver Scalper** | Intraday SSLN momentum scalper (LSE hours), 0.3% bounce entry, 0.2% trail, forced exit 16:15 London, £50 daily loss cap. | [`bot/layer3_silver.py`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/layer3_silver.py) |

✅ **14 instruments enabled**, 10 disabled. ⚠️ The `disabled_reason` values —
`no_edge_walk_forward`, `backtest_poor_performer_2yr`,
`no_edge_walk_forward_confirmed_live_loss` — are inherited claims from prior analysis and were
not re-tested here.

✅ Account baseline **GBP 250,000** cash / **GBP 251,006.98** NetLiquidation, flat (0 positions,
0 working orders) at restart.

---

## 2. The algorithm

### 2.1 Indicators

📄 [`bot/indicators.py`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/indicators.py).
All computed from OHLCV bars fetched from the broker per cycle.

| Indicator | Definition | Configured value |
|---|---|---|
| **Alligator** ([`:137`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/indicators.py#L137)) | Bill Williams. SMMA of median price `(H+L)/2`, three lines, each shifted forward. | jaw 13/shift 8, teeth 8/5, lips 5/3 ([`:81-83`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/indicators.py#L81)) |
| **MA200** ([`:173`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/indicators.py#L173)) | Simple MA of close. `BULL` if price > MA, else `BEAR`. | period 200 |
| **Williams %R** ([`:186`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/indicators.py#L186)) | `-100 × (highest_high − close) / (highest_high − lowest_low)`. Emits `CROSS_UP`/`CROSS_DOWN`/`ABOVE`/`BELOW` around a midline. | period 14, mid −50 |
| **ADX** ([`:215`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/indicators.py#L215)) | Wilder. Used only as a trend-strength gate. | period 14, threshold 20 |
| **RSI** ([`:257`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/indicators.py#L257)) | Used by Layer 2 only. | period 14, oversold 35, overbought 70 |

**Alligator state** is derived from the normalised gaps between the three lines, with
`alligator_min_gap_pct = 0.003`:

```
gap_jt = |jaw − teeth| / price ;  gap_tl = |teeth − lips| / price
SLEEPING  if both gaps <  0.003          → no trade at all
EATING    if both gaps >  0.009  (3×)    → HIGH confidence
WAKING    otherwise                      → MEDIUM confidence
direction = BULL if lips > teeth > jaw ; BEAR if lips < teeth < jaw ; else NONE
```

> ⚠️ **Reviewer note.** `SLEEPING` requires *both* gaps below the threshold and `EATING`
> requires *both* above 3×; everything else is `WAKING`. Confidence therefore maps to the
> spread of a moving-average fan, not to any measure of expected return. Whether that is a
> meaningful confidence signal is a strategy question, not a code-correctness one.

### 2.2 Signal engine — "Triple Confirmation"

📄 [`bot/signals.py`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/signals.py).
Pure and stateless: no broker calls, no side effects. Gate order:

```
1. Alligator SLEEPING            → HOLD  ("sideways market, no trade")
2. bull_score = (alligator BULL) + (price > MA200) + (WR bullish)
   bear_score = (alligator BEAR) + (price < MA200) + (WR bearish)
3. score == 3 AND ADX not WEAK   → BUY (+1) / SELL (−1)
   score == 3 AND ADX WEAK       → HOLD (downgraded, logged)
4. partial (2 of 3)              → HOLD
confidence = HIGH if alligator EATING else MEDIUM
```

**All three must agree.** There is no weighting or scoring beyond unanimity, and ADX acts only
as a veto — it never promotes a signal.

⚠️ Only `HIGH` and `MEDIUM` signals are actionable downstream
([`layer1.py:415`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/layer1.py#L415)),
so a `LOW` never trades.

### 2.3 The regime layer — mostly **not live**

This matters more than its size in the codebase suggests, and it is the component the
observation window is meant to judge.

✅ Flags observed in the live startup banner:

| Component | Shadow | **Live** |
|---|---|---|
| classifier (Claude-based regime labelling) | true | **false** |
| persistence / smoothing | true | **false** |
| router (regime → engine selection) | true | **false** |
| overlays (event/macro blocks) | true | **false** |
| mean_reversion engine | true | **false** |
| **regime_filter** | — | **true** |

> ⚠️ **Only `regime_filter` is live.** The classifier, router, smoothing and overlay machinery
> run in shadow and record decisions without affecting trading. A reviewer should not read the
> extensive `bot/regime/` tree as describing live behaviour.

📄 The live path is a plugin hook: `layer1._regime_filter_allows`
([`:247`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/layer1.py#L247)) asks
every plugin via `apply_regime_filter`, and **fails open** — a plugin that raises is logged and
ignored, so a buggy plugin cannot halt trading. That is a deliberate availability-over-safety
choice and is worth a reviewer's attention, because it is the opposite of the fail-closed
posture taken elsewhere (§4.4).

📄 Regime internals are documented here **at interface level only**. `bot/regime/router.py`,
`smoothing.py`, `orchestrator.py` and `entry_gate.py` carry truth tables referencing
[`specs/CLAUDE_STRATEGY_SPEC_v3.md`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/specs/CLAUDE_STRATEGY_SPEC_v3.md)
(1,594 lines), which was **not** read for this document. If the review needs the regime design
audited, that spec plus those four modules is the parcel.

---

## 3. The live entry pipeline

📄 This ordering **is** the review surface — a trade must clear every gate, in this order:

| # | Gate | Code | Blocks when |
|---|---|---|---|
| 1 | Signal | `signals.py` | not a unanimous 3/3 with ADX ok, or confidence `LOW` |
| 2 | `allow_new_entries` | [`layer1.py:277`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/layer1.py#L277) | instrument is exits-only |
| 3 | **Regime filter** | [`layer1.py:247`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/layer1.py#L247) | any plugin vetoes (fails open on error) |
| 4 | **`_can_enter`** | [`layer1.py:173`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/layer1.py#L173) | equity unreadable · **position divergence** · max 5 open · max 2 entries/cycle |
| 5 | **`validate_order`** | [`bot/order_validator.py`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/order_validator.py) | qty ≤ 0 or > 500 · notional > $5,000 · price ≤ 0 · daily loss ≥ $500 · weekly ≥ $1,500 |
| 6 | LLM sentiment | `layer1._llm_sentiment_check` | Claude sentiment below threshold |
| 7 | Plugin `pre_trade` | all plugins | any plugin returns False |
| 8 | Execution | `broker.handle_signal` | — |

Gates 4 and 5 are the hard risk limits; 3, 6 and 7 are discretionary filters.

---

## 4. Exits and protection

### 4.1 Two-tier synthetic exits

📄 [`bot/position_tracker.py`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/position_tracker.py)

- **Tier 1 — emergency stop**, every cycle, measured from **entry**:
  `LONG: entry × (1 − pct/100)`, `SHORT: entry × (1 + pct/100)`
  ([`:331`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/position_tracker.py#L331)).
- **Tier 2 — trailing stop + take profit**, on the cadence in §4.2. The trail ratchets only in
  the favourable direction.

### 4.2 Exit cadence (fixed in Phase 2)

📄 [`bot/bar_schedule.py`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/bar_schedule.py)
`should_evaluate_tier2`:

| Timeframe | Cadence |
|---|---|
| **daily** | **every cycle** (~1 min) |
| **4hr** | a 5-minute window after each reachable bar close, **plus** a pre-close window where the boundary falls at/after the market close |

⚠️ Tier-2 evaluation had **never run** for daily names before this fix (§6.2).

### 4.3 Broker-held protective stops

📄 [`bot/protective_stops.py`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/protective_stops.py),
[`bot/orders.py`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/orders.py)

Every entry is submitted as an **atomic bracket**: a `MKT` parent with `transmit=False` plus an
attached `STP` child with `transmit=True`, so the position can never be live at the broker
without protection. The stop sits at the **emergency** level, not the trail — the broker stop
is a backstop for process/host death, while the synthetic trail remains the primary exit.

Three invariants:

1. **No unprotected position persists.** If the parent fills but the stop does not become a
   working order, the attach is retried once; failing that the position is **flattened
   immediately** and alerted.
2. **No ratcheting.** The stop is submitted once and left static. Every synthetic exit
   therefore **cancels** it first (`orders.close()`), because a stop left working after a close
   can *open* a new position in the opposite direction.
3. **Startup audit.** Tracked positions without a broker stop, and orphaned stops without a
   position, are alerted.

### 4.4 Fail-closed discipline (and one exception)

📄 Three places refuse to infer safety from a failed read:

- Broker position read fails → no divergence inferred, no entry block cleared
  ([`bot/reconciliation.py`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/reconciliation.py)).
- Stop introspection unsupported/failing → reported **UNVERIFIED**, not "unprotected".
- Equity unreadable → **all new entries blocked** for that cycle; exits unaffected.

⚠️ The exception is the regime filter (§2.3), which fails **open**. Deliberate, but
inconsistent with the above, and worth a reviewer's judgement.

---

## 5. Position sizing

📄 [`bot/sizing.py`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/sizing.py)

```
risk_capital  = equity × risk_fraction            (account base currency)
stop_distance = price × trail_stop_pct / 100      (per share, native units)
qty           = floor(risk_capital / (stop_distance × fx_to_base))
```

- Equity is read **live** from the broker (`NetLiquidation`) once per cycle, never hardcoded.
- Sized against the **synthetic trail** stop — the primary exit — not the wider broker stop.
- Caps to `max_qty_per_order` / `max_notional_per_order` so it produces orders the validation
  gate accepts.
- Returns **0** when one share would exceed the budget; the gate then rejects a non-positive
  qty and no position is taken.

> ⚠️ **`risk_fraction` is not present in `instruments.json` settings.** The intended 1% comes
> from `DEFAULT_RISK_FRACTION` in code. It is the intended value, but it is implicit — a
> reviewer should flag that a risk parameter of this importance is not stated in config.

### 5.1 Process-death exposure

Because sizing uses the trail but the broker holds the emergency stop, a process/host death
where only the broker stop fires costs `emergency_pct / trail_pct` × the intended risk. That
ratio was **capped at 3×** before the window (§6.3):

| | Before cap | After cap |
|---|---|---|
| Worst single enabled position | GBP 25,000 (10% of equity, NVTS at 10×) | **GBP 7,500 (3%)** |
| Three worst concurrent | GBP 50,000 (20%) | **GBP 22,500 (9%)** |

---

## 6. What was broken, and what was fixed

### 6.1 The incident that started it

⚠️ Inherited, from `docs/flatten_record.md` and the audit: an attempt to flatten the account
**closed at 2× size (or ran twice), flipping six long positions to short**. A subsequent audit
found **8 positions open at the broker and 0 working orders** — no broker-held stop existed for
anything — and **4 of those 8 were absent from `positions.db` entirely**, so they had no
synthetic stop either.

✅ Verified independently during Phase 1: the six shorts existed at the broker with exactly the
negated sizes.

The account was ultimately flattened by an **overnight cash reset, not by trading** — ✅
confirmed via a clientId-0 probe showing **zero executions**, which is the only client that
sees activity originating outside the API.

### 6.2 Defects fixed

| # | Defect | Failure mode | Fix |
|---|---|---|---|
| A | No reconciliation between broker and `positions.db` | Positions open at the broker, invisible to the bot, with no stop of any kind | Per-cycle diff, alert on transition, per-symbol entry block ([`bot/reconciliation.py`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/reconciliation.py)) |
| B | No broker-held stops anywhere | Process death = every position unprotected | Atomic bracket at the emergency level, cancel-on-exit, flatten-if-unprotectable |
| C | Tier-2 exits **never ran** for daily names | The daily bar-close window *was* the market close, and the bot skips closed markets, so the two never overlapped. Trail/TP were dead code. | `should_evaluate_tier2`: every cycle for daily |
| C2 | Same defect on the 4hr **final** bar (US 16:00, LSE 17:00) | Last bar of every session never evaluated | Pre-close window |
| D | Equal-notional sizing `int($1000/price)` | A 1%-stop and a 10%-stop name carried identical notional → **10× different risk** | Fixed-fractional risk sizing against live equity |
| — | Gateway `ExistingSessionDetectedAction` empty | IBC blocked on a modal for ~3h; API refused every client; a trading window was lost | Set to `primary` via compose |
| — | Emergency/trail ratio up to 10× | Process death turned 1% risk into a 10% account loss | Capped at 3× across 13 instruments |

✅ All verified in this session, each with tests; C and C2 have tests that fail against the old
behaviour, confirmed by reverting the fix and re-running.

### 6.3 A near-miss worth recording

✅ At the Phase 3 restart, `/root/trading` was found to be **17 commits behind** the branch —
none of the Phase 2 code was deployed. Restarting without noticing would have run the
**pre-remediation bot** while every PR showed green. Deployment (`350b28f` → `c44fe1f`) is what
actually put the fixes into production.

---

## 7. Current state

✅ As of 2026-07-29:

- Bot, API and nginx running; **zero warnings or errors** since restart; equity read succeeding
  every cycle.
- Account flat; reconciliation silent (tracker 0 vs broker 0).
- **The observation window has not started.** It begins at the first full session once the
  remaining pre-restart checks pass — see
  [`docs/observation_window_v2.md`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/docs/observation_window_v2.md).
- IG deployment (`cogniflowai-ig-*`) remains **stopped** pending a demo market-data entitlement
  issue.

> ⚠️ **Before the window starts, read §8.1.** Layers 2 and 3 sit outside every Phase 2
> protection and will raise reconciliation divergences the window treats as void-conditions.
> This needs a decision first.

The window pre-registers its own success criteria: expectancy in **R multiples**, counterfactual
expectancy of regime-blocked trades, minimum sample sizes below which it is inconclusive **by
definition**, and a keep/replace/retire rule decided on a **bootstrap confidence interval**
rather than a point estimate.

---

## 8. Known limitations and open items

1. ⚠️ **Edge is unproven here.** Walk-forward results are inherited and were not re-run. The
   `no_edge_*` disable reasons on 10 instruments are likewise inherited.
2. ⚠️ **The measured system ≠ the walk-forward-tested system.** Phase 2 changed exit semantics
   (daily Tier-2 from *never* to every cycle) and sizing. Absolute P&L is not comparable to
   backtests; R-multiple expectancy is the comparable measure.
3. ✅ **Consecutive-loss counter has no era boundary.** `_check_consecutive_losses` reads the
   last five outcomes with no filter on time or system version, and auto-disable writes
   `enabled: false` into `instruments.json`. Old-system losses can disable an instrument under
   a new system. Deferred post-window; the database was started fresh to avoid it.
4. ⚠️ **Regime internals unaudited here** (§2.3) — interface level only.
5. ⚠️ **Regime filter fails open** while everything else fails closed (§4.4).
6. ⚠️ **`risk_fraction` implicit in code**, not config (§5).
7. ⚠️ **MSFT had `entry_price = 0.0`** in the tracker at audit time — a data defect that
   distorts synthetic stop maths. Not re-checked post-restart.
8. ✅ **Layers 2 and 3 are unremediated — see §8.1 below.** This is the most significant open
   finding in this document.
9. ⚠️ **`trades.db` / `trading.db` are empty** and `advisor.db` holds a single 2026-04 report —
   little historical trade record exists to audit.

### 8.1 Layers 2 and 3 are outside every Phase 2 protection

✅ Verified at `c44fe1f`. The Phase 2 work — broker-held stops, reconciliation, risk sizing —
was implemented on **Layer 1's entry path only**. Layers 2 and 3 place orders directly:

| | Call site | `stop_price` passed? | Uses `PositionTracker`? | Sizing |
|---|---|---|---|---|
| **Layer 2** | [`layer2.py:71,79`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/layer2.py#L71) | **no** | **no** (0 references) | fixed `inst['qty']` |
| **Layer 3** | [`layer3_silver.py:212,242`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/layer3_silver.py#L212) | **no** | **no** (0 references) | fixed |

Both are **live**: Layer 2 has **7 enabled** instruments (SMH, QQQ, GLD, NVDA, META, AMZN,
MSFT), Layer 3 has **1** (SSLN).

**Consequence 1 — the "every entry carries a broker stop" guarantee is Layer 1 only.** Layer 2
and Layer 3 positions are opened as bare market orders with no attached stop and no synthetic
tracker entry. On process or host death they have **no protection of any kind** — precisely the
condition the 2026-07 audit found and Phase 2 was built to eliminate.

**Consequence 2 — these positions will trip the reconciler, and that may void the window.**
`diff_positions` is deliberately *not* filtered to `active_instruments`
([`reconciliation.py:88`](https://github.com/vijay-tatineni/tradingbot/blob/c44fe1f/bot/reconciliation.py#L88)),
and only `XAUUSD` is in `unmanaged_positions`. So every Layer 2 or Layer 3 position — real at
the broker, absent from Layer 1's `positions.db` — is classified **`untracked`**, which raises
a Telegram alert and **blocks new entries for that symbol**.

**SSLN is the sharpest case: it is enabled in `layer1_active` *and* `layer3_silver`.** A Layer 3
scalp changes the broker quantity for a symbol Layer 1 also tracks, producing a
`qty_mismatch` divergence rather than a merely untracked one.

This matters directly to the observation window, whose §4.3 lists reconciliation alerts as
**expected zero** and treats non-zero as meaning the measured book and the tracked book
disagree — i.e. the window is void. As configured, **the window is likely to be invalidated by
its own architecture within days of Layer 2 or Layer 3 trading.**

Three options, none of which should be chosen without review:

1. Stop Layers 2 and 3 for the duration of the window (cleanest measurement; loses their P&L).
2. Add their symbols to `unmanaged_positions` (silences the alerts, but re-creates the exact
   blind spot the audit found — positions nothing watches).
3. Extend Phase 2 protections to Layers 2 and 3 (correct, but is new work and would change the
   system mid-window).

---

## 9. Verify it yourself

All commands assume the repo at `c44fe1f`.

| Claim | Command |
|---|---|
| Signal requires unanimity | `sed -n '60,95p' bot/signals.py` |
| Alligator periods/shifts | `sed -n '81,83p' bot/indicators.py` |
| Configured thresholds | `python3 -c "import json;print(json.load(open('instruments.json'))['settings'])"` |
| Entry gate order | `grep -n '_regime_filter_allows\|_can_enter\|_validate_entry\|pre_trade' bot/layer1.py` |
| Bracket is atomic | `grep -n 'transmit' bot/orders.py` |
| Stop level = emergency, not trail | `grep -n 'resolve_emergency_stop_pct' -A 12 bot/protective_stops.py` |
| Sizing formula | `grep -n 'def calculate_risk_qty' -A 40 bot/sizing.py` |
| No instrument exceeds 3× | `python3 -m pytest tests/test_stop_ratio_cap.py -q` |
| Tier-2 cadence + old-behaviour tests | `python3 -m pytest tests/test_exit_cadence.py tests/test_4hr_close_boundary.py -q` |
| Regime flags are shadow-only | `grep -A 10 'Flags:' bot_stdout.log \| tail -10` |
| Full suite | `python3 -m pytest tests/ -q` → 2,089 passed, 5 skipped |

---

## 10. Questions worth putting to the reviewer

1. Is unanimity across three correlated trend indicators (Alligator, MA200, Williams %R) a
   genuine edge, or three views of the same momentum? All three are price-derived and
   trend-following.
2. Is Alligator gap-spread a defensible proxy for **confidence**, given it drives no sizing but
   does drive tradeability?
3. Sizing uses the trail stop while the broker holds a 3× wider stop. Is a 3× process-death
   multiple the right cap, or should sizing use the *wider* stop and accept smaller positions?
4. The regime filter fails open. Correct for availability, or should it fail closed like the
   rest?
5. Given §8.1 — Layers 2 and 3 opening unprotected positions that also trip the reconciler —
   should they be stopped for the window, exempted, or remediated first? Note that "exempt"
   re-creates the audit's original blind spot.
6. Are the pre-registered sample floors (30 trades) adequate to conclude anything about a
   regime filter, given trade P&L is heavy-tailed?
