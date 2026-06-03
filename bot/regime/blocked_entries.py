"""
Regime filter — blocked-entry log.

Owns the `regime_blocked_entries` SQLite table. One row per entry signal
that the regime filter rejects (because the instrument's smoothed regime
is anything other than TRENDING, or because no regime row exists yet).
The row carries the price + bar timestamp at which the entry would have
fired, so the shadow-trade simulator (Commit 2) can replay the
"what-if-we-had-taken-it" path from the same point.

Schema is created lazily on first instantiation; safe to wire even when
the filter flag is off (the table simply stays empty).
"""
import sqlite3
from datetime import datetime, timezone
from typing import Optional


CREATE_BLOCKED_ENTRIES = """
CREATE TABLE IF NOT EXISTS regime_blocked_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    instrument TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    signal_confidence TEXT,
    smoothed_regime TEXT,
    classifier_rationale TEXT,
    would_have_entry_price REAL,
    bar_time TEXT,
    shadow_trade_id TEXT
)
"""


class RegimeBlockedEntriesLog:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._ensure_table()

    def _ensure_table(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(CREATE_BLOCKED_ENTRIES)

    def log(self,
            instrument: str,
            signal_type: str,
            signal_confidence: Optional[str],
            smoothed_regime: Optional[str],
            classifier_rationale: Optional[str],
            would_have_entry_price: Optional[float],
            bar_time: Optional[str]) -> int:
        """Record a blocked entry. Returns the row id so a caller can
        later attach a shadow_trade_id via attach_shadow_trade()."""
        ts = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO regime_blocked_entries "
                "(ts, instrument, signal_type, signal_confidence, "
                "smoothed_regime, classifier_rationale, "
                "would_have_entry_price, bar_time) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, instrument, signal_type, signal_confidence,
                 smoothed_regime, classifier_rationale,
                 would_have_entry_price, bar_time),
            )
            return cursor.lastrowid

    def attach_shadow_trade(self, row_id: int, shadow_trade_id: str) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE regime_blocked_entries "
                "SET shadow_trade_id = ? WHERE id = ?",
                (shadow_trade_id, row_id),
            )
