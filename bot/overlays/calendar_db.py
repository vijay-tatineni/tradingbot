"""
SQLite schema for macro calendar — §10.2, §15.4.

Calendar data stored in SQLite (not file-based), shared by overlays and UI.
"""
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("overlays.calendar_db")

CREATE_MACRO_EVENTS = """
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
"""


class CalendarDB:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        conn = sqlite3.connect(self._db_path)
        conn.execute(CREATE_MACRO_EVENTS)
        conn.commit()
        conn.close()

    def get_macro_events(self, from_date: Optional[str] = None) -> list:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        if from_date:
            cursor = conn.execute(
                "SELECT * FROM macro_events WHERE event_date >= ? ORDER BY event_date",
                (from_date,),
            )
        else:
            cursor = conn.execute("SELECT * FROM macro_events ORDER BY event_date")
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows

    def add_macro_event(self, event_date: str, name: str, source: str,
                        event_time: Optional[str] = None,
                        impact: str = "medium") -> int:
        conn = sqlite3.connect(self._db_path)
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            """INSERT INTO macro_events
               (event_date, event_time, name, source, impact, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (event_date, event_time, name, source, impact, now, now),
        )
        event_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return event_id
