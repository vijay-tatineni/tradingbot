"""
Overlay registry — §10.3-10.4 of CLAUDE_STRATEGY_SPEC_v3.

Precedence order:
  1. DATA_QUALITY    → short-circuit, NoOpEngine, no trading
  2. LOW_LIQUIDITY   → classify, but allow_new_entries=False
  3. MACRO_LOCKOUT   → classify, allow_new_entries=False, exits continue
  4. None active     → route per §9.8
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from bot.overlays.models import OverlayCheck
from bot.overlays.data_quality import DataQualityOverlay
from bot.overlays.macro_lockout import MacroLockoutOverlay
from bot.overlays.low_liquidity import LowLiquidityOverlay

logger = logging.getLogger("overlays.registry")

OVERLAY_ORDER = [
    DataQualityOverlay(),
    LowLiquidityOverlay(),
    MacroLockoutOverlay(),
]

OVERLAY_BY_NAME = {o.name: o for o in OVERLAY_ORDER}

_db_path: Optional[str] = None


def init_overlay_registry(db_path: str) -> None:
    global _db_path
    _db_path = db_path


def _load_macro_events() -> list:
    if _db_path is None:
        return []
    try:
        from bot.overlays.calendar_db import CalendarDB
        db = CalendarDB(_db_path)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = db.get_macro_events(from_date=today)
        events = []
        for row in rows:
            event = {"name": row["name"], "event_date": row["event_date"]}
            if row.get("event_time"):
                iso_str = f"{row['event_date']}T{row['event_time']}:00"
                event["event_time"] = datetime.fromisoformat(iso_str).replace(
                    tzinfo=timezone.utc)
            events.append(event)
        return events
    except Exception as e:
        logger.warning("Failed to load macro events from DB: %s", e)
        return []


def active_overlays(instrument: str, now: datetime, ctx: dict) -> list:
    enriched_ctx = {**ctx}
    if "macro_events" not in enriched_ctx:
        enriched_ctx["macro_events"] = _load_macro_events()
    results = []
    for overlay in OVERLAY_ORDER:
        check = overlay.check(instrument, now, enriched_ctx)
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
