# Trading Bot Strategy and Dynamic Universe Status Review

> **Documentation-only record.** No strategy change, config edit, service restart, deployment,
> IBKR connection, DB/snapshot creation, or data/runtime artifact accompanies this document. It
> summarizes existing repo evidence and `/root/deployment_records/*` for a go/no-go review.
>
> - Repo: `/root/trading`, branch of record at write time: `breakout-strategy`
> - Deployed HEAD referenced throughout: **`c6a1c00`**
> - Date: 2026-07-08

## Executive Summary

The Dynamic Universe rollout aimed to let the bot trade a broker-resolved, dynamically maintained
instrument universe in a **shadow-only** (observe, never trade) mode before any paper or live use.
A large amount of additive, feature-flagged code and offline tooling was merged to `main` and
**deployed inert** — none of it is wired into the live trading path and every master flag is off or
absent. Shadow mode was **never enabled**; paper/live trading was **never approved**.

The rollout is **blocked and now paused**. The immediate blocker is a missing prerequisite chain:
there is **no approved canonical identity source** (no `universe.db`, no R2A-1 identity records) and
**no usable approved offline OHLCV package** (the only package on disk is an unfilled placeholder
template). A bounded IBKR historical OHLCV export was attempted and **stopped at preflight before any
connection** for exactly this reason.

Separately, a review of the **existing live bot** surfaced several live-risk questions that are
plausibly higher priority than continuing Dynamic Universe. Those are documented here (not fixed).

Key conclusions (verbatim):

```text
Dynamic Universe code/tooling was merged and deployed inert.
Shadow mode was never enabled.
Paper/live trading was never approved.
No universe.db or universe_shadow.db was created.
No IBKR OHLCV export occurred.
The rollout blocked because no approved canonical identity source and no approved OHLCV package existed.
Further Dynamic Universe work is paused.
```

## Repository and Branch Status

- Production/deployed working tree: `/root/trading`
- Base branch: `breakout-strategy`
- Documentation branch: `docs/trading-bot-status-review`
- Current deployed HEAD before this documentation branch: `c6a1c00554a28187a748997c17d42ab2c29379da`
- Documentation-only commit: `34a75c487822fa7f05abc23708d50629f9235d99`
- PR: https://github.com/vijay-tatineni/tradingbot/pull/15
- Status: documentation-only branch; no strategy, runtime, config, DB, snapshot, broker, or service changes.

This document records the paused state of the Dynamic Universe rollout and the trading bot strategy review. It does not authorize activation, deployment, migrations, IBKR access, shadow mode, paper trading, or live trading changes.

## Original Trading Bot Strategy

The live bot's trading logic lives in `bot/layer1.py` (active), `bot/layer2.py` (accumulation), and
`bot/layer3_silver.py` (silver scalper), orchestrated by `main.py`; instruments and settings are
loaded from `instruments.json`.

The **active** engine is the **Triple Confirmation** strategy (`bot/signals.py`,
`bot/indicators.py`):

- **Gate 1 — Alligator awake:** if the Williams Alligator is `SLEEPING` (sideways market), no trade.
- **Gate 2 — three independent signals must agree:** Alligator direction (BULL/BEAR), price vs.
  MA200 trend, and Williams %R signal. A full `3/3` agreement is required to emit a signal;
  `2/3` is a logged PARTIAL (no trade), anything else is MIXED (no trade).
- **ADX filter:** even on `3/3`, a `WEAK` ADX trend downgrades to no-trade / LOW confidence;
  confidence is `HIGH` when the Alligator is `EATING`, else `MEDIUM`.
- **Position sizing:** risk-based, deriving quantity from a target notional (`bot/layer1.py:243`,
  "Risk-based position sizing: calculate qty from target_notional").

Layer 2 (`Accumulation`) and Layer 3 (`SilverScalper`, LSE-hours scalping) run alongside as separate
engines. The dashboard labels Layer 1 as "ACTIVE TRADING · TRIPLE CONFIRMATION"
(`bot/dashboard.py:352`).

## Regime-Aware Strategy Logic

A regime-aware overlay (`bot/regime/`, documented in `docs/regime_strategy.md`, spec
`specs/CLAUDE_STRATEGY_SPEC_v3.md` §7–§9, §14) classifies each instrument's market condition and
routes it to an appropriate engine:

- **Classifier** (`bot/regime/classifier.py`): TRENDING / RANGING / UNCLEAR from ADX, Bollinger
  bandwidth, and trend strength.
- **Smoothing** (`bot/regime/smoothing.py`): requires N consecutive same-regime reads before a
  transition (anti-whipsaw); tracks days-in-regime.
- **Router** (`bot/regime/router.py`): maps smoothed regime + overlay state + flags → engine
  selection + entry permission (invariant: `NoOpEngine ⇒ entries blocked`).
- **Entry gates** (`bot/regime/entry_gate.py`): instrument-pause (always), overlay gate
  (`enable_event_overlays_live`), router gate (`enable_router_live`).
- **Orchestrator** (`bot/regime/orchestrator.py`): integrates via `pre_trade()` and, by design,
  **does not modify `bot/layer1.py`**.

Flags (`bot/regime/flags.py`) default to **shadow**; the system logs what it *would* do differently
alongside the live triple-confirmation engine and is enabled incrementally bottom-up. Per the memory
of record, a live regime-filter experiment was scoped (evaluated via the dashboard Filter tile /
`filter_performance`), and the regime filter's incremental value remains an open question (see Risks).

## What Was Actually Live

- **Live:** the Triple Confirmation engine (Layer 1) plus Layer 2/Layer 3, on the fixed
  `instruments.json` universe, under the existing services (`cogniflowai-bot`, `-api`, and the IG
  instance `-ig-bot`, `-ig-api`).
- **Shadow / not live:** the regime overlay (flags default to shadow) and **all** Dynamic Universe
  code (see below).
- **Never live:** Dynamic Universe shadow mode, Dynamic Universe paper trading, Dynamic Universe live
  trading.

## Dynamic Universe Goal

Replace the static `instruments.json` universe with a **dynamically maintained, broker-resolved**
universe, gated behind a strict rollout:

1. merge additive, feature-flagged code **inert** (no behavior change);
2. enable **shadow mode** (evaluate and log candidate selection/sizing/risk against real bars, but
   **never** route an order);
3. only after shadow evidence, consider paper, then live.

A hard design principle throughout: **canonical identity is broker-free** — every instrument must
carry the triple `(canonical_instrument_id, instrument_uid, listing_uid)` derived from a verified
economic anchor (ISIN/FIGI + MIC + currency), **never** from a ticker.

## Work Completed

Merged to `main` and **deployed inert** (representative commits; all "disabled/un-wired"):

- `4d3c046` — Dynamic Universe v1: IBKR-only shadow foundation (additive, feature-flagged).
- `5f40b63`, `1792ebd`, `62a68d0` — shadow hardening (cooldown freeze, position seam, corp-action
  policies, scheduler bar-availability, offline rehearsal; atomic migrations; USD FX normalization).
- `5da3e6e` → `6713cfa` (**R1 series**) — state & position safety, lifecycle-safe close identity,
  replay integrity.
- `b5445ae`, `8251cb6` (**R2A-0/0.1**) — close-event reuse, NULL-hash fail-closed, strict lifecycle
  evidence, fail-closed migration.
- `2c36b46` (**R2A-1**) — canonical identity + verified broker mappings (`IdentityStore`,
  `resolve_identity_atomic`, opaque uid derivation, append-only identity audit).
- `efff5d2`, `fa026e3` (**R2B / residuals**) — persisted candidate-source integration, observability,
  selection audit, atomicity.
- `05eacfb` (**R2C**) — FX-normalized sizing + portfolio heat.
- `e6da7a5`, `44f96c4` (**shadow prereqs W1/W2**) — broker-free completed-bar provider boundary,
  lazy flag-gated construction, unsafe-DB-path blocklist (blocks `universe.db` for the shadow path).
- `9f49e3f`, `ceca042` (**Gate C wiring**, PR #10) — default-off, fail-closed runtime wiring in
  `main.py`; **un-wired in production**.
- `ebc3285`/`c18d1c8` (PR #11), `7f17052` (PR #12), `557c843` (PR #13), `b62a712` (PR #14, HEAD
  `c6a1c00`) — broker-free `LocalCompletedBarSnapshotProvider` / `LocalBarsSnapshotProvider`, the
  offline snapshot **records/seed seam**, and offline snapshot **generation/validation tooling**
  (`tools/gen_shadow_snapshots.py`, `tools/validate_shadow_snapshots.py`).

Package layout: `bot/universe/` (identity, DB/migrations, providers, evaluator, scheduler, risk,
sizing), `tools/` (offline snapshot tooling), `tests/universe/` (R2A/R2B/R2C suites),
`docs/dynamic_universe_*.md`. All additive; none imported on the live hot path.

## Safety Guardrails Preserved

- **Feature-flagged and default-off:** `enable_dynamic_universe_shadow` is absent from config
  (defaults off); `main.py` Gate C wiring imports `bot.universe` only lazily after the master flag is
  confirmed true.
- **Broker-free identity:** identity is derived from a verified anchor, never a ticker; broker
  `conid`/`epic` are mapping attributes only; `ig_mapping` is always written
  `order_routing_blocked=1`.
- **Fail-closed everywhere:** unverified/ambiguous/stale/future-dated references write nothing;
  construction is gated so it cannot silently create a DB with the flag off.
- **DB path blocklist:** `PRODUCTION_DB_BASENAMES` forbids the shadow path from opening `universe.db`.
- **Offline tooling is out-of-band:** `tools/gen_shadow_snapshots.py` is not imported by `main.py`,
  makes no broker call, and has no default output dir (cannot accidentally create runtime dirs).
- **Order-routing frozen:** no Dynamic Universe path reaches order placement; no `placeOrder`.

## Where the Rollout Blocked

Shadow enablement requires a real, approved data feed of completed bars for the candidate universe.
That in turn requires (a) an **approved canonical identity source** so each instrument carries the
R2A-1 triple, and (b) an **approved offline OHLCV package** to generate snapshots. Neither exists in
an approved, usable form, so shadow mode cannot be turned on.

## IBKR OHLCV Export Attempt

A bounded, read-only IBKR historical OHLCV export for AAPL, MSFT, AMZN, GOOGL, JPM was planned
(`/root/deployment_records/ibkr_historical_export_plan_20260708T193013Z.md`) and attempted
(`/root/deployment_records/ibkr_historical_export_and_snapshot_generation.md`).

- **It stopped at preflight — no IBKR connection was made.** A live IB Gateway *was* reachable
  (ports 4001/4002 open), so this was **not** a connectivity failure.
- Per-approval rule *"if any preflight fails, do not connect"*, the export halted because there was
  no approved identity source to resolve the required triple (ticker fallback is forbidden). No
  `ohlcv.json`, no snapshots, no order/market-data/account call.
- **Verdict:** `IBKR_HISTORICAL_EXPORT_STOPPED_OR_FAILED`.

## Canonical Identity Source Blocker

Analyzed in `/root/deployment_records/gate_g4_canonical_identity_source_unblock_plan.md`
(verdict `GATE_G4_IDENTITY_SOURCE_UNBLOCK_PLAN_READY`):

- The only in-repo path that mints/resolves the triple is
  `IdentityStore.resolve_identity_atomic` (`bot/universe/identity_store.py`). Constructing
  `IdentityStore(db_path)` calls `migrate(db_path)` — i.e. **any use of the R2A-1 path creates/
  migrates `universe.db`**, which is on the forbidden list.
- The snapshot generator reads the triple from its **input JSON**, so a static operator-supplied
  identity file *could* stage the triples without a DB — but that bypasses R2A-1
  verification/conflict/audit and does not satisfy "resolved through `IdentityStore`."
- Options weighed: (1) operator static identity file — no DB, but a prep artifact only; (2) minimal
  `universe.db` bootstrap via `resolve_identity_atomic` — the compliant, audited mechanism, but
  **requires creating `universe.db` under a separate explicit approval**; (3) IBKR contract-details
  bootstrap — **rejected** (violates broker-free identity; combines two forbidden actions).
- Recommended path: operator identity file **now** → minimal `universe.db` bootstrap **under separate
  approval**. A fully compliant identity source ultimately requires creating `universe.db`.

## Current State

Recorded in `/root/deployment_records/dynamic_universe_rollout_paused.md` (verdict
`DYNAMIC_UNIVERSE_ROLLOUT_PAUSED`), verified read-only:

- Deployed HEAD `c6a1c00`, branch `breakout-strategy`; services unchanged.
- Dynamic Universe flags off/absent; package unwired from the live path.
- **`universe.db` and `universe_shadow.db` both ABSENT**; no `*.db` anywhere (incl. all backups)
  contains `instrument_identity` / `instrument_listing` / `ibkr_mapping`.
- **No shadow snapshots** (`runtime/shadow_snapshots/` absent).
- No IBKR export occurred; no service restart / config edit / migration / order activity.
- The only OHLCV package on disk (`data_approvals/offline_ohlcv_source/du_shadow_usd_v1/`) is an
  **unfilled placeholder template**: identity fields are `FILL_REAL_*`, hashes are `FILL_SHA256_*`,
  and it covers only 1 of the 5 symbols. **Not a usable approved package.** (This directory is
  untracked and intentionally **not** added to Git.)

The active documentation branch is `docs/trading-bot-status-review`, created from `breakout-strategy`. The operational `/root/trading` deployment remains on `breakout-strategy` at `c6a1c00554a28187a748997c17d42ab2c29379da` unless separately changed. This branch is documentation-only.

## What We Learned

- The pre-enable safety work (R1→R2C, W1/W2, Gate C) succeeded at its narrow goal: a large surface
  was merged with **zero live behavior change** and strong fail-closed guarantees.
- The true gating dependency was **not** code readiness but **data/identity provenance**: a
  broker-free, verified identity source and an approved OHLCV package. These are governance/approval
  artifacts, not engineering tasks, and were underestimated relative to the code effort.
- The "broker-free identity, no ticker fallback" invariant is strict enough that it cannot be
  satisfied by convenience shortcuts (e.g. IBKR contract details) without violating design rules —
  which is working as intended, but means the unblock needs an explicit operator identity decision.
- The prerequisite chain grew multi-step (identity source → OHLCV package → snapshots → shadow),
  which is what motivated the pause: it is no longer a clean, bounded shadow enablement.

## Risks Identified in the Existing Bot

A review of the **currently live** bot raised questions that are plausibly **higher priority** than
resuming Dynamic Universe. These are recorded for a decision, **not fixed here**:

```text
broker-side stop protection
synthetic stop cadence
position sizing
regime filter incremental value
LLM classifier usefulness
```

- **Broker-side stop protection** — whether resting protective stops exist broker-side vs. relying on
  the bot process being alive to exit.
- **Synthetic stop cadence** — if stops are synthetic (bot-evaluated), how often they are checked and
  the exposure between evaluations (gaps, downtime, restarts).
- **Position sizing** — validate the risk-based sizing (`bot/layer1.py:243`) against intended
  per-trade risk and portfolio concentration.
- **Regime filter incremental value** — does the regime overlay measurably improve outcomes over the
  triple-confirmation baseline? (Evaluate via the dashboard Filter tile / `filter_performance`.)
- **LLM classifier usefulness** — whether any LLM-based classification earns its cost/latency/risk.

## Recommendation

1. **Keep Dynamic Universe paused.** Do not create `universe.db`, run an export, generate snapshots,
   or enable shadow mode under current authorization.
2. **Make a go/no-go decision explicitly and separately**, informed by this review, before any
   further Dynamic Universe work.
3. **Prioritize the existing-bot live-risk questions** (especially broker-side stop protection and
   synthetic stop cadence) ahead of resuming Dynamic Universe — live capital risk outranks a
   shadow-mode feature.
4. If Dynamic Universe does resume, follow the G4 plan: an operator-approved identity source first
   (with the exact approval text in the G4 record), then a real filled/approved OHLCV package, then
   snapshots, then shadow — each separately gated.

## Final Verdict

```text
TRADING_BOT_STATUS_REVIEW_DOC_CHECKED_IN
```
