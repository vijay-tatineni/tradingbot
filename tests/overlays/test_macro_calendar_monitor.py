"""Tests for macro calendar monitor."""
import sqlite3
import pytest
from datetime import datetime, timezone, timedelta

from bot.overlays.macro_calendar_monitor import (
    check_calendar_freshness,
    format_monthly_report,
    run_check,
    startup_check,
    should_send_alert,
    _last_alert_severity,
    _last_alert_time,
)
import bot.overlays.macro_calendar_monitor as monitor_mod


@pytest.fixture
def db_with_events(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS macro_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_date TEXT NOT NULL,
            event_time TEXT,
            name TEXT NOT NULL,
            source TEXT NOT NULL,
            impact TEXT DEFAULT 'medium',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    now_str = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).date()
    for i in range(5):
        d = today + timedelta(days=i * 10)
        conn.execute(
            "INSERT INTO macro_events (event_date, name, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (d.isoformat(), f"Event {i}", "test", now_str, now_str),
        )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def empty_db(tmp_path):
    db_path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS macro_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_date TEXT NOT NULL,
            event_time TEXT,
            name TEXT NOT NULL,
            source TEXT NOT NULL,
            impact TEXT DEFAULT 'medium',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def test_missing_db(tmp_path):
    status = check_calendar_freshness(str(tmp_path / "nonexistent.db"))
    assert status["severity"] == "MISSING"


def test_missing_table(tmp_path):
    db_path = str(tmp_path / "no_table.db")
    conn = sqlite3.connect(db_path)
    conn.close()
    status = check_calendar_freshness(db_path)
    assert status["severity"] == "MISSING"


def test_empty_calendar(empty_db):
    status = check_calendar_freshness(empty_db)
    assert status["severity"] == "EMPTY"


def test_ok_calendar(db_with_events):
    status = check_calendar_freshness(db_with_events)
    assert status["severity"] in ("OK", "LOW", "CRITICAL")
    assert status["events_remaining"] == 5


def test_format_monthly_report():
    status = {
        "severity": "OK",
        "events_remaining": 10,
        "days_of_coverage": 45,
        "farthest_date": "2026-07-01",
        "message": "Macro calendar OK",
    }
    report = format_monthly_report(status)
    assert "Monthly Macro Calendar Report" in report
    assert "Fed" in report
    assert "BoE" in report


def test_run_check_calls_send_on_critical(empty_db):
    sent = []
    run_check(empty_db, send_fn=sent.append, force=True)
    assert len(sent) == 1
    assert "🚨" in sent[0]


def test_startup_check_alerts_on_missing(tmp_path):
    sent = []
    startup_check(str(tmp_path / "nope.db"), send_fn=sent.append)
    assert len(sent) == 1
    assert "BOT START" in sent[0]


def test_rate_limiting():
    monitor_mod._last_alert_severity = None
    monitor_mod._last_alert_time = None
    assert should_send_alert("CRITICAL") is True
    assert should_send_alert("CRITICAL") is False
    assert should_send_alert("LOW") is True
