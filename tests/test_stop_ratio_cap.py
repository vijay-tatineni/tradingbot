"""The broker-held stop may not sit more than 3x the trail stop away.

Positions are sized (PR D) so a move to the *trail* stop costs risk_fraction of
equity. The broker-held stop (PR B) sits at the wider *emergency* level, and it
is what fires if the bot process or host dies. So the realised loss in that
scenario scales by emergency_stop_pct / trail_stop_pct.

Before this cap, NVTS ran a 10x ratio: an intended 1% risk became a 10% loss of
the whole account on process death, and NVTS + ANTO + NBIS orphaned together
came to 20%. Capping the ratio at 3x bounds that exposure.

This is a config invariant, so it is asserted against the real instruments.json
rather than a fixture — a future edit that widens a stop must fail here.
"""

import json
from pathlib import Path

import pytest

INSTRUMENTS = Path(__file__).resolve().parent.parent / "instruments.json"

MAX_RATIO = 3.0
TOLERANCE = 1e-9


def _instruments_with_stop_levels():
    data = json.loads(INSTRUMENTS.read_text())
    found = []

    def walk(node):
        if isinstance(node, dict):
            if ("symbol" in node and "trail_stop_pct" in node
                    and "emergency_stop_pct" in node):
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return found


ALL_INSTRUMENTS = _instruments_with_stop_levels()


def test_the_config_actually_has_stop_levels_to_check():
    """Guard against the walker silently finding nothing."""
    assert len(ALL_INSTRUMENTS) >= 20


@pytest.mark.parametrize(
    "inst", ALL_INSTRUMENTS,
    ids=[i["symbol"] for i in ALL_INSTRUMENTS])
def test_emergency_stop_is_at_most_three_times_the_trail(inst):
    trail = float(inst["trail_stop_pct"])
    emergency = float(inst["emergency_stop_pct"])
    assert trail > 0, f"{inst['symbol']} has a non-positive trail stop"

    ratio = emergency / trail
    assert ratio <= MAX_RATIO + TOLERANCE, (
        f"{inst['symbol']}: emergency {emergency}% is {ratio:.2f}x the "
        f"{trail}% trail. On process death only the broker stop fires, so an "
        f"intended 1% risk becomes a {ratio:.1f}% loss. Cap is {MAX_RATIO}x."
    )


def test_emergency_stop_is_never_tighter_than_the_trail():
    """A backstop inside the primary exit would fire first and change strategy."""
    for inst in ALL_INSTRUMENTS:
        trail = float(inst["trail_stop_pct"])
        emergency = float(inst["emergency_stop_pct"])
        assert emergency >= trail, (
            f"{inst['symbol']}: emergency {emergency}% is tighter than the "
            f"{trail}% trail — the backstop would pre-empt the primary exit"
        )


def test_worst_case_process_death_loss_is_bounded():
    """The number that matters, stated in money at the Phase 1 baseline."""
    equity, risk_fraction = 250_000.0, 0.01
    worst = max(float(i["emergency_stop_pct"]) / float(i["trail_stop_pct"])
                for i in ALL_INSTRUMENTS)
    worst_loss = equity * risk_fraction * worst

    assert worst <= MAX_RATIO + TOLERANCE
    assert worst_loss <= 7_500.0 + TOLERANCE
