"""Tests for degradation policies."""
from bot.degradation.policies import evaluate_degradation, THRESHOLDS, HARD_ACTIONS


def test_no_degradation_at_zero():
    result = evaluate_degradation("classifier", consecutive=0,
                                  failures_last_hour=0)
    assert result is None


def test_soft_at_single_failure():
    result = evaluate_degradation("classifier", consecutive=1,
                                  failures_last_hour=1)
    assert result is not None
    assert result.severity == "soft"


def test_hard_at_threshold():
    result = evaluate_degradation("classifier", consecutive=5,
                                  failures_last_hour=5)
    assert result is not None
    assert result.severity == "hard"
    assert result.flag_to_disable == "enable_classifier_live"


def test_router_hard_at_3():
    result = evaluate_degradation("router", consecutive=3,
                                  failures_last_hour=3)
    assert result.severity == "hard"
    assert result.flag_to_disable == "enable_router_live"


def test_overlay_hard_pauses_instruments():
    result = evaluate_degradation("overlay", consecutive=5,
                                  failures_last_hour=5)
    assert result.severity == "hard"
    assert result.flag_to_disable is None
    assert result.pause_instruments is True


def test_overlay_hard_does_not_disable_flag():
    """§13.3 v2 asymmetry: overlays pause instruments, do NOT auto-disable flags."""
    result = evaluate_degradation("overlay", consecutive=5,
                                  failures_last_hour=5)
    assert result.flag_to_disable is None


def test_classifier_hourly_pct_triggers_hard():
    result = evaluate_degradation("classifier", consecutive=2,
                                  failures_last_hour=6,
                                  total_invocations_last_hour=10)
    assert result.severity == "hard"


def test_shadow_simulator_never_hard():
    result = evaluate_degradation("shadow_simulator", consecutive=100,
                                  failures_last_hour=100)
    assert result.severity == "soft"


def test_mean_reversion_hard_disables_flag():
    result = evaluate_degradation("mean_reversion", consecutive=3,
                                  failures_last_hour=3)
    assert result.severity == "hard"
    assert result.flag_to_disable == "enable_mean_reversion_live"


def test_db_logging_hard_pauses():
    result = evaluate_degradation("db_logging", consecutive=3,
                                  failures_last_hour=3)
    assert result.severity == "hard"
    assert result.pause_instruments is True


def test_unknown_component():
    result = evaluate_degradation("unknown_thing", consecutive=99,
                                  failures_last_hour=99)
    assert result is None


def test_all_thresholds_defined():
    expected = {"classifier", "router", "overlay", "mean_reversion",
                "db_logging", "shadow_simulator", "calendar_ui"}
    assert set(THRESHOLDS.keys()) == expected
