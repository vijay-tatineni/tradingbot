"""
§15.4.5: Validation rules for macro calendar events.
"""
import re
from datetime import date, datetime


VALID_REGIONS = {"US", "UK", "EU", "GLOBAL"}
VALID_SEVERITIES = {"high", "medium", "low"}
EVENT_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def validate_macro_event(data: dict) -> list[dict]:
    errors = []

    event_date = data.get("date", "")
    if not event_date:
        errors.append({"field": "date", "message": "Required"})
    else:
        try:
            date.fromisoformat(event_date)
        except (ValueError, TypeError):
            errors.append({"field": "date", "message": f"Expected YYYY-MM-DD, got '{event_date}'"})

    time_utc = data.get("time_utc", "")
    if not time_utc:
        errors.append({"field": "time_utc", "message": "Required"})
    elif not TIME_RE.match(time_utc):
        errors.append({"field": "time_utc", "message": f"Expected HH:MM, got '{time_utc}'"})
    else:
        h, m = int(time_utc[:2]), int(time_utc[3:])
        if h > 23 or m > 59:
            errors.append({"field": "time_utc", "message": f"Invalid time: {time_utc}"})

    event = data.get("event", "")
    if not event:
        errors.append({"field": "event", "message": "Required"})
    elif len(event) > 64:
        errors.append({"field": "event", "message": "Max 64 characters"})
    elif not EVENT_NAME_RE.match(event):
        errors.append({"field": "event", "message": "Must match [A-Z][A-Z0-9_]*"})

    region = data.get("region", "")
    if not region:
        errors.append({"field": "region", "message": "Required"})
    elif region not in VALID_REGIONS:
        errors.append({"field": "region", "message": f"Must be one of {sorted(VALID_REGIONS)}"})

    severity = data.get("severity", "medium")
    if severity not in VALID_SEVERITIES:
        errors.append({"field": "severity", "message": f"Must be one of {sorted(VALID_SEVERITIES)}"})

    notes = data.get("notes", "")
    if len(notes) > 500:
        errors.append({"field": "notes", "message": "Max 500 characters"})

    return errors
