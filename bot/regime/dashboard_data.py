"""
§15.1: Dashboard data queries for regime observability tabs.

Pure data-access functions that read from existing tables. No Flask
dependency — the API server imports and calls these.
"""
import sqlite3
import json
import logging
from typing import Optional

logger = logging.getLogger("regime.dashboard")


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def get_regime_states(db_path: str, instruments: Optional[list] = None) -> list:
    conn = _connect(db_path)
    try:
        if instruments:
            placeholders = ",".join("?" * len(instruments))
            rows = conn.execute(
                f"SELECT * FROM regime_classification_cache "
                f"WHERE instrument IN ({placeholders}) "
                f"ORDER BY created_at DESC",
                instruments,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM regime_classification_cache "
                "ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def get_active_overlays(db_path: str) -> list:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM instrument_entry_pauses "
            "WHERE cleared_at IS NULL "
            "ORDER BY paused_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def get_degradation_events(db_path: str, limit: int = 50) -> list:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM degradation_events ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def get_instrument_pauses(db_path: str) -> list:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM instrument_entry_pauses ORDER BY paused_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def get_shadow_decisions(db_path: str, limit: int = 50,
                         instrument: Optional[str] = None) -> list:
    conn = _connect(db_path)
    try:
        if instrument:
            rows = conn.execute(
                "SELECT * FROM shadow_decisions "
                "WHERE instrument = ? ORDER BY ts DESC LIMIT ?",
                (instrument, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM shadow_decisions ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def get_shadow_vs_live_summary(db_path: str) -> dict:
    conn = _connect(db_path)
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM shadow_decisions"
        ).fetchone()[0]
        disagreements = conn.execute(
            "SELECT COUNT(*) FROM shadow_decisions "
            "WHERE disagreement_type IS NOT NULL AND disagreement_type != ''"
        ).fetchone()[0]
        agreements = total - disagreements
        return {
            "total_decisions": total,
            "agreements": agreements,
            "disagreements": disagreements,
            "agreement_pct": (agreements / total * 100) if total > 0 else 0,
        }
    except sqlite3.OperationalError:
        return {"total_decisions": 0, "agreements": 0, "disagreements": 0, "agreement_pct": 0}
    finally:
        conn.close()


def get_routing_history(db_path: str, limit: int = 50) -> list:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT ts, instrument, shadow_regime, shadow_engine_selected, "
            "shadow_action_would_be, live_engine, live_action_taken, "
            "disagreement_type FROM shadow_decisions "
            "ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
