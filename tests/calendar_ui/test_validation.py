"""§15.4.5: Macro event validation tests."""
import pytest

from bot.calendar_ui.validation import validate_macro_event


def _valid_event(**overrides):
    base = {
        "date": "2026-06-18",
        "time_utc": "18:00",
        "event": "FOMC_RATE_DECISION",
        "region": "US",
        "severity": "high",
        "notes": "",
    }
    base.update(overrides)
    return base


def test_valid_event():
    assert validate_macro_event(_valid_event()) == []


def test_missing_date():
    errors = validate_macro_event(_valid_event(date=""))
    assert any(e["field"] == "date" for e in errors)


def test_invalid_date_format():
    errors = validate_macro_event(_valid_event(date="2026/06/18"))
    assert any(e["field"] == "date" and "YYYY-MM-DD" in e["message"] for e in errors)


def test_missing_time():
    errors = validate_macro_event(_valid_event(time_utc=""))
    assert any(e["field"] == "time_utc" for e in errors)


def test_invalid_time_format():
    errors = validate_macro_event(_valid_event(time_utc="25:00"))
    assert any(e["field"] == "time_utc" for e in errors)


def test_event_name_too_long():
    errors = validate_macro_event(_valid_event(event="A" * 65))
    assert any(e["field"] == "event" for e in errors)


def test_event_name_invalid_chars():
    errors = validate_macro_event(_valid_event(event="fomc_rate"))
    assert any(e["field"] == "event" for e in errors)


def test_event_name_valid_pattern():
    assert validate_macro_event(_valid_event(event="BOE_RATE_DECISION")) == []
    assert validate_macro_event(_valid_event(event="CPI_DATA2")) == []


def test_invalid_region():
    errors = validate_macro_event(_valid_event(region="JP"))
    assert any(e["field"] == "region" for e in errors)


def test_valid_regions():
    for region in ("US", "UK", "EU", "GLOBAL"):
        assert validate_macro_event(_valid_event(region=region)) == []


def test_invalid_severity():
    errors = validate_macro_event(_valid_event(severity="critical"))
    assert any(e["field"] == "severity" for e in errors)


def test_notes_too_long():
    errors = validate_macro_event(_valid_event(notes="x" * 501))
    assert any(e["field"] == "notes" for e in errors)


def test_multiple_errors():
    errors = validate_macro_event({"date": "", "time_utc": "", "event": "", "region": ""})
    assert len(errors) >= 4
