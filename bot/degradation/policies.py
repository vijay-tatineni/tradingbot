"""
Degradation policies — §13.2-13.3 of CLAUDE_STRATEGY_SPEC_v3.

Defines per-component thresholds and fallback actions.
v2 fail-toward-safety: overlays pause instruments (flag NOT auto-disabled),
classifier/router/MR disable their flag (reverts to safer legacy path).
"""
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("degradation.policies")


@dataclass(frozen=True)
class DegradationThreshold:
    component: str
    soft_consecutive: int
    hard_consecutive: int
    hard_hourly_pct: Optional[float]
    hard_hourly_count: Optional[int]


THRESHOLDS = {
    "classifier": DegradationThreshold(
        component="classifier",
        soft_consecutive=1,
        hard_consecutive=5,
        hard_hourly_pct=50.0,
        hard_hourly_count=None,
    ),
    "router": DegradationThreshold(
        component="router",
        soft_consecutive=1,
        hard_consecutive=3,
        hard_hourly_pct=None,
        hard_hourly_count=None,
    ),
    "overlay": DegradationThreshold(
        component="overlay",
        soft_consecutive=1,
        hard_consecutive=5,
        hard_hourly_pct=None,
        hard_hourly_count=None,
    ),
    "mean_reversion": DegradationThreshold(
        component="mean_reversion",
        soft_consecutive=1,
        hard_consecutive=3,
        hard_hourly_pct=None,
        hard_hourly_count=None,
    ),
    "db_logging": DegradationThreshold(
        component="db_logging",
        soft_consecutive=1,
        hard_consecutive=3,
        hard_hourly_pct=None,
        hard_hourly_count=None,
    ),
    "shadow_simulator": DegradationThreshold(
        component="shadow_simulator",
        soft_consecutive=1,
        hard_consecutive=999,  # never hard-degrades
        hard_hourly_pct=None,
        hard_hourly_count=None,
    ),
    "calendar_ui": DegradationThreshold(
        component="calendar_ui",
        soft_consecutive=1,
        hard_consecutive=3,
        hard_hourly_pct=None,
        hard_hourly_count=None,
    ),
}


@dataclass(frozen=True)
class DegradationAction:
    severity: str  # "soft" or "hard"
    component: str
    action: str
    flag_to_disable: Optional[str]
    pause_instruments: bool


HARD_ACTIONS = {
    "classifier": DegradationAction(
        severity="hard",
        component="classifier",
        action="Disable enable_classifier_live, router uses frozen state",
        flag_to_disable="enable_classifier_live",
        pause_instruments=False,
    ),
    "router": DegradationAction(
        severity="hard",
        component="router",
        action="Disable enable_router_live, revert to legacy path",
        flag_to_disable="enable_router_live",
        pause_instruments=False,
    ),
    "overlay": DegradationAction(
        severity="hard",
        component="overlay",
        action="Pause new entries on affected instruments. Flag NOT auto-disabled.",
        flag_to_disable=None,
        pause_instruments=True,
    ),
    "mean_reversion": DegradationAction(
        severity="hard",
        component="mean_reversion",
        action="Disable enable_mean_reversion_live",
        flag_to_disable="enable_mean_reversion_live",
        pause_instruments=False,
    ),
    "db_logging": DegradationAction(
        severity="hard",
        component="db_logging",
        action="Pause new trades, existing positions continue",
        flag_to_disable=None,
        pause_instruments=True,
    ),
    "calendar_ui": DegradationAction(
        severity="hard",
        component="calendar_ui",
        action="No trading impact; critical alert about DB write failures",
        flag_to_disable=None,
        pause_instruments=False,
    ),
}


def evaluate_degradation(component: str, consecutive: int,
                         failures_last_hour: int,
                         total_invocations_last_hour: int = 0) -> Optional[DegradationAction]:
    threshold = THRESHOLDS.get(component)
    if threshold is None:
        logger.warning("No degradation threshold for component: %s", component)
        return None

    is_hard = consecutive >= threshold.hard_consecutive

    if not is_hard and threshold.hard_hourly_pct is not None:
        if total_invocations_last_hour > 0:
            pct = (failures_last_hour / total_invocations_last_hour) * 100
            if pct >= threshold.hard_hourly_pct:
                is_hard = True

    if not is_hard and threshold.hard_hourly_count is not None:
        if failures_last_hour >= threshold.hard_hourly_count:
            is_hard = True

    if is_hard:
        return HARD_ACTIONS.get(component)

    if consecutive >= threshold.soft_consecutive:
        return DegradationAction(
            severity="soft",
            component=component,
            action=f"Soft degradation: log and use fallback for {component}",
            flag_to_disable=None,
            pause_instruments=False,
        )

    return None
