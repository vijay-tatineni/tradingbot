"""Tests for calendar DB."""
import pytest

from bot.overlays.calendar_db import CalendarDB


@pytest.fixture
def db(tmp_path):
    return CalendarDB(str(tmp_path / "test.db"))


def test_add_and_get_macro_events(db):
    db.add_macro_event("2026-06-01", "FOMC Decision", "Fed",
                       event_time="2026-06-01T18:00:00", impact="high")
    events = db.get_macro_events()
    assert len(events) == 1
    assert events[0]["name"] == "FOMC Decision"


def test_get_macro_events_from_date(db):
    db.add_macro_event("2026-05-01", "Past Event", "Fed")
    db.add_macro_event("2026-06-01", "Future Event", "Fed")
    events = db.get_macro_events(from_date="2026-05-15")
    assert len(events) == 1
    assert events[0]["name"] == "Future Event"


def test_empty_macro_events(db):
    events = db.get_macro_events()
    assert events == []
