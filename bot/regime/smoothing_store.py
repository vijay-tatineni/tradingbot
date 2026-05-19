"""
Persistent store for SmoothedRegimeState — one row per instrument.

The smoothing module (bot/regime/smoothing.py) is pure: update(prior, classification)
returns a new state. This module owns the prior. Without it the scheduler has nothing
to fold each new classification into, and the orchestrator's router has nothing to read.
"""
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from bot.regime.models import SmoothedRegimeState

logger = logging.getLogger("regime.smoothing_store")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS smoothed_regime_state (
    instrument        TEXT PRIMARY KEY,
    effective_regime  TEXT NOT NULL,
    source_regime     TEXT NOT NULL,
    days_in_regime    INTEGER NOT NULL,
    last_changed_at   TEXT NOT NULL,
    confidence        REAL NOT NULL,
    pending_regime    TEXT,
    pending_days      INTEGER NOT NULL DEFAULT 0,
    regime_history    TEXT NOT NULL DEFAULT '[]',
    updated_at        TEXT NOT NULL
)
"""


class SmoothedStateStore:
    def __init__(self, db_path: str):
        self._db_path = db_path
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(CREATE_TABLE)

    def get_latest(self, instrument: str) -> Optional[SmoothedRegimeState]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT effective_regime, source_regime, days_in_regime, "
                "last_changed_at, confidence, pending_regime, pending_days, "
                "regime_history FROM smoothed_regime_state WHERE instrument = ?",
                (instrument,),
            ).fetchone()
        if row is None:
            return None
        return SmoothedRegimeState(
            instrument=instrument,
            effective_regime=row[0],
            source_regime=row[1],
            days_in_regime=row[2],
            last_changed_at=datetime.fromisoformat(row[3]),
            confidence=row[4],
            pending_regime=row[5],
            pending_days=row[6],
            regime_history=json.loads(row[7]),
        )

    def put(self, state: SmoothedRegimeState) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO smoothed_regime_state "
                "(instrument, effective_regime, source_regime, days_in_regime, "
                "last_changed_at, confidence, pending_regime, pending_days, "
                "regime_history, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    state.instrument,
                    state.effective_regime,
                    state.source_regime,
                    state.days_in_regime,
                    state.last_changed_at.isoformat(),
                    state.confidence,
                    state.pending_regime,
                    state.pending_days,
                    json.dumps(list(state.regime_history)),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
