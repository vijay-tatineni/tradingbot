"""Tests for failure tracker."""
from bot.degradation.failure_tracker import FailureTracker


def test_initial_state():
    tracker = FailureTracker()
    assert tracker.consecutive_failures("classifier") == 0
    assert tracker.failures_in_last_hour("classifier") == 0


def test_record_failure_increments():
    tracker = FailureTracker()
    result = tracker.record_failure("classifier")
    assert result["consecutive"] == 1
    tracker.record_failure("classifier")
    assert tracker.consecutive_failures("classifier") == 2


def test_success_resets_consecutive():
    tracker = FailureTracker()
    tracker.record_failure("classifier")
    tracker.record_failure("classifier")
    tracker.record_success("classifier")
    assert tracker.consecutive_failures("classifier") == 0


def test_windowed_failures():
    tracker = FailureTracker()
    for _ in range(3):
        tracker.record_failure("router")
    assert tracker.failures_in_last_hour("router") == 3


def test_reset():
    tracker = FailureTracker()
    tracker.record_failure("overlay")
    tracker.record_failure("overlay")
    tracker.reset("overlay")
    assert tracker.consecutive_failures("overlay") == 0
    assert tracker.failures_in_last_hour("overlay") == 0


def test_independent_components():
    tracker = FailureTracker()
    tracker.record_failure("classifier")
    tracker.record_failure("classifier")
    tracker.record_failure("router")
    assert tracker.consecutive_failures("classifier") == 2
    assert tracker.consecutive_failures("router") == 1
