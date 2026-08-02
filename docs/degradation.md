# Degradation Framework

Reference: `specs/CLAUDE_STRATEGY_SPEC_v3.md` §13

## Overview

The degradation framework handles component failures gracefully. When a regime subsystem fails, the system degrades to safe defaults rather than crashing.

## Severity Levels

### Soft Degradation
- Warning logged, counter incremented
- System continues with cached/default values
- Example: single classifier timeout

### Hard Degradation
- ERROR logged, Telegram alert sent
- Feature flag disabled automatically
- Affected instruments paused
- Example: 3 consecutive classifier failures

## Components

### Failure Tracker (`bot/degradation/failure_tracker.py`)
Counts consecutive failures per component. Triggers hard degradation at threshold.

### Policies (`bot/degradation/policies.py`)
Maps component → degradation response (which flag to disable, which instruments to pause).

### Instrument Pause Registry (`bot/degradation/instrument_pause_registry.py`)
SQLite-backed registry of paused instruments. Entry gate checks this first. Pauses persist across bot restarts.

### Events (`bot/degradation/events.py`)
Logs degradation events to `degradation_events` table for dashboard visibility.

### Recovery (`bot/degradation/recover_overlay.py`)
CLI tool for manually clearing overlays and resuming paused instruments after investigation.

## Telegram Alerts

Hard degradation triggers immediate Telegram notification via `bot/regime/alerts.py`. Includes component name, trigger reason, and action taken.
