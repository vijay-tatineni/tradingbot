"""
Position metadata persistence — §12.1 of CLAUDE_STRATEGY_SPEC_v3.

Composite PK (position_id, fill_id). One row per fill.
Aggregate quantity = SUM(entry_quantity) WHERE position_id = ?.
"""
import json
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional

from bot.regime.models import PositionMetadata

logger = logging.getLogger("shadow.position_metadata_store")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS position_metadata (
    position_id TEXT NOT NULL,
    fill_id TEXT NOT NULL,
    instrument TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    entry_quantity REAL NOT NULL,
    entry_strategy TEXT NOT NULL,
    entry_regime TEXT,
    entry_overlays_active TEXT,
    entry_prompt_version TEXT,
    exit_policy TEXT NOT NULL,
    PRIMARY KEY (position_id, fill_id)
)
"""

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_position_metadata_position
    ON position_metadata(position_id)
"""


class PositionMetadataStore:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._ensure_table()
        self._fill_counters: dict[str, int] = {}

    def _ensure_table(self) -> None:
        conn = sqlite3.connect(self._db_path)
        conn.execute(CREATE_TABLE)
        conn.execute(CREATE_INDEX)
        conn.commit()
        conn.close()

    def _next_fill_id(self, position_id: str) -> str:
        seq = self._fill_counters.get(position_id, 0)
        self._fill_counters[position_id] = seq + 1
        return f"{position_id}-{seq:04d}"

    def generate_fill_id(self, position_id: str,
                         broker_execution_id: Optional[str] = None) -> str:
        if broker_execution_id:
            return broker_execution_id
        return self._next_fill_id(position_id)

    def persist(self, metadata: PositionMetadata) -> None:
        conn = sqlite3.connect(self._db_path)
        overlays_json = json.dumps(metadata.entry_overlays_active)
        conn.execute(
            """INSERT OR REPLACE INTO position_metadata
               (position_id, fill_id, instrument, entry_time, entry_price,
                entry_quantity, entry_strategy, entry_regime,
                entry_overlays_active, entry_prompt_version, exit_policy)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (metadata.position_id, metadata.fill_id, metadata.instrument,
             metadata.entry_time.isoformat(), metadata.entry_price,
             metadata.entry_quantity, metadata.entry_strategy,
             metadata.entry_regime, overlays_json,
             metadata.entry_prompt_version, metadata.exit_policy),
        )
        conn.commit()
        conn.close()
        logger.info("Persisted metadata: %s/%s %s",
                     metadata.position_id, metadata.fill_id,
                     metadata.entry_strategy)

    def get_fills(self, position_id: str) -> list[PositionMetadata]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM position_metadata WHERE position_id = ? ORDER BY fill_id",
            (position_id,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [_row_to_metadata(row) for row in rows]

    def aggregate_quantity(self, position_id: str) -> float:
        conn = sqlite3.connect(self._db_path)
        cursor = conn.execute(
            "SELECT SUM(entry_quantity) FROM position_metadata WHERE position_id = ?",
            (position_id,),
        )
        result = cursor.fetchone()[0]
        conn.close()
        return result or 0.0


def _row_to_metadata(row: sqlite3.Row) -> PositionMetadata:
    overlays = json.loads(row["entry_overlays_active"]) if row["entry_overlays_active"] else []
    return PositionMetadata(
        position_id=row["position_id"],
        fill_id=row["fill_id"],
        instrument=row["instrument"],
        entry_time=datetime.fromisoformat(row["entry_time"]),
        entry_price=row["entry_price"],
        entry_quantity=row["entry_quantity"],
        entry_strategy=row["entry_strategy"],
        entry_regime=row["entry_regime"],
        entry_overlays_active=overlays,
        entry_prompt_version=row["entry_prompt_version"],
        exit_policy=row["exit_policy"],
    )
