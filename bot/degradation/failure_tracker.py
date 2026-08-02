"""
Failure tracker — §13.1-13.2 of CLAUDE_STRATEGY_SPEC_v3.

Tracks consecutive and windowed failures per component to determine
soft vs hard degradation thresholds.
"""
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("degradation.failure_tracker")


class FailureTracker:
    def __init__(self):
        self._consecutive: dict[str, int] = defaultdict(int)
        self._window: dict[str, list[datetime]] = defaultdict(list)

    def record_success(self, component: str) -> None:
        self._consecutive[component] = 0

    def record_failure(self, component: str) -> dict:
        now = datetime.now(timezone.utc)
        self._consecutive[component] += 1
        self._window[component].append(now)

        one_hour_ago = now - timedelta(hours=1)
        self._window[component] = [
            t for t in self._window[component] if t > one_hour_ago
        ]

        return {
            "component": component,
            "consecutive": self._consecutive[component],
            "failures_last_hour": len(self._window[component]),
        }

    def consecutive_failures(self, component: str) -> int:
        return self._consecutive.get(component, 0)

    def failures_in_last_hour(self, component: str) -> int:
        now = datetime.now(timezone.utc)
        one_hour_ago = now - timedelta(hours=1)
        entries = self._window.get(component, [])
        return sum(1 for t in entries if t > one_hour_ago)

    def reset(self, component: str) -> None:
        self._consecutive[component] = 0
        self._window[component] = []
