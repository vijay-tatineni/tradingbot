"""
Overlay registry — §10.3-10.4 of CLAUDE_STRATEGY_SPEC_v3.

Precedence order:
  1. DATA_QUALITY    → short-circuit, NoOpEngine, no trading
  2. LOW_LIQUIDITY   → classify, but allow_new_entries=False
  3. MACRO_LOCKOUT   → classify, allow_new_entries=False, exits continue
  4. None active     → route per §9.8
"""
from datetime import datetime
from typing import Optional

from bot.overlays.models import OverlayCheck
from bot.overlays.data_quality import DataQualityOverlay
from bot.overlays.macro_lockout import MacroLockoutOverlay
from bot.overlays.low_liquidity import LowLiquidityOverlay

OVERLAY_ORDER = [
    DataQualityOverlay(),
    LowLiquidityOverlay(),
    MacroLockoutOverlay(),
]

OVERLAY_BY_NAME = {o.name: o for o in OVERLAY_ORDER}


def active_overlays(instrument: str, now: datetime, ctx: dict) -> list:
    results = []
    for overlay in OVERLAY_ORDER:
        check = overlay.check(instrument, now, ctx)
        if check.is_active:
            results.append(check)
    return results


def get_overlay(name: str):
    overlay = OVERLAY_BY_NAME.get(name)
    if overlay is None:
        raise ValueError(f"Unknown overlay: {name}")
    return overlay


def instruments_affected_by_overlay(overlay_name: str) -> list:
    """§10.6: Instruments whose entries depend on this overlay being functional."""
    overlay = get_overlay(overlay_name)
    return overlay.instruments_affected()
