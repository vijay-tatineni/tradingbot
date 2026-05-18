"""
Regression test: v2 overlay fail-safe semantics.

Before v2: overlay hard-failure would auto-disable the overlay flag,
which paradoxically REMOVES the safety check.

After v2: overlay hard-failure pauses instruments via InstrumentPauseRegistry.
Flag is NOT auto-disabled. Recovery requires manual `bot recover-overlay` CLI.

This regression test ensures we never revert to the unsafe v1 behavior.
"""
from bot.degradation.policies import evaluate_degradation, HARD_ACTIONS


def test_overlay_hard_never_disables_flag():
    """Regression: overlay hard must NOT disable its flag (that removes safety)."""
    action = evaluate_degradation("overlay", consecutive=5,
                                  failures_last_hour=5)
    assert action.flag_to_disable is None, \
        "REGRESSION: overlay hard-failure must not disable flag (removes safety)"


def test_overlay_hard_must_pause_instruments():
    """Regression: overlay hard must pause instruments for manual recovery."""
    action = evaluate_degradation("overlay", consecutive=5,
                                  failures_last_hour=5)
    assert action.pause_instruments is True, \
        "REGRESSION: overlay hard-failure must pause instruments"


def test_hard_action_table_overlay_entry():
    """Regression: HARD_ACTIONS['overlay'] must exist with correct semantics."""
    assert "overlay" in HARD_ACTIONS
    entry = HARD_ACTIONS["overlay"]
    assert entry.flag_to_disable is None
    assert entry.pause_instruments is True
