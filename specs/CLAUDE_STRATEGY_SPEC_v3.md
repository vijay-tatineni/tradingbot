# Task Spec: Regime-Aware Strategy Switching with Claude Classifier

**Branch:** `claude-strategy`
**Target:** CogniflowAI Trading Bot (github.com/vijay-tatineni/tradingbot)
**Model:** `claude-sonnet-4-6` via Anthropic API
**Author:** Vijay / CogniflowAI
**Design:** Option C — Regime-Aware Strategy Switching
**Spec Version:** v3 (adds calendar UI, macro alerts, regression-prevention testing)
**Status:** Implementation spec — ready for Claude Code

---

## Changelog

**v1 → v2** (applied review fixes):
- §3 merge gate split into merge gate vs promotion gate
- §6 removed redundant `enable_regime_block_no_trade` flag
- §7 added `cache_hit` field to `RegimeClassification`, `fill_id` field to `PositionMetadata`
- §9.4 cache-hit now on dataclass field
- §9.8 routing table rewritten with explicit unambiguous rows
- §12 `position_metadata` composite key `(position_id, fill_id)` for partial fills
- §13 overlay hard-failure now pauses instruments (does NOT auto-disable overlay)
- §14 main loop decoupled `allow_new_entries` from `enable_router_live`
- §17 promotion plan reaffirmed
- §19 checklist updated

**v2 → v3** (this version):
- §9.4 macro calendar storage moved to SQLite (with Claude Code choosing JSON cache vs pure SQLite based on simplicity)
- §10.7 new section: macro calendar maintenance alerts (weekly/monthly/startup)
- §15.4 new section: calendar editor web UI (macro editable, earnings editable with override semantics, audit log, JWT auth, confirmation modals)
- §16 expanded into 7 subsections covering unit / invariant / behavioural-equivalence / integration / API / regression-specific / CI configuration
- §16.2 new: invariant tests pinning each v2 review fix
- §16.3 new: golden-file behavioural-equivalence tests with mocked Claude
- §16.5 new: HTTP API tests for calendar UI
- §16.6 new: named regression tests, one per v2 fix
- §16.7 new: pre-commit hooks + CI config + optional nightly regression replay
- §19 deliverables checklist regenerated to reflect v3 additions

---

## 0. How to Read This Spec

This spec describes a complete architecture. Claude Code should build it **all at once**, behind feature flags, with everything defaulting to shadow mode. Nothing new affects live trading behaviour until a human flips a specific flag in config.

The architecture replaces "triple-confirmation only" with a regime-aware router that dispatches to different deterministic strategies based on market regime (classified by Claude, smoothed deterministically) and a separate overlay system (fully deterministic) that gates entries based on event risk, liquidity, and data quality. A web UI on the existing nginx-served dashboard allows manual editing of the macro calendar (always manual) and earnings calendar (Finnhub-authoritative with manual overrides and additions).

**Read these sections first, in order:** §1 (objective), §2 (architecture overview), §6 (feature flags), §7 (dataclasses), §16 (testing strategy). Everything else is implementation detail for those five.

---

## 1. Objective

Build a regime-aware trading system where:

1. **Claude** classifies each instrument's market regime daily (TRENDING / RANGING / UNCLEAR)
2. **Deterministic smoothing** prevents regime label thrashing via persistence and hysteresis rules
3. **Deterministic overlays** (earnings lockout, macro lockout, low liquidity, data quality) block new entries independently of regime
4. **A router** selects which strategy engine runs based on smoothed regime + overlay state
5. **Per-regime strategy engines** (TripleConfirmation for TRENDING, MeanReversion for RANGING) generate candidate signals
6. **The order validator** (existing) enforces risk sizing and caps
7. **Position metadata** ensures positions exit under the strategy and regime they were entered with
8. **Counterfactual logging** records what the shadow system would have done vs what the live system did, for evidence-based promotion decisions
9. **Web UI** allows editing of macro calendar (manual source of truth) and earnings calendar (Finnhub overrides + additions), with audit log and confirmation modals on destructive actions
10. **Maintenance alerts** notify when macro calendar runs low so manual maintenance doesn't slip

All new capabilities default to **shadow mode**. Live promotion of each capability requires an explicit flag flip backed by 30+ days of shadow data.

### Non-goals
- Changing the order validator
- Changing risk sizing ($1,000 per position)
- Auto-updating `instruments.json` or `instruments_ig.json`
- Going live with real money in this branch
- Building a mean-reversion strategy that is proven to have edge — spec includes the engine skeleton and validation scaffolding; strategy-discovery research happens after shadow data accumulates
- Multi-user authorization (single-user JWT only)

---

## 2. Architecture Overview

```
[Daily, per instrument]
   OHLCV + indicators + news
          ↓
   Compute regime features (deterministic)   ← ADX, ATR, range efficiency, MA200 slope
          ↓
   Claude classifies regime                  ← RegimeClassification
          ↓
   Smoothing layer (deterministic)           ← SmoothedRegimeState
          ↓
   Overlay checks (deterministic, parallel)  ← earnings (Finnhub + overrides),
                                               macro (manual), liquidity, data quality
          ↓
   Router                                    ← RoutingDecision
          ↓
[Per bar, per instrument]
   Selected strategy engine                  ← TripleConfirmation | MeanReversion | NoOp
          ↓
   Candidate Signal (or None)
          ↓
   Risk validator / order validator (existing, unchanged)
          ↓
   Broker adapter (existing, unchanged)
          ↓
   Position tagged with PositionMetadata at fill (one row per fill_id)
          ↓
[Exit management]
   Position's own entry_strategy handles exits (entry-regime exit contract)

[Independent surfaces]
   Web UI on nginx:8082 → calendar editor (macro + earnings overrides) → DB
   Scheduled tasks → macro calendar freshness alerts → Telegram
   CLI tool → bot recover-overlay <name> → instrument pause registry
```

Counterfactual shadow logging runs in parallel throughout. See §11.

---

## 3. Branch & Git Hygiene

1. `git checkout -b claude-strategy` from current `main`
2. **Merge gate** (required to merge to `main`):
   - All tests pass (existing 486 + new tests per §16)
   - `test_no_state_contamination.py` passes
   - 24-hour clean run on paper account in full shadow mode without any hard-degradation events
3. **Promotion gate** (required to flip any live flag):
   - 30 days of shadow data collected and reviewed per §17
   - Per-flag prerequisites in §6.3 satisfied
   - Manual review of dashboard counterfactual metrics
4. All new code under `bot/regime/`, `bot/strategies/`, `bot/overlays/`, `bot/shadow/`, `bot/calendar_ui/` — do NOT scatter new files
5. Commit granularity: one commit per numbered section of this spec where practical
6. PR title: `claude-strategy: Option C regime-aware routing with shadow mode (v3 spec)`

The merge gate exists to keep the code on `main` shippable. The promotion gate exists to keep live trading safe. Conflating them stalls the branch unnecessarily.

### 3.1 Tech debt log

Add `docs/TECH_DEBT.md` (new file) listing known compromises this PR is taking on:

- **Main loop in `bot/layer1.py`** — orchestration and signal logic are co-located in a file named `layer1.py`. The right structure is `bot/orchestrator.py` for orchestration and `bot/layer1.py` for signal logic only. This refactor is deferred to keep PR scope manageable. Plan to address in a follow-up PR after `claude-strategy` ships and stabilises.

Claude Code should add any further compromises it makes during implementation to this file, with rationale.

---

## 4. Directory Layout

```
bot/
  strategies/
    __init__.py
    base.py                          # StrategyEngine ABC (§8)
    triple_confirmation.py           # Wraps existing layer1 logic
    mean_reversion.py                # Skeleton only, disabled by default
    registry.py                      # Strategy factory
  regime/
    __init__.py
    features.py                      # Deterministic regime feature computation
    classifier.py                    # Claude-based daily classifier
    classifier_prompt.py             # Versioned system prompt
    classifier_schema.py             # Pydantic for Claude response
    smoothing.py                     # Persistence + hysteresis
    router.py                        # Regime → engine mapping
    models.py                        # RegimeClassification, SmoothedRegimeState,
                                     # RoutingDecision, PositionMetadata
    cache.py                         # SQLite cache for classifier calls
    cost_tracker.py                  # Token / $ tracking
  overlays/
    __init__.py
    base.py                          # Overlay ABC
    earnings_lockout.py              # Finnhub + manual overrides
    macro_lockout.py                 # Manual calendar (SQLite-backed)
    low_liquidity.py                 # Computes 20-day median from OHLCV
    data_quality.py
    precedence.py
    registry.py
    instrument_dependencies.py
    macro_calendar_monitor.py        # NEW v3: freshness alerts
  shadow/
    __init__.py
    simulator.py
    counterfactual_logger.py
    hypothetical_trades.py
  position_metadata/
    __init__.py
    tagger.py
    exit_policy.py
  degradation/
    __init__.py
    failure_tracker.py
    policies.py
    instrument_pause.py
  config/
    flags.py
  calendar_ui/                       # NEW v3: web UI for calendar editing
    __init__.py
    routes.py                        # HTTP route handlers
    store.py                         # SQLite-backed store (single source of truth)
    audit.py                         # Audit log writer
    validators.py                    # Input validation
    static/                          # HTML/CSS/JS for the editor page
      index.html
      app.js
      styles.css
specs/
  CLAUDE_STRATEGY_SPEC_v3.md
  prompts/
    classifier_v1.md
docs/
  regime_strategy.md
  shadow_mode.md
  degradation.md
  calendar_ui.md                     # NEW v3: how to use the editor
  TECH_DEBT.md                       # NEW v3
tests/
  regime/
    test_features.py
    test_classifier.py
    test_smoothing.py
    test_router.py
    test_models.py
    test_cache.py
  overlays/
    test_earnings_lockout.py
    test_macro_lockout.py
    test_low_liquidity.py
    test_data_quality.py
    test_precedence.py
    test_instrument_dependencies.py
    test_macro_calendar_monitor.py   # NEW v3
  strategies/
    test_base.py
    test_triple_confirmation.py
    test_mean_reversion.py
    test_registry.py
  shadow/
    test_simulator.py
    test_counterfactual_logger.py
    test_hypothetical_trades.py
    test_no_state_contamination.py
  position_metadata/
    test_tagger.py
    test_tagger_partial_fills.py
    test_exit_policy.py
  degradation/
    test_failure_tracker.py
    test_policies.py
    test_instrument_pause.py
  calendar_ui/                       # NEW v3
    test_store.py
    test_audit.py
    test_validators.py
    test_routes_macro.py             # HTTP API tests
    test_routes_earnings.py
    test_routes_csv_import.py
  invariants/                        # NEW v3 — pinning tests for spec rules
    test_routing_noop_never_allows.py
    test_overlay_hard_failure_semantics.py
    test_overlay_never_blocks_exits.py
    test_entry_regime_exit_contract.py
    test_cache_hit_field_present.py
    test_fill_id_composite_key.py
    test_no_redundant_flags.py
    test_mixed_strategy_fills_raises.py
    test_shadow_isolation.py
  regression/                        # NEW v3 — named per v2 fix
    test_v2_fix_redundant_flag_removed.py
    test_v2_fix_cache_hit_on_dataclass.py
    test_v2_fix_partial_fills_composite_key.py
    test_v2_fix_routing_unambiguous.py
    test_v2_fix_overlay_fail_safe.py
    test_v2_fix_overlay_independence.py
    test_v2_fix_exit_contract.py
    test_v2_fix_no_mixed_strategy_fills.py
  golden/                            # NEW v3 — behavioural equivalence
    test_classifier_golden.py        # Mocked Claude, fixed input → fixed output
    test_smoothing_golden.py
    test_router_golden.py
    test_overlay_golden.py
    test_shadow_simulator_golden.py
    fixtures/
      classifier/                    # Per-test fixture pairs (input + expected output)
      smoothing/
      router/
      overlay/
      shadow/
  integration/
    test_full_shadow_pipeline.py
    test_flag_combinations.py
    test_overlay_independence.py
    test_calendar_ui_end_to_end.py   # NEW v3
  property/                          # NEW v3 — Hypothesis-based
    test_smoothing_properties.py
    test_router_properties.py
    test_overlay_precedence_properties.py
  fixtures/
    sample_ohlcv_barc.csv
    sample_ohlcv_sgln.csv
    sample_news.json
    sample_regime_classifications.json
    sample_earnings_calendar.json
    sample_macro_calendar.json
    sample_partial_fills.json
    sample_finnhub_earnings_response.json   # NEW v3
ci/
  pre-commit-config.yaml             # NEW v3
  github-actions.yml                 # NEW v3 (or equivalent CI config)
  nightly_regression_replay.py       # NEW v3 (scaffolding, gated by env var)
```

---

## 5. Overall Design Rules

These are global rules that apply across every module. Violations are bugs.

1. **Default to safe.** Every flag defaults to off/shadow. A missing config key never accidentally enables live behaviour.
2. **No silent fallbacks.** Every degradation, every fallback, every error path emits a log line. Critical events emit Telegram alerts.
3. **Live ≠ shadow data.** Shadow system reads live data but writes only to `shadow_*` tables. Live code paths never read `shadow_*` tables. Enforced by test in §11.4.
4. **Shadow is async and killable.** Shadow never blocks live decisions. Bounded queues. Skip shadow work if it falls behind.
5. **Entry-regime exit contract.** A position's exit logic is determined by the engine that entered it, not by the current routed engine.
6. **Overlays gate entries only.** No overlay can block an exit. Exits are always allowed.
7. **Classifier errors default to previous smoothed regime.** Never to TRENDING by default.
8. **Startup prints full flag state** to stdout, logs, and Telegram.
9. **All timestamps are UTC.** No local time anywhere in the code. Config stores IANA timezone names when display needs local time.
10. **Version everything.** Prompts, schemas, configs. Log versions with every decision.
11. **Fail toward safety, never away from it.** Persistent failures in safety-critical components must produce *more* caution, not less. Hard-failure of a blocking component never auto-disables it.
12. **Every behavioural rule in this spec has at least one pinning test in `tests/invariants/`.** Reviewing a refactor means checking the invariant tests still pass.
13. **Calendar edits never bypass validation.** The store layer validates everything before persisting. The UI is a thin wrapper around the store; the store can be used directly (e.g., from a future import script) with the same guarantees.

---

## 6. Feature Flags

All flags live in `bot/config/flags.py` and are loaded from config. **Every flag defaults to off or shadow.** Config loader raises on unknown flag names (typo protection) and logs full flag state at startup.

### 6.1 Flag inventory

```yaml
feature_flags:
  # Classifier
  enable_classifier_shadow: true
  enable_classifier_live: false

  # Smoothing
  enable_persistence_shadow: true
  enable_persistence_live: false

  # Router
  enable_router_shadow: true
  enable_router_live: false

  # Overlays
  enable_event_overlays_shadow: true
  enable_event_overlays_live: false

  # Mean-reversion
  enable_mean_reversion_shadow: true
  enable_mean_reversion_live: false

  # Position metadata exit contract
  enable_position_tagged_exit_policy: false

  # Calendar UI (NEW v3)
  enable_calendar_ui: true            # UI is informational; safe to enable from day one

  # Global
  data_quality_strict_mode: false
```

**Removed in v2:** `enable_regime_block_no_trade` (redundant with router behaviour).

### 6.2 Flag semantics rules

- **Shadow flags can be true while corresponding live flag is false.** Shadow always runs first.
- **Live flag being true requires corresponding shadow flag to also be true.** Config loader enforces this.
- **`enable_router_live` requires `enable_classifier_live` and `enable_persistence_live`.**
- **`enable_mean_reversion_live` requires `enable_router_live`.**
- **`enable_event_overlays_live` does NOT require `enable_router_live`.** Overlays are an independent axis.
- **`enable_calendar_ui` has no dependencies.** Pure UI flag.
- **`data_quality_strict_mode` overrides individual overlay settings.**
- **Missing flag in config → safe default**, warning logged.

### 6.3 Dependency graph (enforced at startup)

```
enable_mean_reversion_live
  └── requires enable_router_live
        ├── requires enable_persistence_live
        │     └── requires enable_classifier_live
        │           └── requires enable_classifier_shadow
        ├── requires enable_persistence_shadow
        └── requires enable_router_shadow

enable_event_overlays_live
  └── requires enable_event_overlays_shadow

enable_calendar_ui
  └── (no dependencies)
```

If a live flag is set without its prerequisites, `flags.py` raises `ConfigError` at startup.

### 6.4 Startup logging

```
[STARTUP] CogniflowAI Trading Bot
Spec version: v3
Flags:
  classifier: shadow=true, live=false
  persistence: shadow=true, live=false
  router: shadow=true, live=false
  overlays: shadow=true, live=false
  mean_reversion: shadow=true, live=false
  position_tagged_exit: false
  calendar_ui: true
  data_quality_strict: false
Effective mode: SHADOW (no live routing; existing triple-confirmation path in use; overlays advisory only)
Config file: /home/vijay/trading/config.yaml (sha256: abc123...)
Calendar UI: enabled at https://<dashboard>/admin/calendars
Macro calendar status: 23 events remaining, farthest date 2027-03-15 (OK)
```

---

## 7. Core Dataclasses

Build these and their tests first, before any other module.

### 7.1 `RegimeClassification`

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Regime = Literal["TRENDING", "RANGING", "UNCLEAR"]

@dataclass(frozen=True)
class RegimeClassification:
    instrument: str
    classified_at: datetime
    trading_date: str
    raw_regime: Regime
    confidence: float
    rationale: str
    features: dict
    model_version: str
    prompt_version: str
    input_hash: str
    cache_hit: bool = False
```

### 7.2 `SmoothedRegimeState`

```python
@dataclass(frozen=True)
class SmoothedRegimeState:
    instrument: str
    effective_regime: Regime
    source_regime: Regime
    days_in_regime: int
    last_changed_at: datetime
    confidence: float
    pending_regime: Regime | None
    pending_days: int
    regime_history: list[Regime]
```

### 7.3 `RoutingDecision`

```python
@dataclass(frozen=True)
class RoutingDecision:
    instrument: str
    decided_at: datetime
    effective_regime: Regime
    selected_engine: str
    allow_new_entries: bool
    active_overlays: list[str]
    overlay_expires_at: datetime | None
    block_reason: str | None
    flag_snapshot: dict
```

### 7.4 `PositionMetadata`

```python
@dataclass(frozen=True)
class PositionMetadata:
    position_id: str
    fill_id: str
    instrument: str
    entry_time: datetime
    entry_price: float
    entry_quantity: float
    entry_strategy: str
    entry_regime: Regime
    entry_overlays_active: list[str]
    entry_prompt_version: str | None
    exit_policy: Literal["use_entry_strategy_rules"]
```

Aggregate position quantity = `SUM(entry_quantity) WHERE position_id = ?`.

### 7.5 Calendar event dataclasses (NEW v3)

```python
@dataclass(frozen=True)
class MacroEvent:
    id: int                          # SQLite rowid
    date: str                        # ISO YYYY-MM-DD
    time_utc: str                    # HH:MM
    event: str                       # "FOMC_RATE_DECISION", "BOE_RATE_DECISION", etc.
    region: str                      # "US", "UK", "EU", "GLOBAL"
    severity: Literal["high", "medium", "low"]
    notes: str

@dataclass(frozen=True)
class EarningsEvent:
    id: int
    instrument: str
    date: str
    time_utc: str | None             # null if unknown
    source: Literal["finnhub", "manual_override", "manual_addition"]
    finnhub_original_date: str | None  # set when source=="manual_override"
    notes: str
```

The `source` field is critical: it tells the overlay code whether this row is from Finnhub (which gets refreshed regularly), a manual override (which wins over Finnhub for that ticker+date), or a manual addition (which Finnhub doesn't know about).

---

## 8. StrategyEngine ABC

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, Optional
import pandas as pd

Action = Literal["BUY", "SELL", "HOLD", "CLOSE"]

@dataclass
class MarketState:
    symbol: str
    bar_time: pd.Timestamp
    ohlcv: pd.DataFrame
    indicators: dict
    open_position: Optional[dict]
    recent_trades: list[dict]
    news: list[dict]
    account: dict
    regime: Optional[SmoothedRegimeState] = None

@dataclass
class Signal:
    action: Action
    confidence: float
    size_hint: Optional[float]
    stop_hint: Optional[float]
    reason: str
    raw: dict

@dataclass
class ExitDecision:
    action: Literal["HOLD", "CLOSE", "ADJUST_STOP"]
    new_stop: Optional[float]
    reason: str

class StrategyEngine(ABC):
    name: str

    @abstractmethod
    def generate_candidate(self, state: MarketState) -> Optional[Signal]: ...

    @abstractmethod
    def manage_exit(self, position: PositionMetadata, state: MarketState) -> ExitDecision: ...
```

### 8.1 `TripleConfirmationEngine`
Thin wrapper around existing `bot/layer1.py` logic. **Do not rewrite indicator logic.** Wrap existing functions; if existing code lacks coherent functions, extract minimum-viable adapters without changing semantics. Behavioural equivalence verified by golden file test (§16.3) producing bit-identical trade lists on fixture data.

### 8.2 `MeanReversionEngine` (skeleton)
```python
class MeanReversionEngine(StrategyEngine):
    name = "MeanReversionEngine"

    def generate_candidate(self, state):
        # Placeholder:
        # - Bollinger Bands (20, 2.0), RSI(14)
        # - BUY if close < lower band AND RSI < 30
        # - Long-only
        # UNVALIDATED — gated by enable_mean_reversion_live
        ...

    def manage_exit(self, position, state):
        # CLOSE at middle band or max_hold_days
        ...
```

### 8.3 `NoOpEngine`
```python
class NoOpEngine(StrategyEngine):
    name = "NoOpEngine"

    def generate_candidate(self, state): return None
    def manage_exit(self, position, state):
        return ExitDecision("HOLD", None, "NoOpEngine manages nothing")
```

### 8.4 Registry
```python
_REGISTRY = {
    "TripleConfirmationEngine": TripleConfirmationEngine,
    "MeanReversionEngine": MeanReversionEngine,
    "NoOpEngine": NoOpEngine,
}

def get_engine(name: str) -> StrategyEngine:
    if name not in _REGISTRY:
        raise ValueError(f"Unknown engine: {name}. Known: {list(_REGISTRY)}")
    return _REGISTRY[name]()
```

---

## 9. Regime Layer

### 9.1 Features (`regime/features.py`)
Deterministic features:
- `adx_14`, `atr_14`, `atr_pct`
- `ma_200_slope_pct_per_day` (linear regression over last 20 bars of MA200)
- `range_efficiency` (net price change / sum of bar ranges, last 20 bars)
- `realized_volatility_20d`
- `close_above_ma200`, `distance_to_ma200_pct`

### 9.2 Classifier (`regime/classifier.py`)

Runs **once per day per instrument**, after market close.

Responsibilities:
1. Compute features
2. Compute `input_hash` = sha256(features + prompt_version + model_version)
3. Check cache; cache hit → return cached with `cache_hit=True`
4. Check daily budget; exceeded → fallback
5. Build prompt
6. Call Anthropic API via existing `bot/llm/` abstraction. **Match `sentiment.py` pattern: tool-use for structured JSON output.**
7. Parse response
8. Log to cost tracker
9. Return classification (`cache_hit=False`)

**Failure modes (all return fallback UNCLEAR with confidence=0.0):**
- API timeout, HTTP 429/5xx (after 3 retries), JSON parse failure, schema validation failure, budget exhausted, invalid regime value

### 9.3 Smoothing (`regime/smoothing.py`)

Pure deterministic. No API calls.

**Rules:**
1. `confidence >= 0.85` and matches `effective_regime` → update, increment `days_in_regime`
2. Differs from `effective_regime` and `confidence >= 0.70`:
   - If `pending_regime == new.raw_regime`: increment `pending_days`; if ≥ 2, promote
   - Else: set `pending_regime = new.raw_regime`, `pending_days = 1`
3. `confidence < 0.70` → retain current state
4. Fallback (`confidence == 0.0`) → retain entirely, warn
5. TRENDING→RANGING requires `confidence >= 0.75` (hysteresis)
6. `regime_history` bounded to last 10

First run: seed `effective_regime = UNCLEAR`, `days_in_regime = 0`. First high-confidence classification promotes on day 1.

### 9.4 Cache (`regime/cache.py`)

```sql
CREATE TABLE IF NOT EXISTS regime_classification_cache (
  instrument     TEXT NOT NULL,
  trading_date   TEXT NOT NULL,
  input_hash     TEXT NOT NULL,
  classification_json TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  PRIMARY KEY (instrument, trading_date, input_hash)
);
```

Cache hit → return with `cache_hit=True` on the dataclass.

### 9.5 Cost tracker

```sql
CREATE TABLE IF NOT EXISTS regime_classification_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  instrument TEXT NOT NULL,
  trading_date TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cost_usd REAL,
  latency_ms INTEGER,
  raw_regime TEXT,
  confidence REAL,
  cache_hit INTEGER DEFAULT 0,
  error TEXT
);
```

Default `max_daily_cost_usd = 5.0`.

**Pricing**: Look up `claude-sonnet-4-6` rates from `https://docs.claude.com` at implementation time. Note source URL and date in `cost_tracker.py` comment. **Do not hardcode from training data.**

### 9.6 Prompt (`regime/classifier_prompt.py`)

`CLASSIFIER_PROMPT_V1` constant. Required content:
- Role
- JSON-only output contract
- TRENDING / RANGING / UNCLEAR definitions
- Confidence calibration reminder
- "TRENDING is not the default; UNCLEAR is acceptable"
- No downstream-strategy mention (avoid target leakage)

Mirror to `specs/prompts/classifier_v1.md` for human review. Sync verified by test.

### 9.7 Schema

```python
class ClassifierResponseSchema(BaseModel):
    regime: Literal["TRENDING", "RANGING", "UNCLEAR"]
    confidence: confloat(ge=0.0, le=1.0)
    rationale: str = Field(max_length=280)
    key_features: list[str] = Field(default_factory=list, max_length=5)
```

### 9.8 Router (v2 unambiguous table)

| Effective regime | Overlays? | mean_reversion_live | Selected engine | allow_new_entries | block_reason |
|---|---|---|---|---|---|
| TRENDING | No | — | TripleConfirmationEngine | True | None |
| TRENDING | Yes | — | TripleConfirmationEngine | False | "Overlay active: …" |
| RANGING | No | True | MeanReversionEngine | True | None |
| RANGING | No | False | NoOpEngine | False | "Mean-reversion not enabled live" |
| RANGING | Yes | True | MeanReversionEngine | False | "Overlay active: …" |
| RANGING | Yes | False | NoOpEngine | False | "Mean-reversion not enabled live; overlay also active: …" |
| UNCLEAR | — | — | NoOpEngine | False | "Regime UNCLEAR" |

**Invariant**: `selected_engine == "NoOpEngine"` ⇒ `allow_new_entries == False`. Pinned by `tests/invariants/test_routing_noop_never_allows.py`.

---

## 10. Overlays

### 10.1 Design
Deterministic. Pure function `(instrument, timestamp, context) → OverlayCheck`:

```python
@dataclass(frozen=True)
class OverlayCheck:
    overlay_name: str
    is_active: bool
    expires_at: datetime | None
    reason: str
```

### 10.2 Overlays

**`earnings_lockout.py`** (uses Finnhub + manual overrides/additions from calendar DB)
- Resolution order: manual_override > finnhub > manual_addition
- Active: from close of bar-day −1 through end of bar-day +1 relative to earnings date
- `data_quality_strict_mode`: −2 through +2
- Earnings dates refreshed from Finnhub daily; manual entries persist independently

**`macro_lockout.py`** (uses manual macro calendar from DB, edited via §15.4 UI)
- Active: from 2 hours before event through close of event day
- `data_quality_strict_mode`: entire event day

**`low_liquidity.py`** (computed by bot from OHLCV)
- Bot computes 20-day median volume per instrument per time-of-day bucket (15-min buckets recommended)
- Cached in DB, refreshed nightly
- Active if today's cumulative volume at decision time < 40% of median for that bucket
- `data_quality_strict_mode`: threshold → 60%

**`data_quality.py`**
- Active if: latest bar stale > N minutes, gap > 15% without news, OHLCV sanity check failures
- Short-circuits other overlays and classification
- `data_quality_strict_mode`: halves N

### 10.3 Precedence
1. `DATA_QUALITY` → short-circuit, NoOpEngine, no trading
2. `LOW_LIQUIDITY` → classify, but `allow_new_entries=False`
3. `EARNINGS_LOCKOUT`, `MACRO_LOCKOUT` → classify, `allow_new_entries=False`, exits continue
4. None active → route per §9.8

### 10.4 Registry
```python
def active_overlays(instrument, now, ctx) -> list[OverlayCheck]:
    results = []
    for overlay in [DataQuality, EarningsLockout, MacroLockout, LowLiquidity]:
        check = overlay.check(instrument, now, ctx)
        if check.is_active:
            results.append(check)
    return results
```

### 10.5 Asymmetry rule
Entries gated by overlays. Exits NEVER blocked by overlays. Test: `tests/invariants/test_overlay_never_blocks_exits.py`.

### 10.6 Instrument dependencies
```python
def instruments_affected_by_overlay(overlay_name: str) -> list[str]:
    """Instruments whose entries depend on this overlay being functional."""
```
- `EARNINGS_LOCKOUT`: individual stocks (ETFs opt out via config)
- `MACRO_LOCKOUT`: all instruments
- `LOW_LIQUIDITY`: all instruments (per-instrument profile)
- `DATA_QUALITY`: all instruments

### 10.7 Macro calendar maintenance alerts (NEW v3)

`bot/overlays/macro_calendar_monitor.py` runs scheduled checks against the macro calendar (stored per §15.4) and emits Telegram alerts to existing `CogniflowAI_Trading_Alerts_Bot`.

#### Thresholds
- `LOW_THRESHOLD_DAYS = 30` — warn below this
- `CRITICAL_THRESHOLD_DAYS = 14` — critical below this

#### Status function
```python
def check_calendar_freshness() -> dict:
    """
    Returns:
      {
        "severity": "OK" | "LOW" | "CRITICAL" | "EMPTY" | "MISSING",
        "events_remaining": int,
        "farthest_date": str | None,
        "days_of_coverage": int,
        "message": str
      }
    """
```

Reads from calendar store (per §15.4 Claude Code's storage choice). Returns:
- `MISSING` if store is unavailable
- `EMPTY` if no future events
- `CRITICAL` if `days_of_coverage < 14`
- `LOW` if `days_of_coverage < 30`
- `OK` otherwise

#### Scheduled tasks (systemd timers or cron)
```
# Weekly freshness check, Sundays 10:00 UTC
0 10 * * 0  python -m bot.overlays.macro_calendar_monitor --check

# Monthly status report, 1st of month 09:00 UTC
0 9 1 * *   python -m bot.overlays.macro_calendar_monitor --monthly-report
```

Alert severities:
- `CRITICAL`, `EMPTY`, `MISSING` → Telegram critical alert
- `LOW` → Telegram warning
- `OK` → suppressed on weekly check; included in monthly report

Monthly report sent unconditionally with current status + links to source pages:
- Fed: `https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm`
- BoE: `https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates`
- ECB: `https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html`
- BLS: `https://www.bls.gov/schedule/news_release/`

#### Startup check
On bot boot, if `enable_event_overlays_shadow` OR `enable_event_overlays_live`:
```python
status = check_calendar_freshness()
if status["severity"] in ("MISSING", "EMPTY"):
    telegram.send_critical(f"BOT START: {status['message']}")
    log.error(f"Macro calendar issue at startup: {status['message']}")
# Always include status in startup log line (§6.4)
```

Bot does not refuse to start on missing/empty calendar (might be the operator's first boot and they haven't populated it yet), but startup is loud about it.

#### Alert idempotency
Each alert severity is rate-limited to one Telegram message per 24 hours. Prevents spam if the weekly check runs and triggers, then a config reload runs the check again.

---

## 11. Shadow Mode & Counterfactual Logging

### 11.1 Purpose
Answer via SQL:
1. Did the classifier/router **avoid losers**?
2. Did the no-trade regime **suppress bad environments**?
3. Did mean-reversion **add value**?

### 11.2 Schema
```sql
CREATE TABLE IF NOT EXISTS shadow_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  instrument TEXT NOT NULL,
  bar_time TEXT NOT NULL,
  live_engine TEXT,
  live_signal_json TEXT,
  live_action_taken TEXT,
  live_trade_id TEXT,
  shadow_regime TEXT,
  shadow_confidence REAL,
  shadow_smoothed_regime TEXT,
  shadow_smoothed_days_in_regime INTEGER,
  shadow_overlays_active TEXT,
  shadow_engine_selected TEXT,
  shadow_signal_json TEXT,
  shadow_action_would_be TEXT,
  disagreement_type TEXT,
  hypothetical_trade_id TEXT,
  flag_snapshot_json TEXT
);

CREATE TABLE IF NOT EXISTS shadow_hypothetical_trades (
  id TEXT PRIMARY KEY,
  instrument TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  opened_bar TEXT NOT NULL,
  entry_engine TEXT NOT NULL,
  entry_regime TEXT,
  entry_price REAL NOT NULL,
  entry_quantity REAL NOT NULL,
  entry_stop REAL,
  closed_at TEXT,
  closed_bar TEXT,
  exit_price REAL,
  exit_reason TEXT,
  pnl REAL,
  pnl_pct REAL,
  status TEXT NOT NULL
);
```

### 11.3 ShadowPositionSimulator
Mirrors real position manager on simulated fills. Applies entry engine's `manage_exit` against real bars. Marks ABANDONED if delisted, data stale, or DATA_QUALITY active > 5 bars. Honours historical overlay state.

### 11.4 No-contamination test (CI-critical)
```python
def test_shadow_never_writes_to_live_tables():
    """Run full pipeline in shadow mode for 24 simulated hours.
       Assert: zero rows in live tables unless live path produced them.
       Assert: live paths never read from shadow_* tables."""
```

### 11.5 Async execution
Separate asyncio task, bounded queue (max 100). Drop oldest on overflow, emit `shadow_lag` counter, Telegram warn if exceeded. Never blocks live loop.

---

## 12. Position Metadata & Exit Contract

### 12.1 Tagging
On every fill:
1. Construct `PositionMetadata` from signal + routing + fill, with `fill_id` = broker execution ID (preferred) or `f"{position_id}-{seq:04d}"` fallback
2. Persist:

```sql
CREATE TABLE IF NOT EXISTS position_metadata (
  position_id TEXT NOT NULL,
  fill_id TEXT NOT NULL,
  instrument TEXT NOT NULL,
  entry_time TEXT NOT NULL,
  entry_price REAL NOT NULL,
  entry_quantity REAL NOT NULL,
  entry_strategy TEXT NOT NULL,
  entry_regime TEXT,
  entry_overlays_active TEXT,
  entry_prompt_version TEXT,
  exit_policy TEXT NOT NULL,
  PRIMARY KEY (position_id, fill_id)
);

CREATE INDEX IF NOT EXISTS idx_position_metadata_position
  ON position_metadata(position_id);
```

3. Attach to in-memory position state.

**Partial fills**: one row per fill, aggregate by `SUM(entry_quantity)`.

**fill_id sourcing**:
- IBKR: use execution ID from fill event
- IG: use execution ID from fill event
- Either broker not supplying execution ID: fall back to `f"{position_id}-{seq:04d}"` monotonic counter
- Composite PK enforces uniqueness

### 12.2 Exit policy enforcement
```python
def get_exit_engine(rows: list[PositionMetadata]) -> StrategyEngine:
    strategies = {m.entry_strategy for m in rows}
    assert len(strategies) == 1, \
        f"Position has fills tagged with multiple strategies: {strategies}"
    policies = {m.exit_policy for m in rows}
    assert policies == {"use_entry_strategy_rules"}, \
        f"Unknown or mixed exit_policy: {policies}"
    return get_engine(strategies.pop())

def compute_exit(rows, state) -> ExitDecision:
    engine = get_exit_engine(rows)
    return engine.manage_exit(aggregate_position(rows), state)
```

### 12.3 Flag behaviour
- `enable_position_tagged_exit_policy=False`: positions tagged but exit uses legacy path
- `True`: exit dispatches via `get_exit_engine`

---

## 13. Degradation Policy

### 13.1 Two levels
**Soft**: log, hourly batched Telegram, fallback for this invocation, track in failure_tracker.
**Hard**: critical Telegram immediately, component-specific safe fallback, requires human recovery.

### 13.2 Thresholds (configurable defaults)
| Component | Soft | Hard |
|---|---|---|
| Classifier API | single failure | 5 consecutive OR 50% over 1 hour |
| Router exception | single failure | 3 consecutive |
| Any overlay check | single failure | 5 consecutive for same overlay |
| Mean-reversion engine | single failure | 3 consecutive |
| DB logging write | single failure | 3 consecutive → pause trades |
| Shadow simulator | single failure | Never hard-degrades |
| Calendar UI write | single failure | 3 consecutive (UI returns 500, no trading impact) |

### 13.3 Per-component fallback (v2 fail-toward-safety)
| Component | Soft | Hard |
|---|---|---|
| Classifier | Use last `SmoothedRegimeState` | Disable `enable_classifier_live`, router uses frozen state, critical alert |
| Router | Default `NoOpEngine`, `allow_new_entries=False` | Disable `enable_router_live`, revert to legacy path |
| Overlay (individual) | Treat as active (stricter), log | **Pause new entries on all instruments returned by `instruments_affected_by_overlay(name)`. Flag NOT auto-disabled. Recovery via `bot recover-overlay <name>` CLI** |
| Mean-reversion | Return no candidate | Disable `enable_mean_reversion_live` |
| DB logging | Retry once, in-memory queue | Pause new trades, existing positions continue |
| Calendar UI write | Return 500 to client | No trading impact (UI is informational); critical alert about DB write failures |

### 13.4 Instrument pause registry
```python
class InstrumentPauseRegistry:
    def pause(self, instrument: str, reason: str, paused_by_overlay: str) -> None: ...
    def is_paused(self, instrument: str) -> bool: ...
    def pause_reason(self, instrument: str) -> str | None: ...
    def clear(self, instrument: str, cleared_by: str) -> None: ...
    def list_paused(self) -> list[tuple[str, str, str]]: ...
```

```sql
CREATE TABLE IF NOT EXISTS instrument_entry_pauses (
  instrument TEXT PRIMARY KEY,
  paused_at TEXT NOT NULL,
  paused_by_overlay TEXT NOT NULL,
  reason TEXT NOT NULL,
  cleared_at TEXT,
  cleared_by TEXT
);
```

Persists across restarts.

### 13.5 No silent fallback rule
Soft → INFO log. Hard → ERROR + critical Telegram + `degradation_events` row.

```sql
CREATE TABLE IF NOT EXISTS degradation_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  component TEXT NOT NULL,
  severity TEXT NOT NULL,
  trigger_reason TEXT NOT NULL,
  action_taken TEXT NOT NULL,
  flag_disabled TEXT,
  instruments_paused TEXT,
  recovery_instructions TEXT
);
```

### 13.6 Recovery
- Flag hard-disable: edit config, restart, startup logs new state
- Instrument pause: `bot recover-overlay <name>` runs overlay self-test, clears pauses on success, logs `cleared_by`

CLI command:
```
$ bot recover-overlay EARNINGS_LOCKOUT
Running self-test on EARNINGS_LOCKOUT...
Self-test passed.
Cleared 3 instrument pauses: BARC.L, HSBA.L, LLOY.L
Audit row written.
```

No auto-recovery anywhere.

---

## 14. Integration into Main Loop (v2 decoupled gates)

```python
# Daily post-close (§9.2: classifier runs after market close)
for instrument in instruments:
    if flags.enable_classifier_shadow:
        classification = classifier.classify(instrument)
        log_classification(classification)
    smoothed = smoothing.update(instrument, classification, prior_state)
    if flags.enable_persistence_shadow:
        log_smoothed_state(smoothed)

# Per bar
for bar in bars:
    for instrument in instruments:
        overlays = active_overlays(instrument, bar.time, context)
        routing = router.route(smoothed[instrument], overlays, flags)
        log_routing_shadow(routing)

        # Engine selection
        if flags.enable_router_live:
            engine = get_engine(routing.selected_engine)
        else:
            engine = get_engine("TripleConfirmationEngine")

        # Entry permission (decoupled v2)
        if instrument_pause_registry.is_paused(instrument):
            allow_entries = False
            block_reason = f"Instrument paused: {instrument_pause_registry.pause_reason(instrument)}"
        elif flags.enable_event_overlays_live and overlays:
            allow_entries = False
            block_reason = f"Overlay active: {[o.overlay_name for o in overlays]}"
        elif flags.enable_router_live:
            allow_entries = routing.allow_new_entries
            block_reason = routing.block_reason
        else:
            allow_entries = True
            block_reason = None

        state = build_market_state(instrument, bar, smoothed[instrument])
        candidate = engine.generate_candidate(state)

        shadow_pipeline.evaluate(instrument, bar, smoothed, overlays, candidate)

        if candidate and candidate.action in ("BUY", "SELL"):
            if not allow_entries:
                log_blocked_entry(candidate, block_reason)
                continue
            validate_and_order(candidate, routing)
        elif candidate and candidate.action == "CLOSE":
            validate_and_order(candidate, routing)

        for position in open_positions(instrument):
            if flags.enable_position_tagged_exit_policy:
                exit_decision = compute_exit(position.metadata_rows, state)
            else:
                exit_decision = legacy_exit_logic(position, state)
            if exit_decision.action == "CLOSE":
                validate_and_order_close(position, exit_decision)
```

### 14.1 Entry gate independence (v2 design)
Three independent gates: instrument pause, overlay-active (gated by `enable_event_overlays_live`), router (gated by `enable_router_live`). Overlays can bite without router live. `test_overlay_independence.py` enforces.

### 14.2 Test matrix for entry gates
| router_live | overlays_live | overlays present | regime UNCLEAR | instrument paused | Expected |
|---|---|---|---|---|---|
| F | F | — | — | F | True |
| F | T | T | — | F | False (overlay) |
| F | T | F | — | F | True |
| T | F | — | T | F | False (router) |
| T | T | T | F | F | False (overlay) |
| T | T | F | T | F | False (router) |
| T | T | F | F | F | True |
| — | — | — | — | T | False (pause) |

---

## 15. Observability

### 15.1 Dashboard tabs (nginx on 8082, JWT-auth)
- **Regime**, **Overlays**, **Routing**, **Shadow vs Live**, **Degradation events**, **Instrument pauses**

### 15.2 Telegram alerts (via `CogniflowAI_Trading_Alerts_Bot`)
- Bot start with flag state
- Component hard-degrade
- DB logging paused
- Calendar UI write failures (3 consecutive)
- Macro calendar maintenance alerts (§10.7)
- Daily summary at UK market close

### 15.3 Log files (daily rotated, 90 days retention)
- `logs/regime/`, `logs/routing/`, `logs/shadow/`, `logs/degradation/`, `logs/pauses/`, `logs/calendar_edits/` (NEW v3)

### 15.4 Calendar Editor UI (NEW v3)

Route: `/admin/calendars` on the existing nginx-served dashboard. Behind existing JWT auth middleware.

#### 15.4.1 Storage decision

**Claude Code decides** between two options based on which is simpler given the existing codebase:

- **Option A**: SQLite as single source of truth. Overlay code queries SQLite directly. UI reads/writes SQLite.
- **Option B**: SQLite as source of truth, with periodic regeneration of a JSON cache file. Overlay code reads JSON (faster cold reads). UI writes SQLite, then triggers cache regeneration.

Recommendation: Option A unless profiling shows SQLite reads are a bottleneck for overlay checks (very unlikely given the volumes). Option A removes the cache-invalidation class of bugs entirely.

Note the decision in `docs/calendar_ui.md`. Both options must:
- Use the existing SQLite database (no new DB file)
- Survive bot restarts
- Be atomic on writes (UI write must not partially apply)

#### 15.4.2 Database schema

```sql
CREATE TABLE IF NOT EXISTS macro_calendar (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,                  -- ISO YYYY-MM-DD
  time_utc TEXT NOT NULL,              -- HH:MM
  event TEXT NOT NULL,                 -- e.g. "FOMC_RATE_DECISION"
  region TEXT NOT NULL,                -- "US", "UK", "EU", "GLOBAL"
  severity TEXT NOT NULL,              -- "high", "medium", "low"
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (date, time_utc, event, region)
);

CREATE INDEX idx_macro_calendar_date ON macro_calendar(date);

CREATE TABLE IF NOT EXISTS earnings_calendar (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  instrument TEXT NOT NULL,
  date TEXT NOT NULL,
  time_utc TEXT,                       -- nullable
  source TEXT NOT NULL,                -- 'finnhub' | 'manual_override' | 'manual_addition'
  finnhub_original_date TEXT,          -- set when source='manual_override'
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (instrument, date, source)
);

CREATE INDEX idx_earnings_calendar_instrument_date
  ON earnings_calendar(instrument, date);

CREATE TABLE IF NOT EXISTS calendar_edit_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  user_jwt_sub TEXT NOT NULL,          -- JWT subject claim
  calendar_type TEXT NOT NULL,         -- 'macro' | 'earnings'
  action TEXT NOT NULL,                -- 'create' | 'update' | 'delete' | 'bulk_import' | 'bulk_delete'
  target_id INTEGER,                   -- rowid of affected row, NULL for bulk
  before_json TEXT,                    -- pre-change state, NULL on create
  after_json TEXT,                     -- post-change state, NULL on delete
  affected_count INTEGER,              -- for bulk operations
  notes TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_audit_ts ON calendar_edit_audit(ts);
```

Audit row is written in the **same transaction** as the calendar change. If the change rolls back, the audit row rolls back too.

#### 15.4.3 Earnings resolution semantics

When the overlay queries earnings for instrument `X` on date `D`:

1. Look for `(instrument=X, date=D, source='manual_override')` — if exists, this is the canonical date for this earnings event. Use it.
2. Look for `(instrument=X, date=D, source='manual_addition')` — if exists, use it.
3. Look for `(instrument=X, date=D, source='finnhub')` — if exists, use it.

Manual overrides win over Finnhub. Manual additions cover tickers Finnhub doesn't have. Finnhub is the bulk source for everything else.

When Finnhub refreshes (daily background job), it:
- Inserts new `(instrument, date, source='finnhub')` rows it didn't have before
- Updates existing `finnhub` rows if Finnhub now reports a different date (rare but happens)
- Does NOT touch `manual_override` or `manual_addition` rows

Manual overrides become stale if Finnhub's reported date changes. Surface this in the UI: when displaying an earnings row, if `source='manual_override'` and the latest Finnhub row for that instrument has a different date, show a "Finnhub now reports X" indicator. The operator can either delete the override (let Finnhub take over) or update the override (acknowledging the new Finnhub date).

#### 15.4.4 HTTP API

All routes under `/api/calendar/`. All behind existing JWT middleware. All return JSON.

**Macro endpoints:**
```
GET    /api/calendar/macro                 → list all events (queryable by date range)
GET    /api/calendar/macro/<id>            → single event
POST   /api/calendar/macro                 → create event (validated body)
PUT    /api/calendar/macro/<id>            → update event (validated body)
DELETE /api/calendar/macro/<id>            → delete event (requires X-Confirm-Event header matching event name)
POST   /api/calendar/macro/import          → bulk import from CSV (multipart/form-data)
DELETE /api/calendar/macro/bulk            → bulk delete (requires X-Confirm-Count header matching count)
```

**Earnings endpoints:**
```
GET    /api/calendar/earnings              → list (queryable by instrument, date range)
GET    /api/calendar/earnings/<id>         → single event
POST   /api/calendar/earnings              → create manual_addition or manual_override
PUT    /api/calendar/earnings/<id>         → update (only manual_* rows; finnhub rows are read-only via UI)
DELETE /api/calendar/earnings/<id>         → delete (only manual_* rows; requires X-Confirm-Event header)
POST   /api/calendar/earnings/import       → bulk import CSV (creates manual_addition rows)
```

**Audit endpoint:**
```
GET    /api/calendar/audit                 → paginated audit log, filterable by calendar_type, action, ts range
```

#### 15.4.5 Validation rules

Macro event:
- `date` matches `YYYY-MM-DD`, parses as valid date, is in the future (warn but allow past dates for backfill)
- `time_utc` matches `HH:MM` between `00:00` and `23:59`
- `event` non-empty, max 64 chars, matches `[A-Z][A-Z0-9_]*`
- `region` in `{"US", "UK", "EU", "GLOBAL"}`
- `severity` in `{"high", "medium", "low"}`
- `notes` max 500 chars
- Uniqueness on `(date, time_utc, event, region)` — duplicate returns 409 Conflict

Earnings event:
- `instrument` non-empty, matches a known instrument from `instruments.json` or `instruments_ig.json` (warn if not found but allow — instrument lists change)
- `date` matches `YYYY-MM-DD`, parses as valid date
- `time_utc` nullable, if present matches `HH:MM`
- `source` in `{"manual_override", "manual_addition"}` for POST/PUT (UI cannot create `finnhub` rows)
- For `source='manual_override'`: `finnhub_original_date` must be supplied and match the current Finnhub row's date for that instrument
- `notes` max 500 chars

Validation errors return 400 with body:
```json
{
  "error": "validation_failed",
  "field": "date",
  "message": "Expected YYYY-MM-DD, got '2026/04/30'"
}
```

#### 15.4.6 Confirmation modal semantics

The UI implements destructive-action confirmation client-side. Server-side enforcement uses HTTP headers:

- `DELETE /api/calendar/macro/<id>` requires header `X-Confirm-Event: <event_name>` matching the event being deleted. Missing or mismatched → 400.
- `DELETE /api/calendar/macro/bulk` requires header `X-Confirm-Count: <N>` matching the count to be deleted. Missing or mismatched → 400.
- `DELETE /api/calendar/earnings/<id>` requires header `X-Confirm-Event: <instrument>:<date>` matching the row. Missing or mismatched → 400.

PUT (update) does NOT require confirmation header. Edits are recoverable from audit; deletes are harder to notice retroactively.

POST (create) does NOT require confirmation. Adding a wrong entry is recoverable.

#### 15.4.7 CSV import format

Macro CSV:
```
date,time_utc,event,region,severity,notes
2026-06-18,18:00,FOMC_RATE_DECISION,US,high,Powell press conference
2026-06-20,11:00,BOE_RATE_DECISION,UK,high,
```

Earnings CSV (always imports as `source='manual_addition'`):
```
instrument,date,time_utc,notes
BARC.L,2026-07-30,06:00,Q2 earnings
```

Import behaviour:
- Per-row validation; valid rows insert, invalid rows reported in response
- Idempotent: if a row matches the uniqueness constraint, skip with reason
- Transaction-wrapped: any DB error rolls back the entire import
- Returns:
```json
{
  "inserted": 23,
  "skipped_duplicates": 5,
  "validation_errors": [
    {"row": 7, "field": "date", "message": "..."}
  ]
}
```

#### 15.4.8 UI page

Static HTML/CSS/JS in `bot/calendar_ui/static/`. Simple table layout per the user's preference. No SPA framework; vanilla JS or htmx to keep dependencies low.

Top of page: tabs for "Macro Calendar" / "Earnings Calendar" / "Audit Log".

Each tab:
- Filter controls (date range, region/instrument)
- Table of events with inline Edit / Delete buttons
- "Add Event" button → modal form
- "Import CSV" button → file upload
- Sidebar: "Recent Changes" showing last 20 audit rows

Delete confirmation: modal asks user to type the event name (or count, for bulk). Submit disabled until match.

Earnings tab additionally shows `source` column and "stale override" indicator per §15.4.3.

Audit tab: paginated table, filterable by calendar type / action / date range. Each row shows before/after JSON in expandable panels for forensic review.

#### 15.4.9 Background tasks

A nightly job (cron or systemd timer) refreshes Finnhub earnings data:
```
30 22 * * *  python -m bot.overlays.earnings_finnhub_sync
```
- Pulls earnings dates for all instruments in `instruments.json` ∪ `instruments_ig.json` from Finnhub
- Inserts new rows
- Updates existing `finnhub` rows when Finnhub reports a different date
- Logs to `logs/calendar_edits/` and writes audit row with `action='finnhub_sync'`
- Telegram alert on hard-failure (3 consecutive failures)

---

## 16. Testing Requirements (expanded in v3)

Testing is structured to **prevent regressions**, not just provide coverage. Every behavioural rule in this spec has at least one pinning test in `tests/invariants/` or `tests/regression/`.

### 16.1 Unit tests (function-level, per module)

Every public function in every new module has unit tests. Coverage target: 85% line coverage on new code, measured by `pytest --cov`. Tests use mocks for external dependencies (LLM, broker, Finnhub).

Required test files per §4 directory layout.

### 16.2 Invariant tests (`tests/invariants/`)

Tests that codify "must always be true" rules. Stand alone in their own directory so reviewers can scan them quickly.

Required:
- `test_routing_noop_never_allows.py` — for every input combination producing `NoOpEngine`, assert `allow_new_entries == False`
- `test_overlay_hard_failure_semantics.py` — overlay hard-failure pauses instruments, does NOT auto-disable flag
- `test_overlay_never_blocks_exits.py` — all overlays active + open position + exit signal → exit executes
- `test_entry_regime_exit_contract.py` — position tagged with engine X exits via engine X regardless of current routing
- `test_cache_hit_field_present.py` — `RegimeClassification.cache_hit` field exists; removing it should fail this test
- `test_fill_id_composite_key.py` — `position_metadata` composite PK `(position_id, fill_id)`; reverting to PK on `position_id` alone should fail
- `test_no_redundant_flags.py` — config rejects `enable_regime_block_no_trade` flag name (typo protection enforces unknown flag rejection)
- `test_mixed_strategy_fills_raises.py` — position with fills tagged with two different engines raises on exit
- `test_shadow_isolation.py` — shadow code paths do not write to live tables; live code paths do not read from shadow tables (mock-spy based)

Where possible, invariants are *also* enforced at runtime via `assert` statements in production code. The test verifies the assertion fires.

### 16.3 Behavioural equivalence tests (`tests/golden/`)

Golden file tests: fixed input → fixed expected output, stored as JSON in `tests/golden/fixtures/`. Future changes must reproduce the same output unless the fixture is explicitly updated with a reviewer's commit message acknowledging the change.

Required:
- `test_classifier_golden.py` — mocked Claude returns fixed JSON for fixed input; tests classifier wiring (parsing, schema validation, dataclass construction, cache interaction). 10+ fixture pairs covering normal cases, edge cases, error cases.
- `test_smoothing_golden.py` — fixed sequence of `RegimeClassification` inputs → fixed sequence of `SmoothedRegimeState` outputs. Covers persistence, hysteresis, fallback retention, first-run.
- `test_router_golden.py` — every row of the §9.8 routing table has a fixture.
- `test_overlay_golden.py` — fixed (instrument, time, context) → fixed `OverlayCheck` outputs.
- `test_shadow_simulator_golden.py` — fixed hypothetical entry + N bars of OHLCV → fixed close outcome.

**Classifier real-API tests** (`test_classifier_real_api.py`): hits the real Anthropic API with a single fixture. Marked `@pytest.mark.integration`. Skipped by default. Runs manually before walk-forward.

### 16.4 Integration tests (`tests/integration/`)

End-to-end pipeline tests with multiple modules.

Required:
- `test_full_shadow_pipeline.py` — fixture data, full pipeline in shadow mode, asserts expected log rows in expected tables
- `test_flag_combinations.py` — for every legal flag combination, bot starts; every illegal combination raises `ConfigError`
- `test_overlay_independence.py` — covers §14.2 truth table
- `test_calendar_ui_end_to_end.py` — UI creates → DB writes → overlay reads → expected behaviour

### 16.5 API tests (`tests/calendar_ui/test_routes_*.py`)

HTTP-level tests for the calendar UI endpoints. Uses test client (e.g., `TestClient` if FastAPI, or framework equivalent).

Required test files:
- `test_routes_macro.py` — GET/POST/PUT/DELETE happy paths, validation failures, JWT enforcement, confirmation header enforcement, audit row creation on every write
- `test_routes_earnings.py` — same + finnhub/manual_override/manual_addition resolution, stale override detection, read-only enforcement on finnhub rows
- `test_routes_csv_import.py` — happy path, partial validation failures, idempotent re-import, transaction rollback on DB error

Each test asserts:
- Correct HTTP status
- Correct response body shape
- Correct DB state after operation
- Audit row written when expected
- No audit row written on validation failures (write didn't happen)

### 16.6 Regression-specific tests (`tests/regression/`)

One test file per v2 review fix. These exist solely to prevent the bug from reappearing. Each file's docstring explains the bug, the fix, and the spec section.

Required:
- `test_v2_fix_redundant_flag_removed.py` — config rejects `enable_regime_block_no_trade`
- `test_v2_fix_cache_hit_on_dataclass.py` — `RegimeClassification.cache_hit` field accessible
- `test_v2_fix_partial_fills_composite_key.py` — multiple fills create multiple rows under same `position_id`
- `test_v2_fix_routing_unambiguous.py` — every §9.8 row produces expected output; specifically, RANGING + no_overlays + mean_reversion_live=False produces NoOpEngine + allow=False
- `test_v2_fix_overlay_fail_safe.py` — 5 consecutive overlay failures pauses instruments, overlay flag remains enabled
- `test_v2_fix_overlay_independence.py` — overlays_live=True + router_live=False blocks entries on overlay-active instruments
- `test_v2_fix_exit_contract.py` — TripleConfirmation-tagged position exits via TripleConfirmation rules after router switches instrument to MeanReversion
- `test_v2_fix_no_mixed_strategy_fills.py` — assertion fires when two fills tag with different strategies

These are the bugs we *know* are easy to silently revert. They get pinned tests forever.

### 16.7 CI configuration

#### Pre-commit hooks (`ci/pre-commit-config.yaml`)

Using the `pre-commit` framework:
- Run unit tests on changed files (`pytest <paths>` based on git diff)
- Run linting (ruff or equivalent, matching existing project config)
- Run mypy on changed modules (if existing project uses mypy)
- Block commit on failure

Bypass with `git commit --no-verify` for emergencies. Document this in `docs/regime_strategy.md`.

#### CI pipeline (GitHub Actions or equivalent)

On every push and PR:
1. Install dependencies
2. Run full test suite (`pytest tests/`)
3. Run all 486 existing tests
4. Run `test_no_state_contamination.py` explicitly (CI-critical, separately reported)
5. Run coverage check (fail if new code coverage < 85%)
6. Build and verify the calendar UI static assets

PR cannot merge if any of the above fails.

#### Nightly regression replay (`ci/nightly_regression_replay.py`)

Scaffolding only in v3; not enabled until shadow data exists.

When enabled (gated by env var `REGRESSION_REPLAY_ENABLED=1`):
- Reads the last 30 days of `shadow_decisions` rows
- Reconstructs the inputs (features, classifications, overlays) for each row
- Runs current code against those inputs
- Compares current output to recorded output
- Reports divergences

Cannot run in CI on every push (would be too slow); intended as a nightly job or weekly review tool. Tells the operator: "did our changes change how we would have decided historically?"

For v3, build the scaffolding (script, schema for divergence reports, CLI entry point) and document how to enable. Actual enabling waits until shadow data accumulates post-merge.

### 16.8 Acceptance gate (before PR can merge)

- All tests green
- Full pipeline runs 24h on paper account (IBKR `DUQ141950`) in full shadow mode without hard-degrading any component
- Shadow logs show classifications, routings, at least one hypothetical trade opened and closed
- `test_no_state_contamination.py` passes
- 486 existing tests still pass
- Coverage on new code ≥ 85%
- Calendar UI passes manual smoke test (create / edit / delete / CSV import)
- `bot recover-overlay <name>` CLI command exists and works on a simulated overlay failure

---

## 17. Promotion Plan (post-merge)

**Week 1–4 of shadow**: observe. Review dashboard daily. Fix bugs that emerge. Maintain macro calendar via UI. Do not flip live flags.

**End of week 4**: review shadow data.
- Classifier: flip rate < 20% per instrument per month? Confidence distribution sane?
- Router: do blocked-entry counterfactuals show improvement?
- Overlays: earnings/macro events appear in expected windows? False positives?
- Calendar UI: macro maintenance happening on schedule? Audit log healthy?

**First promotion**: `enable_event_overlays_live = true`. Overlays are fully deterministic, safest to promote. Works correctly with router_live=false per §14. Watch 2 weeks.

**Second promotion**: `enable_classifier_live = true` + `enable_persistence_live = true`. Watch 2 weeks.

**Third promotion**: `enable_router_live = true` (mean-reversion still off → RANGING routes to NoOp). Router blocks non-TRENDING entries. Watch 4 weeks.

**Fourth promotion**: `enable_position_tagged_exit_policy = true`. Behavioural equivalent since all open positions are TripleConfirmation-entered. Watch 2 weeks.

**Mean-reversion**: separate research project. Walk-forward on RANGING-labelled historical windows. Only promote `enable_mean_reversion_live` after separate PR demonstrating edge.

**Real money**: only after 60+ days at full live-flag state on paper.

**Nightly regression replay**: enable after 30 days of shadow data exists.

---

## 18. Out of Scope

- Mean-reversion strategy validation (separate project)
- Per-instrument routing overrides
- Multi-model ensemble
- Claude managing exits
- Claude choosing instruments
- Regime-conditional universe retest (post-promotion project)
- IG-specific adaptations
- Prompt caching beyond daily-per-instrument cache
- Multi-user authorization for calendar UI
- Mobile-responsive calendar UI (desktop-only is fine for v3)
- Automatic macro calendar feed (manual maintenance is the design choice)

---

## 19. Deliverables Checklist

### Core architecture (from v2)
- [ ] Branch `claude-strategy` from `main`
- [ ] Directory layout per §4
- [ ] Dataclasses with v2 fields (`cache_hit`, `fill_id`)
- [ ] StrategyEngine ABC + TripleConfirmationEngine wrapper + MeanReversionEngine skeleton + NoOpEngine + registry
- [ ] Regime layer (features, classifier matching `sentiment.py` tool-use pattern, prompt V1, schema, cache with `cache_hit`, cost tracker, smoothing, router with §9.8 unambiguous table)
- [ ] Overlays (earnings with Finnhub + override semantics, macro from DB, low_liquidity computed, data_quality), precedence, registry, instrument dependency map
- [ ] Shadow simulator + counterfactual logger + hypothetical trades table
- [ ] Position metadata with composite `(position_id, fill_id)` PK + partial fill support + exit policy
- [ ] Degradation framework with v2 overlay-pause semantics
- [ ] InstrumentPauseRegistry with persisted pauses
- [ ] CLI `bot recover-overlay <name>` with self-test
- [ ] Flags module with v3 dependency graph + startup logging
- [ ] Main loop with v2 decoupled entry gates per §14
- [ ] Dashboard tabs

### New in v3
- [ ] `bot/overlays/macro_calendar_monitor.py` with weekly/monthly/startup checks (§10.7)
- [ ] Systemd timers or cron entries for weekly and monthly macro alerts
- [ ] Calendar UI module `bot/calendar_ui/` with routes, store, audit, validators, static assets (§15.4)
- [ ] SQLite schemas for `macro_calendar`, `earnings_calendar`, `calendar_edit_audit`
- [ ] Finnhub earnings sync nightly job
- [ ] Earnings override resolution semantics (manual_override > finnhub, with stale-override detection)
- [ ] Confirmation header enforcement on DELETE endpoints
- [ ] CSV import endpoints with per-row validation and transaction rollback
- [ ] Audit log UI tab with before/after JSON viewer
- [ ] `docs/calendar_ui.md` user-facing guide
- [ ] `docs/TECH_DEBT.md` with `layer1.py` orchestration note

### Testing (expanded in v3)
- [ ] Unit tests per §16.1, coverage ≥ 85% on new code
- [ ] Invariant tests (`tests/invariants/`) per §16.2 — 9 named files
- [ ] Golden-file behavioural-equivalence tests (`tests/golden/`) per §16.3 — 5 named files + fixtures
- [ ] `test_classifier_real_api.py` marked optional
- [ ] Integration tests (`tests/integration/`) per §16.4 — 4 named files
- [ ] API tests (`tests/calendar_ui/`) per §16.5 — 3 named files
- [ ] Regression tests (`tests/regression/`) per §16.6 — 8 named files, one per v2 fix
- [ ] Pre-commit config per §16.7
- [ ] CI pipeline config per §16.7
- [ ] Nightly regression replay scaffolding per §16.7 (gated, not enabled)
- [ ] `test_no_state_contamination.py` passing
- [ ] 486 existing tests still passing

### Documentation
- [ ] `docs/regime_strategy.md`
- [ ] `docs/shadow_mode.md`
- [ ] `docs/degradation.md` (with v2 overlay recovery)
- [ ] `docs/calendar_ui.md`
- [ ] `docs/TECH_DEBT.md`

### Operational
- [ ] `claude-sonnet-4-6` pricing confirmed from `docs.claude.com` at impl time, noted in `cost_tracker.py`
- [ ] `config.yaml` updated with all v3 flags
- [ ] README points at `docs/regime_strategy.md`

### CLAUDE.md additions
- [ ] Rule: "Do not modify `bot/layer1.py` signal-generation logic when wrapping it for `TripleConfirmationEngine`. Wrap only. Behavioural equivalence verified by existing backtest suite producing bit-identical trade lists."
- [ ] Rule: "Do not hardcode Anthropic API pricing from training data. Look up `claude-sonnet-4-6` pricing from `https://docs.claude.com` at implementation time and note source URL and date in code comments."

---

## 20. Open Questions

All previously open questions answered as of v3:

1. ✅ `bot/llm/sentiment.py` uses tool-use for structured JSON. New classifier matches.
2. ✅ Main loop in `bot/layer1.py`. Orchestrator extraction logged in `docs/TECH_DEBT.md`.
3. ✅ Confidence thresholds 0.85 / 0.70 / 0.75 confirmed as defaults.
4. ✅ Earnings calendar: Finnhub (already in use for news) with manual override/addition semantics per §15.4.3.
5. ✅ Macro calendar: manually maintained in SQLite, edited via §15.4 UI, freshness monitored per §10.7.
6. ✅ Low-liquidity: bot computes 20-day median per instrument per time-of-day bucket.
7. ✅ `max_daily_cost_usd = 5.0` confirmed.
8. ✅ Dashboard auth: reuse existing JWT via nginx on 8082. Calendar UI under same middleware.
9. ✅ `CLAUDE.md` rules to add: layer1.py wrapping rule + Anthropic pricing lookup rule (see §19 checklist).
10. ✅ Telegram channel: reuse `CogniflowAI_Trading_Alerts_Bot` for all alert types including calendar maintenance.
11. ✅ Broker fill IDs: use IBKR/IG execution IDs when supplied, fall back to `position_id-seq` for either broker as needed.
12. ✅ Overlay recovery: CLI command `bot recover-overlay <name>` confirmed.
13. ✅ Earnings UI editability: both overrides and additions, with stale-override detection.
14. ✅ Calendar storage: Claude Code's choice between SQLite-only or SQLite+JSON-cache, defaulting to SQLite-only unless profiling shows otherwise.
