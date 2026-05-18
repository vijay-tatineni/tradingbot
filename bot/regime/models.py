"""
Core dataclasses for the regime-aware strategy switching system.
§7 of CLAUDE_STRATEGY_SPEC_v3.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional


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


@dataclass(frozen=True)
class SmoothedRegimeState:
    instrument: str
    effective_regime: Regime
    source_regime: Regime
    days_in_regime: int
    last_changed_at: datetime
    confidence: float
    pending_regime: Optional[Regime]
    pending_days: int
    regime_history: list = field(default_factory=list)


@dataclass(frozen=True)
class RoutingDecision:
    instrument: str
    decided_at: datetime
    effective_regime: Regime
    selected_engine: str
    allow_new_entries: bool
    active_overlays: list = field(default_factory=list)
    overlay_expires_at: Optional[datetime] = None
    block_reason: Optional[str] = None
    flag_snapshot: dict = field(default_factory=dict)


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
    entry_overlays_active: list = field(default_factory=list)
    entry_prompt_version: Optional[str] = None
    exit_policy: Literal["use_entry_strategy_rules"] = "use_entry_strategy_rules"
