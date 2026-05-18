"""Tests for degradation event store."""
import pytest

from bot.degradation.events import DegradationEventStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "test.db")
    return DegradationEventStore(db_path)


def test_record_event(store):
    event_id = store.record(
        component="classifier",
        severity="hard",
        trigger_reason="5 consecutive failures",
        action_taken="Disabled enable_classifier_live",
        flag_disabled="enable_classifier_live",
    )
    assert event_id >= 1


def test_record_with_instruments(store):
    event_id = store.record(
        component="overlay:EARNINGS_LOCKOUT",
        severity="hard",
        trigger_reason="5 consecutive failures",
        action_taken="Paused instruments",
        instruments_paused=["AAPL", "MSFT"],
    )
    assert event_id >= 1


def test_recent_returns_events(store):
    store.record("classifier", "soft", "1 failure", "logged")
    store.record("router", "hard", "3 failures", "disabled flag")
    events = store.recent(limit=10)
    assert len(events) == 2


def test_recent_ordered_desc(store):
    store.record("classifier", "soft", "first", "logged")
    store.record("router", "hard", "second", "disabled")
    events = store.recent()
    assert events[0][2] == "router"
    assert events[1][2] == "classifier"
