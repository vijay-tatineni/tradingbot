"""
Data quality overlay — §10.2 of CLAUDE_STRATEGY_SPEC_v3.

Active if: latest bar stale > N minutes, gap > 15% without news,
OHLCV sanity check failures.
Short-circuits other overlays and classification.
data_quality_strict_mode: halves N.
"""
from datetime import datetime, timezone, timedelta
from typing import Optional

from bot.overlays.base import Overlay
from bot.overlays.models import OverlayCheck

DEFAULT_STALE_MINUTES = 30
GAP_THRESHOLD_PCT = 15.0


class DataQualityOverlay(Overlay):
    name = "DATA_QUALITY"

    def check(self, instrument: str, now: datetime, ctx: dict) -> OverlayCheck:
        strict = ctx.get("data_quality_strict_mode", False)
        stale_limit = DEFAULT_STALE_MINUTES // 2 if strict else DEFAULT_STALE_MINUTES
        reasons = []

        last_bar_time = ctx.get("last_bar_time")
        if last_bar_time is not None:
            age_minutes = (now - last_bar_time).total_seconds() / 60
            if age_minutes > stale_limit:
                reasons.append(f"Bar stale {age_minutes:.0f}m (limit {stale_limit}m)")

        ohlcv = ctx.get("ohlcv")
        if ohlcv is not None:
            problems = _sanity_check(ohlcv)
            if problems:
                reasons.extend(problems)

        prev_close = ctx.get("prev_close")
        current_open = ctx.get("current_open")
        if prev_close and current_open and prev_close > 0:
            gap_pct = abs(current_open - prev_close) / prev_close * 100
            has_news = ctx.get("has_news_for_gap", False)
            if gap_pct > GAP_THRESHOLD_PCT and not has_news:
                reasons.append(f"Gap {gap_pct:.1f}% without news")

        is_active = len(reasons) > 0
        return OverlayCheck(
            overlay_name=self.name,
            is_active=is_active,
            expires_at=None,
            reason="; ".join(reasons) if reasons else "Data quality OK",
        )

    def self_test(self) -> bool:
        now = datetime.now(timezone.utc)
        result = self.check("__self_test__", now, {})
        return not result.is_active

    def instruments_affected(self) -> list:
        return ["__all__"]


def _sanity_check(ohlcv: dict) -> list:
    problems = []
    o, h, l, c, v = (ohlcv.get(k) for k in ("open", "high", "low", "close", "volume"))
    if any(x is None for x in (o, h, l, c)):
        problems.append("Missing OHLCV fields")
        return problems
    if l > h:
        problems.append(f"Low ({l}) > High ({h})")
    if o <= 0 or c <= 0:
        problems.append("Non-positive open or close")
    if v is not None and v < 0:
        problems.append("Negative volume")
    return problems
