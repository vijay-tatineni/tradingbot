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


def _latest_shadow_smoothed(conn: sqlite3.Connection) -> dict:
    try:
        rows = conn.execute(
            "SELECT s.instrument, s.shadow_smoothed_regime, "
            "s.shadow_smoothed_days_in_regime "
            "FROM shadow_decisions s "
            "INNER JOIN (SELECT instrument, MAX(id) AS max_id "
            "FROM shadow_decisions GROUP BY instrument) latest "
            "ON s.instrument = latest.instrument AND s.id = latest.max_id"
        ).fetchall()
        return {
            r["instrument"]: (
                r["shadow_smoothed_regime"],
                r["shadow_smoothed_days_in_regime"],
            )
            for r in rows
        }
    except sqlite3.OperationalError:
        return {}


def get_regime_states(db_path: str, instruments: Optional[list] = None) -> list:
    """
    Latest classification per instrument. classification_json is parsed and
    flattened into top-level keys (raw_regime, confidence). Smoothed values
    are merged in from the latest shadow_decisions row per instrument when
    available. _raw_classification_json is retained for callers that need
    the original payload.
    """
    conn = _connect(db_path)
    try:
        try:
            if instruments:
                placeholders = ",".join("?" * len(instruments))
                cache_rows = conn.execute(
                    f"SELECT * FROM regime_classification_cache "
                    f"WHERE instrument IN ({placeholders}) "
                    f"ORDER BY created_at DESC",
                    instruments,
                ).fetchall()
            else:
                cache_rows = conn.execute(
                    "SELECT * FROM regime_classification_cache "
                    "ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
        except sqlite3.OperationalError:
            return []

        smoothed_by_instr = _latest_shadow_smoothed(conn)

        result = []
        for row in cache_rows:
            raw = dict(row)
            try:
                parsed = json.loads(raw["classification_json"])
            except (json.JSONDecodeError, TypeError):
                parsed = {}
            smoothed, days = smoothed_by_instr.get(raw["instrument"], (None, None))
            result.append({
                "instrument": raw["instrument"],
                "raw_regime": parsed.get("raw_regime"),
                "confidence": parsed.get("confidence"),
                "smoothed_regime": smoothed,
                "days_in_regime": days,
                "last_classified": raw.get("created_at"),
                "trading_date": raw.get("trading_date"),
                "_raw_classification_json": raw["classification_json"],
            })
        return result
    finally:
        conn.close()


def get_active_overlays(db_path: str) -> list:
    """
    Active rows from instrument_entry_pauses (cleared_at IS NULL).

    Note: this is NOT the same as "active overlays". It returns instruments
    currently paused due to prior overlay hard-failures. For the live
    overlays view (e.g. MACRO_LOCKOUT firing now because of a calendar
    event), call bot.overlays.registry.active_overlays() directly — that
    function needs runtime context the api_server does not have for
    DATA_QUALITY/LOW_LIQUIDITY, but macro events load from the DB.
    """
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


def get_instrument_pauses(db_path: str, active_only: bool = False) -> list:
    """
    All paused instruments by default. When active_only=True, restrict to
    rows with cleared_at IS NULL.
    """
    conn = _connect(db_path)
    try:
        if active_only:
            sql = (
                "SELECT * FROM instrument_entry_pauses "
                "WHERE cleared_at IS NULL "
                "ORDER BY paused_at DESC"
            )
        else:
            sql = (
                "SELECT * FROM instrument_entry_pauses "
                "ORDER BY paused_at DESC"
            )
        rows = conn.execute(sql).fetchall()
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


def get_current_routing(db_path: str) -> list:
    """
    Most recent shadow_decisions row per distinct instrument. Returns
    instrument, smoothed_regime, selected_engine, allow_new_entries,
    block_reason, ts.

    allow_new_entries is False when the selected engine is NoOpEngine or
    when shadow_overlays_active is non-empty. block_reason names the
    overlay(s) or notes the NoOp selection.
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT s.* FROM shadow_decisions s "
            "INNER JOIN (SELECT instrument, MAX(id) AS max_id "
            "FROM shadow_decisions GROUP BY instrument) latest "
            "ON s.instrument = latest.instrument AND s.id = latest.max_id "
            "ORDER BY s.instrument"
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            engine = d.get("shadow_engine_selected") or ""
            overlays_raw = d.get("shadow_overlays_active") or ""
            overlays_present = overlays_raw and overlays_raw not in ("[]", "null")
            allow = engine and engine != "NoOpEngine" and not overlays_present
            if allow:
                block_reason = ""
            elif overlays_present:
                block_reason = f"Overlay(s): {overlays_raw}"
            elif engine == "NoOpEngine":
                block_reason = "Router selected NoOpEngine"
            else:
                block_reason = "No engine selected"
            result.append({
                "instrument": d["instrument"],
                "smoothed_regime": d.get("shadow_smoothed_regime"),
                "selected_engine": engine,
                "allow_new_entries": bool(allow),
                "block_reason": block_reason,
                "ts": d.get("ts"),
            })
        return result
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
