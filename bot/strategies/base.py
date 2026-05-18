"""
StrategyEngine ABC — §8 of CLAUDE_STRATEGY_SPEC_v3.

All strategy engines implement generate_candidate() for entries
and manage_exit() for position management.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

from bot.regime.models import SmoothedRegimeState, PositionMetadata


Action = Literal["BUY", "SELL", "HOLD", "CLOSE"]


@dataclass
class MarketState:
    symbol: str
    bar_time: pd.Timestamp
    ohlcv: pd.DataFrame
    indicators: dict
    open_position: Optional[dict]
    recent_trades: list
    news: list
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
    def generate_candidate(self, state: MarketState) -> Optional[Signal]:
        ...

    @abstractmethod
    def manage_exit(self, position: PositionMetadata, state: MarketState) -> ExitDecision:
        ...
