"""
Macro calendar maintenance alerts — §10.7 of CLAUDE_STRATEGY_SPEC_v3.

Scheduled checks against the macro calendar (SQLite) and emits Telegram alerts.
"""
import argparse
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("overlays.macro_calendar_monitor")

LOW_THRESHOLD_DAYS = 30
CRITICAL_THRESHOLD_DAYS = 14

SOURCE_URLS = {
    "Fed": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "BoE": "https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates",
    "ECB": "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html",
    "BLS": "https://www.bls.gov/schedule/news_release/",
}

_last_alert_severity = None
_last_alert_time = None


def check_calendar_freshness(db_path: str) -> dict:
    try:
        conn = sqlite3.connect(db_path)
    except Exception:
        return {
            "severity": "MISSING",
            "events_remaining": 0,
            "farthest_date": None,
            "days_of_coverage": 0,
            "message": "Macro calendar database unavailable",
        }

    try:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='macro_events'"
        )
        if cursor.fetchone()[0] == 0:
            conn.close()
            return {
                "severity": "MISSING",
                "events_remaining": 0,
                "farthest_date": None,
                "days_of_coverage": 0,
                "message": "Macro events table does not exist",
            }

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cursor = conn.execute(
            "SELECT COUNT(*), MAX(event_date) FROM macro_events WHERE event_date >= ?",
            (today_str,),
        )
        row = cursor.fetchone()
        conn.close()

        count = row[0] or 0
        farthest = row[1]

        if count == 0:
            return {
                "severity": "EMPTY",
                "events_remaining": 0,
                "farthest_date": None,
                "days_of_coverage": 0,
                "message": "No future macro events in calendar",
            }

        today = datetime.now(timezone.utc).date()
        from datetime import date as date_type
        farthest_date = date_type.fromisoformat(farthest)
        days_of_coverage = (farthest_date - today).days

        if days_of_coverage < CRITICAL_THRESHOLD_DAYS:
            severity = "CRITICAL"
            msg = (f"Macro calendar critically low: {count} events, "
                   f"{days_of_coverage} days of coverage remaining")
        elif days_of_coverage < LOW_THRESHOLD_DAYS:
            severity = "LOW"
            msg = (f"Macro calendar running low: {count} events, "
                   f"{days_of_coverage} days of coverage remaining")
        else:
            severity = "OK"
            msg = (f"Macro calendar OK: {count} events, "
                   f"{days_of_coverage} days of coverage")

        return {
            "severity": severity,
            "events_remaining": count,
            "farthest_date": farthest,
            "days_of_coverage": days_of_coverage,
            "message": msg,
        }
    except Exception as e:
        conn.close()
        return {
            "severity": "MISSING",
            "events_remaining": 0,
            "farthest_date": None,
            "days_of_coverage": 0,
            "message": f"Error reading macro calendar: {e}",
        }


def should_send_alert(severity: str) -> bool:
    """Rate-limit: one Telegram message per severity per 24 hours."""
    global _last_alert_severity, _last_alert_time
    now = datetime.now(timezone.utc)
    if (_last_alert_severity == severity and _last_alert_time is not None
            and (now - _last_alert_time).total_seconds() < 86400):
        return False
    _last_alert_severity = severity
    _last_alert_time = now
    return True


def format_monthly_report(status: dict) -> str:
    lines = [
        "📅 <b>Monthly Macro Calendar Report</b>",
        f"Status: <b>{status['severity']}</b>",
        f"Events remaining: {status['events_remaining']}",
        f"Days of coverage: {status['days_of_coverage']}",
        f"Farthest date: {status.get('farthest_date', 'N/A')}",
        "",
        "<b>Source pages for updates:</b>",
    ]
    for name, url in SOURCE_URLS.items():
        lines.append(f"  • {name}: {url}")
    return "\n".join(lines)


def run_check(db_path: str, send_fn=None, force: bool = False) -> dict:
    status = check_calendar_freshness(db_path)
    severity = status["severity"]

    if send_fn is None:
        logger.info("Calendar check: %s", status["message"])
        return status

    if severity in ("CRITICAL", "EMPTY", "MISSING"):
        if force or should_send_alert(severity):
            send_fn(f"🚨 {status['message']}")
    elif severity == "LOW":
        if force or should_send_alert(severity):
            send_fn(f"⚠️ {status['message']}")

    return status


def run_monthly_report(db_path: str, send_fn=None) -> dict:
    status = check_calendar_freshness(db_path)
    if send_fn is not None:
        report = format_monthly_report(status)
        send_fn(report)
    return status


def startup_check(db_path: str, send_fn=None) -> dict:
    """On bot boot, check calendar and alert on MISSING/EMPTY."""
    status = check_calendar_freshness(db_path)
    severity = status["severity"]

    if severity in ("MISSING", "EMPTY"):
        logger.error("Macro calendar issue at startup: %s", status["message"])
        if send_fn is not None:
            send_fn(f"🚨 BOT START: {status['message']}")
    else:
        logger.info("Macro calendar startup check: %s", status["message"])

    return status


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Macro calendar monitor")
    parser.add_argument("--check", action="store_true",
                        help="Run weekly freshness check")
    parser.add_argument("--monthly-report", action="store_true",
                        help="Run monthly status report")
    parser.add_argument("--db", default="data/trading.db",
                        help="Path to trading database")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.monthly_report:
        status = run_monthly_report(args.db)
    else:
        status = run_check(args.db)

    print(f"Status: {status['severity']} — {status['message']}")
