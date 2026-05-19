"""
SQLite cache for regime classifications — §9.4 of CLAUDE_STRATEGY_SPEC_v3.
"""
import json
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional

from bot.regime.models import RegimeClassification

logger = logging.getLogger("regime.cache")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS regime_classification_cache (
    instrument     TEXT NOT NULL,
    trading_date   TEXT NOT NULL,
    input_hash     TEXT NOT NULL,
    classification_json TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (instrument, trading_date, input_hash)
)
"""


class RegimeCache:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._ensure_table()

    def _ensure_table(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(CREATE_TABLE)

    def get(self, instrument: str, trading_date: str,
            input_hash: str) -> Optional[RegimeClassification]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT classification_json FROM regime_classification_cache "
                "WHERE instrument = ? AND trading_date = ? AND input_hash = ?",
                (instrument, trading_date, input_hash),
            ).fetchone()

        if row is None:
            return None

        data = json.loads(row[0])
        return RegimeClassification(
            instrument=data["instrument"],
            classified_at=datetime.fromisoformat(data["classified_at"]),
            trading_date=data["trading_date"],
            raw_regime=data["raw_regime"],
            confidence=data["confidence"],
            rationale=data["rationale"],
            features=data["features"],
            model_version=data["model_version"],
            prompt_version=data["prompt_version"],
            input_hash=data["input_hash"],
            cache_hit=True,
        )

    def put(self, classification: RegimeClassification) -> None:
        data = {
            "instrument": classification.instrument,
            "classified_at": classification.classified_at.isoformat(),
            "trading_date": classification.trading_date,
            "raw_regime": classification.raw_regime,
            "confidence": classification.confidence,
            "rationale": classification.rationale,
            "features": classification.features,
            "model_version": classification.model_version,
            "prompt_version": classification.prompt_version,
            "input_hash": classification.input_hash,
        }
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO regime_classification_cache "
                "(instrument, trading_date, input_hash, classification_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    classification.instrument,
                    classification.trading_date,
                    classification.input_hash,
                    json.dumps(data),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
