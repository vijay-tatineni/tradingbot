"""
Degradation events DB — §13.5 of CLAUDE_STRATEGY_SPEC_v3.

No silent fallback rule:
  Soft → INFO log
  Hard → ERROR + critical Telegram + degradation_events row
"""
import sqlite3
import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("degradation.events")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS degradation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    component TEXT NOT NULL,
    severity TEXT NOT NULL,
    trigger_reason TEXT NOT NULL,
    action_taken TEXT NOT NULL,
    flag_disabled TEXT,
    instruments_paused TEXT,
    recovery_instructions TEXT
)
"""


class DegradationEventStore:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._ensure_table()

    def _ensure_table(self) -> None:
        conn = sqlite3.connect(self._db_path)
        conn.execute(CREATE_TABLE)
        conn.commit()
        conn.close()

    def record(self, component: str, severity: str, trigger_reason: str,
               action_taken: str, flag_disabled: Optional[str] = None,
               instruments_paused: Optional[list] = None,
               recovery_instructions: Optional[str] = None) -> int:
        conn = sqlite3.connect(self._db_path)
        now = datetime.now(timezone.utc).isoformat()
        paused_json = json.dumps(instruments_paused) if instruments_paused else None
        cursor = conn.execute(
            """INSERT INTO degradation_events
               (ts, component, severity, trigger_reason, action_taken,
                flag_disabled, instruments_paused, recovery_instructions)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (now, component, severity, trigger_reason, action_taken,
             flag_disabled, paused_json, recovery_instructions),
        )
        event_id = cursor.lastrowid
        conn.commit()
        conn.close()
        logger.info("Recorded degradation event #%d: %s %s", event_id, severity, component)
        return event_id

    def recent(self, limit: int = 20) -> list:
        conn = sqlite3.connect(self._db_path)
        cursor = conn.execute(
            "SELECT * FROM degradation_events ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        conn.close()
        return rows
