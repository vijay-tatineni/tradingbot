"""
Instrument pause registry — §13.4 of CLAUDE_STRATEGY_SPEC_v3.

DB-persisted registry of instruments paused by overlay hard-failures.
Survives restarts. Recovery only via `bot recover-overlay <name>` CLI.
"""
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("degradation.instrument_pause_registry")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS instrument_entry_pauses (
    instrument TEXT PRIMARY KEY,
    paused_at TEXT NOT NULL,
    paused_by_overlay TEXT NOT NULL,
    reason TEXT NOT NULL,
    cleared_at TEXT,
    cleared_by TEXT
)
"""


class InstrumentPauseRegistry:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._ensure_table()

    def _ensure_table(self) -> None:
        conn = sqlite3.connect(self._db_path)
        conn.execute(CREATE_TABLE)
        conn.commit()
        conn.close()

    def pause(self, instrument: str, reason: str, paused_by_overlay: str) -> None:
        conn = sqlite3.connect(self._db_path)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT OR REPLACE INTO instrument_entry_pauses
               (instrument, paused_at, paused_by_overlay, reason, cleared_at, cleared_by)
               VALUES (?, ?, ?, ?, NULL, NULL)""",
            (instrument, now, paused_by_overlay, reason),
        )
        conn.commit()
        conn.close()
        logger.warning("Paused instrument %s: %s (by %s)", instrument, reason,
                        paused_by_overlay)

    def is_paused(self, instrument: str) -> bool:
        conn = sqlite3.connect(self._db_path)
        cursor = conn.execute(
            "SELECT 1 FROM instrument_entry_pauses WHERE instrument = ? AND cleared_at IS NULL",
            (instrument,),
        )
        result = cursor.fetchone() is not None
        conn.close()
        return result

    def pause_reason(self, instrument: str) -> Optional[str]:
        conn = sqlite3.connect(self._db_path)
        cursor = conn.execute(
            "SELECT reason FROM instrument_entry_pauses WHERE instrument = ? AND cleared_at IS NULL",
            (instrument,),
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None

    def clear(self, instrument: str, cleared_by: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "UPDATE instrument_entry_pauses SET cleared_at = ?, cleared_by = ? "
            "WHERE instrument = ? AND cleared_at IS NULL",
            (now, cleared_by, instrument),
        )
        conn.commit()
        conn.close()
        logger.info("Cleared pause on %s (by %s)", instrument, cleared_by)

    def clear_by_overlay(self, overlay_name: str, cleared_by: str) -> list:
        """Clear all pauses set by a specific overlay. Returns list of cleared instruments."""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.execute(
            "SELECT instrument FROM instrument_entry_pauses "
            "WHERE paused_by_overlay = ? AND cleared_at IS NULL",
            (overlay_name,),
        )
        instruments = [row[0] for row in cursor.fetchall()]

        if instruments:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE instrument_entry_pauses SET cleared_at = ?, cleared_by = ? "
                "WHERE paused_by_overlay = ? AND cleared_at IS NULL",
                (now, cleared_by, overlay_name),
            )
            conn.commit()

        conn.close()
        return instruments

    def list_paused(self) -> list:
        """Returns list of (instrument, paused_by_overlay, reason) tuples."""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.execute(
            "SELECT instrument, paused_by_overlay, reason FROM instrument_entry_pauses "
            "WHERE cleared_at IS NULL"
        )
        results = cursor.fetchall()
        conn.close()
        return results
