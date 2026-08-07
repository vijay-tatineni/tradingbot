# Regime Strategy System

Reference: `specs/CLAUDE_STRATEGY_SPEC_v3.md` §7-§9, §14

## Overview

The regime strategy system classifies market conditions into TRENDING, RANGING, or UNCLEAR, then routes each instrument to the appropriate strategy engine.

## Components

### Classifier (`bot/regime/classifier.py`)
Classifies an instrument's market regime using ADX, Bollinger bandwidth, and trend strength. Returns `RegimeClassification` with regime, confidence, and feature values.

### Smoothing (`bot/regime/smoothing.py`)
Prevents whipsaw by requiring N consecutive same-regime classifications before transitioning. Tracks days-in-regime for downstream logic.

### Router (`bot/regime/router.py`)
Maps smoothed regime + overlay state + flags → engine selection + entry permission. Routing table in §9.8. Invariant: NoOpEngine ⇒ entries blocked.

### Entry Gates (`bot/regime/entry_gate.py`)
Three independent gates evaluated in order:
1. **Instrument pause** — always checked, blocks unconditionally
2. **Overlay gate** — only when `enable_event_overlays_live=true`
3. **Router gate** — only when `enable_router_live=true`

Overlays can block without router being live. See §14.2 truth table.

### Orchestrator (`bot/regime/orchestrator.py`)
Plugin that integrates regime logic into the main trading loop via `pre_trade()`. Does not modify `bot/layer1.py`.

## Feature Flags

See `bot/regime/flags.py`. Dependency chain:
```
enable_mean_reversion_live → enable_router_live → enable_persistence_live → enable_classifier_live
enable_event_overlays_live (independent axis)
```

## Rollout

All flags default to shadow. Enable incrementally bottom-up. The system runs in shadow mode alongside the existing triple-confirmation engine, logging what it would do differently.
