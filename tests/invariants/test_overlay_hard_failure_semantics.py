"""
§13.3 invariant: Overlay hard-failure pauses instruments, does NOT auto-disable flag.

This is the asymmetry rule:
- Classifier/router/MR hard → disable flag (revert to legacy)
- Overlay hard → pause instruments (flag NOT touched)

Reason: disabling an overlay flag removes safety. Pausing instruments
preserves safety until manual recovery via `bot recover-overlay`.
"""
from bot.degradation.policies import evaluate_degradation, HARD_ACTIONS


def test_overlay_hard_does_not_disable_flag():
    action = evaluate_degradation("overlay", consecutive=5,
                                  failures_last_hour=5)
    assert action is not None
    assert action.severity == "hard"
    assert action.flag_to_disable is None
    assert action.pause_instruments is True


def test_classifier_hard_disables_flag():
    action = evaluate_degradation("classifier", consecutive=5,
                                  failures_last_hour=5)
    assert action.flag_to_disable == "enable_classifier_live"
    assert action.pause_instruments is False


def test_router_hard_disables_flag():
    action = evaluate_degradation("router", consecutive=3,
                                  failures_last_hour=3)
    assert action.flag_to_disable == "enable_router_live"
    assert action.pause_instruments is False


def test_mean_reversion_hard_disables_flag():
    action = evaluate_degradation("mean_reversion", consecutive=3,
                                  failures_last_hour=3)
    assert action.flag_to_disable == "enable_mean_reversion_live"
    assert action.pause_instruments is False


def test_asymmetry_is_consistent_in_hard_actions():
    """All overlay entries in HARD_ACTIONS have pause=True, flag=None.
    All non-overlay non-db entries have flag!=None, pause=False."""
    for component, action in HARD_ACTIONS.items():
        if component == "overlay":
            assert action.flag_to_disable is None, f"{component} should not disable flag"
            assert action.pause_instruments is True, f"{component} should pause instruments"
        elif component in ("classifier", "router", "mean_reversion"):
            assert action.flag_to_disable is not None, f"{component} should disable a flag"
            assert action.pause_instruments is False, f"{component} should not pause instruments"
