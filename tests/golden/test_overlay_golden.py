"""
Golden-file test for overlay registry.

Verifies that overlay combinations produce expected active overlay sets.
Uses relative time offsets in fixtures so tests don't depend on clock.
"""
import json
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

import pytest

from bot.overlays.registry import active_overlays

FIXTURES = Path(__file__).parent / "fixtures" / "overlay_scenarios.json"


def _load_scenarios():
    with open(FIXTURES) as f:
        return json.load(f)


def _build_ctx(raw_ctx: dict, now: datetime) -> dict:
    ctx = {}
    for key, val in raw_ctx.items():
        if key == "last_bar_time_offset_minutes":
            ctx["last_bar_time"] = now + timedelta(minutes=val)
        elif key == "macro_event_offset_hours":
            event_time = now + timedelta(hours=val)
            ctx["macro_events"] = [{"event_time": event_time, "name": "Test Event"}]
        else:
            ctx[key] = val
    return ctx


SCENARIOS = _load_scenarios()


@pytest.mark.parametrize(
    "scenario",
    SCENARIOS,
    ids=[s["name"] for s in SCENARIOS],
)
def test_overlay_scenario(scenario):
    now = datetime(2026, 5, 18, 14, 0, 0, tzinfo=timezone.utc)
    ctx = _build_ctx(scenario["ctx"], now)
    results = active_overlays(scenario["instrument"], now, ctx)
    active_names = [r.overlay_name for r in results]
    assert active_names == scenario["expected_active"], \
        f"Scenario '{scenario['name']}': expected {scenario['expected_active']}, got {active_names}"
