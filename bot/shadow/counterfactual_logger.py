"""
Counterfactual logger — §11.2 of CLAUDE_STRATEGY_SPEC_v3.

Logs shadow decisions to shadow_decisions table. Records what the
regime-aware system would have done vs what live actually did.
Shadow code writes ONLY to shadow_* tables.
"""
import json
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("shadow.counterfactual_logger")

CREATE_SHADOW_DECISIONS = """
CREATE TABLE IF NOT EXISTS shadow_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    instrument TEXT NOT NULL,
    bar_time TEXT NOT NULL,
    live_engine TEXT,
    live_signal_json TEXT,
    live_action_taken TEXT,
    live_trade_id TEXT,
    live_blocked_by TEXT,
    shadow_regime TEXT,
    shadow_confidence REAL,
    shadow_smoothed_regime TEXT,
    shadow_smoothed_days_in_regime INTEGER,
    shadow_overlays_active TEXT,
    shadow_engine_selected TEXT,
    shadow_signal_json TEXT,
    shadow_action_would_be TEXT,
    disagreement_type TEXT,
    hypothetical_trade_id TEXT,
    flag_snapshot_json TEXT
)
"""

CREATE_SHADOW_TRADES = """
CREATE TABLE IF NOT EXISTS shadow_hypothetical_trades (
    id TEXT PRIMARY KEY,
    instrument TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    opened_bar TEXT NOT NULL,
    entry_engine TEXT NOT NULL,
    entry_regime TEXT,
    entry_price REAL NOT NULL,
    entry_quantity REAL NOT NULL,
    entry_stop REAL,
    closed_at TEXT,
    closed_bar TEXT,
    exit_price REAL,
    exit_reason TEXT,
    pnl REAL,
    pnl_pct REAL,
    status TEXT NOT NULL
)
"""


class CounterfactualLogger:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        conn = sqlite3.connect(self._db_path)
        conn.execute(CREATE_SHADOW_DECISIONS)
        conn.execute(CREATE_SHADOW_TRADES)
        try:
            conn.execute(
                "ALTER TABLE shadow_decisions ADD COLUMN live_blocked_by TEXT"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
        conn.commit()
        conn.close()

    def log_decision(self, instrument: str, bar_time: str,
                     live_engine: Optional[str] = None,
                     live_signal: Optional[dict] = None,
                     live_action: Optional[str] = None,
                     live_trade_id: Optional[str] = None,
                     live_blocked_by: Optional[str] = None,
                     shadow_regime: Optional[str] = None,
                     shadow_confidence: Optional[float] = None,
                     shadow_smoothed_regime: Optional[str] = None,
                     shadow_smoothed_days: Optional[int] = None,
                     shadow_overlays_active: Optional[list] = None,
                     shadow_engine: Optional[str] = None,
                     shadow_signal: Optional[dict] = None,
                     shadow_action: Optional[str] = None,
                     disagreement_type: Optional[str] = None,
                     hypothetical_trade_id: Optional[str] = None,
                     flag_snapshot: Optional[dict] = None) -> int:
        conn = sqlite3.connect(self._db_path)
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            """INSERT INTO shadow_decisions
               (ts, instrument, bar_time, live_engine, live_signal_json,
                live_action_taken, live_trade_id, live_blocked_by,
                shadow_regime, shadow_confidence, shadow_smoothed_regime,
                shadow_smoothed_days_in_regime, shadow_overlays_active,
                shadow_engine_selected, shadow_signal_json,
                shadow_action_would_be, disagreement_type,
                hypothetical_trade_id, flag_snapshot_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now, instrument, bar_time, live_engine,
             json.dumps(live_signal) if live_signal else None,
             live_action, live_trade_id, live_blocked_by,
             shadow_regime, shadow_confidence, shadow_smoothed_regime,
             shadow_smoothed_days,
             json.dumps(shadow_overlays_active) if shadow_overlays_active else None,
             shadow_engine,
             json.dumps(shadow_signal) if shadow_signal else None,
             shadow_action, disagreement_type,
             hypothetical_trade_id,
             json.dumps(flag_snapshot) if flag_snapshot else None),
        )
        row_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return row_id

    def open_hypothetical(self, trade_id: str, instrument: str,
                          bar_time: str, engine: str, regime: Optional[str],
                          price: float, quantity: float,
                          stop: Optional[float] = None) -> None:
        conn = sqlite3.connect(self._db_path)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO shadow_hypothetical_trades
               (id, instrument, opened_at, opened_bar, entry_engine,
                entry_regime, entry_price, entry_quantity, entry_stop,
                status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')""",
            (trade_id, instrument, now, bar_time, engine, regime,
             price, quantity, stop),
        )
        conn.commit()
        conn.close()

    def close_hypothetical(self, trade_id: str, bar_time: str,
                           exit_price: float, exit_reason: str,
                           pnl: float, pnl_pct: float) -> None:
        conn = sqlite3.connect(self._db_path)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """UPDATE shadow_hypothetical_trades
               SET closed_at = ?, closed_bar = ?, exit_price = ?,
                   exit_reason = ?, pnl = ?, pnl_pct = ?, status = 'CLOSED'
               WHERE id = ?""",
            (now, bar_time, exit_price, exit_reason, pnl, pnl_pct, trade_id),
        )
        conn.commit()
        conn.close()

    def abandon_hypothetical(self, trade_id: str, reason: str) -> None:
        conn = sqlite3.connect(self._db_path)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """UPDATE shadow_hypothetical_trades
               SET closed_at = ?, exit_reason = ?, status = 'ABANDONED'
               WHERE id = ?""",
            (now, reason, trade_id),
        )
        conn.commit()
        conn.close()

    def get_open_hypotheticals(self, instrument: Optional[str] = None) -> list:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        if instrument:
            cursor = conn.execute(
                "SELECT * FROM shadow_hypothetical_trades WHERE status = 'OPEN' AND instrument = ?",
                (instrument,),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM shadow_hypothetical_trades WHERE status = 'OPEN'"
            )
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows

    def table_names(self) -> set:
        """Return set of table names this logger writes to (for isolation testing)."""
        return {"shadow_decisions", "shadow_hypothetical_trades"}
