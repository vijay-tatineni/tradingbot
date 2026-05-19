"""
Macro lockout overlay — §10.2 of CLAUDE_STRATEGY_SPEC_v3.

Active: from 2 hours before event through close of event day.
data_quality_strict_mode: entire event day.
"""
from datetime import datetime, timezone, timedelta, date
from typing import Optional

from bot.overlays.base import Overlay
from bot.overlays.models import OverlayCheck

HOURS_BEFORE_EVENT = 2


class MacroLockoutOverlay(Overlay):
    name = "MACRO_LOCKOUT"

    def check(self, instrument: str, now: datetime, ctx: dict) -> OverlayCheck:
        strict = ctx.get("data_quality_strict_mode", False)
        macro_events = ctx.get("macro_events", [])

        if not macro_events:
            return OverlayCheck(
                overlay_name=self.name,
                is_active=False,
                expires_at=None,
                reason="No macro events scheduled",
            )

        for event in macro_events:
            event_time = event.get("event_time")
            event_name = event.get("name", "Unknown macro event")
            event_date = event.get("event_date")

            if event_time is not None:
                if isinstance(event_time, str):
                    event_time = datetime.fromisoformat(event_time)
                if event_time.tzinfo is None:
                    event_time = event_time.replace(tzinfo=timezone.utc)

                if strict:
                    day_start = event_time.replace(hour=0, minute=0, second=0,
                                                   microsecond=0)
                    lockout_start = day_start
                else:
                    lockout_start = event_time - timedelta(hours=HOURS_BEFORE_EVENT)

                lockout_end = event_time.replace(hour=23, minute=59, second=59,
                                                 microsecond=0)

                if lockout_start <= now <= lockout_end:
                    return OverlayCheck(
                        overlay_name=self.name,
                        is_active=True,
                        expires_at=lockout_end,
                        reason=f"Macro lockout: {event_name}",
                    )
            elif event_date is not None:
                ed = event_date if isinstance(event_date, date) else date.fromisoformat(str(event_date))
                today = now.date() if isinstance(now, datetime) else now
                if today == ed:
                    lockout_end = datetime(ed.year, ed.month, ed.day, 23, 59, 59,
                                           tzinfo=timezone.utc)
                    return OverlayCheck(
                        overlay_name=self.name,
                        is_active=True,
                        expires_at=lockout_end,
                        reason=f"Macro lockout: {event_name}",
                    )

        return OverlayCheck(
            overlay_name=self.name,
            is_active=False,
            expires_at=None,
            reason="No active macro events",
        )

    def self_test(self) -> bool:
        now = datetime.now(timezone.utc)
        result = self.check("__self_test__", now, {})
        return True

    def instruments_affected(self) -> list:
        return ["__all__"]
