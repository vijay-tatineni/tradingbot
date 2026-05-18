"""
Overlay dataclasses — §10.1 of CLAUDE_STRATEGY_SPEC_v3.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class OverlayCheck:
    overlay_name: str
    is_active: bool
    expires_at: Optional[datetime]
    reason: str
