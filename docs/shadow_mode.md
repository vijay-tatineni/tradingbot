# Shadow Mode

Reference: `specs/CLAUDE_STRATEGY_SPEC_v3.md` §11

## Overview

Shadow mode runs the regime-aware strategy system in parallel with the live triple-confirmation engine. It logs what the regime system would have done without affecting live trading.

## Components

### Counterfactual Logger (`bot/shadow/counterfactual_logger.py`)
Logs every decision comparison to `shadow_decisions` table. Records live vs shadow engine selection, action, regime, and disagreement type.

### Hypothetical Trades (`shadow_hypothetical_trades` table)
Tracks paper positions that shadow would have opened. Closed when exit conditions are met. PnL calculated for performance comparison.

### Position Metadata (`bot/shadow/position_metadata_store.py`)
Tags live fills with the entry strategy and regime. Used by the exit policy to dispatch exits via the entry engine (§12.2).

### Exit Policy (`bot/shadow/exit_policy.py`)
Enforces "exit via entry strategy" contract. Looks up position metadata to determine which engine handles exit logic, regardless of current regime.

### Simulator (`bot/shadow/simulator.py`)
Async bounded queue for shadow position lifecycle. Manages hypothetical opens, exits, and abandonment.

## Table Isolation

Shadow code writes ONLY to `shadow_*` tables. Live code writes ONLY to live tables. Enforced by `tests/invariants/test_shadow_isolation.py` with row-count verification across all 7 tables.

## Disagreement Types

- `ENGINE_MISMATCH` — shadow would use a different engine
- `ACTION_MISMATCH` — same engine, different action (BUY vs HOLD)
- `None` — shadow agrees with live
