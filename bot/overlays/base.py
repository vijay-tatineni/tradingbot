"""
Overlay base ABC — §10.1 of CLAUDE_STRATEGY_SPEC_v3.

All overlays are deterministic pure functions:
  (instrument, timestamp, context) → OverlayCheck
"""
from abc import ABC, abstractmethod
from datetime import datetime

from bot.overlays.models import OverlayCheck


class Overlay(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def check(self, instrument: str, now: datetime, ctx: dict) -> OverlayCheck:
        ...

    @abstractmethod
    def self_test(self) -> bool:
        """Run a lightweight self-test. Returns True if the overlay is functional."""
        ...

    @abstractmethod
    def instruments_affected(self) -> list:
        """Return list of instruments whose entries depend on this overlay."""
        ...
