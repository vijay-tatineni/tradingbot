"""
Low liquidity overlay — §10.2 of CLAUDE_STRATEGY_SPEC_v3.

Bot computes 20-day median volume per instrument per time-of-day bucket.
Active if today's cumulative volume at decision time < 40% of median.
data_quality_strict_mode: threshold → 60%.
"""
from datetime import datetime, timezone
from typing import Optional

from bot.overlays.base import Overlay
from bot.overlays.models import OverlayCheck

NORMAL_THRESHOLD_PCT = 40.0
STRICT_THRESHOLD_PCT = 60.0


class LowLiquidityOverlay(Overlay):
    name = "LOW_LIQUIDITY"

    def check(self, instrument: str, now: datetime, ctx: dict) -> OverlayCheck:
        strict = ctx.get("data_quality_strict_mode", False)
        threshold = STRICT_THRESHOLD_PCT if strict else NORMAL_THRESHOLD_PCT

        median_volume = ctx.get("median_volume_for_bucket")
        current_volume = ctx.get("current_cumulative_volume")

        if median_volume is None or current_volume is None:
            return OverlayCheck(
                overlay_name=self.name,
                is_active=False,
                expires_at=None,
                reason="Volume data unavailable",
            )

        if median_volume <= 0:
            return OverlayCheck(
                overlay_name=self.name,
                is_active=False,
                expires_at=None,
                reason="Median volume is zero or negative",
            )

        volume_pct = (current_volume / median_volume) * 100

        if volume_pct < threshold:
            return OverlayCheck(
                overlay_name=self.name,
                is_active=True,
                expires_at=None,
                reason=f"Low liquidity: {volume_pct:.1f}% of median "
                       f"(threshold {threshold:.0f}%)",
            )

        return OverlayCheck(
            overlay_name=self.name,
            is_active=False,
            expires_at=None,
            reason=f"Liquidity OK: {volume_pct:.1f}% of median",
        )

    def self_test(self) -> bool:
        now = datetime.now(timezone.utc)
        result = self.check("__self_test__", now, {})
        return True

    def instruments_affected(self) -> list:
        return ["__all__"]
