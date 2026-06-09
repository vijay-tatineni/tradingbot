# Dynamic Universe v1 — IBKR-only Shadow Foundation (freeze record)

> Branch `feature/dynamic-universe-ibkr-shadow-v1` (base production `breakout-strategy`
> @ `ffd6d23`, which carries the deployed hard-disabled invariant). **Additive,
> feature-flagged, non-integrated.** No live/paper execution, no Order Intent, no IG
> execution, no EODHD ingestion, no Claude/TTI. The breakout result remains PROVISIONAL
> (Phase 3c: NO; real-money: NO) and is untouched. Date: 2026-06-09 (UTC).

## 1. Approved operator policy (frozen here)

```text
Dynamic Universe v1 initial execution gateway:  IBKR
IG cash-equity routing:  BLOCKED until EPIC identity, data entitlement, sizing
                         semantics, and gateway preflight are independently verified.
Automatic broker fallback:        PROHIBITED
Mirrored IBKR + IG execution:     PROHIBITED
Dual-broker allocation:           not permitted unless separately pre-registered + approved
```

See `docs/dynamic_universe_gateway_partition_v1.md` for the per-instrument allocation.

## 2. Frozen v1 assumptions (used exactly; not tuned from outcomes)

Encoded in `bot/universe/params.py` (a test asserts the risk/exit constants equal the
frozen `backtest/breakout_sim.py`):

```text
Candidate sources:                AUTO, TTI, MANUAL
TTI/MANUAL candidate TTL:         5 completed sessions
Minimum usable history:           250 valid completed daily bars
Minimum price:                    $10 (local-currency equivalent)
Minimum ADV20:                    $20,000,000 equivalent
Entry hysteresis:                 2 consecutive passing completed sessions
Ordinary eligibility removal:     2 consecutive failing completed sessions
Post-exit cooldown:               3 completed sessions
Max total open positions:         5
Max positions per sector:         2
Risk per trade:                   0.50% equity
Max notional per instrument:      20% equity
Max portfolio heat:               2.50%
Candidate priority:               1) ADV20 desc  2) est. spread asc  3) stable canonical id
```

Breakout entry / exit logic is the FROZEN deterministic rules, reused read-only from
`backtest/breakout_strategy.compute_indicators` (close > preceding-20-day-high excl
current AND close > SMA200 AND SMA50 > SMA200 AND ADX14 > 25; initial stop = entry −
2×ATR14; trailing = highest completed close − 3×ATR14 monotonic; trend break =
completed close < SMA50). Nothing in the breakout logic is modified.

## 3. Scope boundaries (what this task does NOT do)

```text
- no live/paper dynamic execution; no Order Intent routing; no cross-broker router
- no IG execution; no IG mapping change; no EODHD call/ingestion
- no change to instruments.json / instruments_ig.json / feature_flags in live config
- no change to hard-disabled behaviour or frozen breakout/risk parameters
- the shadow foundation is NOT wired into main.py (zero live runtime effect)
```

## 4. Deliverable map

```text
Workstream 1 (freeze):    this file; docs/dynamic_universe_gateway_partition_v1.md
Workstream 2 (IG audit):  docs/ig_shutdown_readiness_audit.md; docs/ig_service_pause_runbook.md
Workstream 3 (shadow):    bot/universe/*; tests/universe/*;
                          docs/{dynamic_universe_shadow_implementation,universe_db_schema,
                                dynamic_universe_state_transitions_v1,
                                dynamic_universe_shadow_operations}.md
Provider status:          docs/dynamic_universe_provider_status.md (EODHD still blocked)
```
