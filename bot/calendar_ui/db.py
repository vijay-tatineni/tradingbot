"""
§15.4.2: Calendar UI database operations.

Extends the existing macro_events table with region column.
Adds calendar_edit_audit table for forensic review.
All audit rows are written in the same transaction as the change.
"""
import json
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("calendar_ui.db")

CREATE_MACRO_EVENTS_V2 = """
CREATE TABLE IF NOT EXISTS macro_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date TEXT NOT NULL,
    event_time TEXT,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    impact TEXT DEFAULT 'medium',
    region TEXT DEFAULT 'US',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (event_date, event_time, name, region)
)
"""

CREATE_AUDIT = """
CREATE TABLE IF NOT EXISTS calendar_edit_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    user_jwt_sub TEXT NOT NULL,
    calendar_type TEXT NOT NULL,
    action TEXT NOT NULL,
    target_id INTEGER,
    before_json TEXT,
    after_json TEXT,
    affected_count INTEGER,
    notes TEXT NOT NULL DEFAULT ''
)
"""

CREATE_AUDIT_INDEX = "CREATE INDEX IF NOT EXISTS idx_audit_ts ON calendar_edit_audit(ts)"


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables(db_path: str) -> None:
    conn = _connect(db_path)
    conn.execute(CREATE_MACRO_EVENTS_V2)
    conn.execute(CREATE_AUDIT)
    conn.execute(CREATE_AUDIT_INDEX)
    _maybe_add_region_column(conn)
    conn.commit()
    conn.close()


def _maybe_add_region_column(conn: sqlite3.Connection) -> None:
    cursor = conn.execute("PRAGMA table_info(macro_events)")
    columns = {row[1] for row in cursor.fetchall()}
    if "region" not in columns:
        conn.execute("ALTER TABLE macro_events ADD COLUMN region TEXT DEFAULT 'US'")
        logger.info("Added 'region' column to macro_events table")


def list_events(db_path: str, from_date: Optional[str] = None,
                to_date: Optional[str] = None) -> list[dict]:
    conn = _connect(db_path)
    query = "SELECT * FROM macro_events"
    params = []
    conditions = []
    if from_date:
        conditions.append("event_date >= ?")
        params.append(from_date)
    if to_date:
        conditions.append("event_date <= ?")
        params.append(to_date)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY event_date, event_time"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_event(db_path: str, event_id: int) -> Optional[dict]:
    conn = _connect(db_path)
    row = conn.execute("SELECT * FROM macro_events WHERE id = ?", (event_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_event(db_path: str, data: dict, user: str) -> dict:
    conn = _connect(db_path)
    now = datetime.now(timezone.utc).isoformat()
    try:
        cursor = conn.execute(
            """INSERT INTO macro_events
               (event_date, event_time, name, source, impact, region, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (data["date"], data["time_utc"], data["event"],
             data.get("source", "manual"), data.get("severity", "medium"),
             data.get("region", "US"), now, now),
        )
        event_id = cursor.lastrowid
        after = dict(conn.execute("SELECT * FROM macro_events WHERE id = ?", (event_id,)).fetchone())

        conn.execute(
            """INSERT INTO calendar_edit_audit
               (ts, user_jwt_sub, calendar_type, action, target_id, before_json, after_json, notes)
               VALUES (?, ?, 'macro', 'create', ?, NULL, ?, ?)""",
            (now, user, event_id, json.dumps(after), data.get("notes", "")),
        )
        conn.commit()
        return after
    except sqlite3.IntegrityError:
        conn.rollback()
        raise DuplicateEventError(
            f"Event already exists: {data['date']} {data['time_utc']} {data['event']} {data.get('region', 'US')}"
        )
    finally:
        conn.close()


def update_event(db_path: str, event_id: int, data: dict, user: str) -> Optional[dict]:
    conn = _connect(db_path)
    now = datetime.now(timezone.utc).isoformat()

    before_row = conn.execute("SELECT * FROM macro_events WHERE id = ?", (event_id,)).fetchone()
    if not before_row:
        conn.close()
        return None

    before = dict(before_row)

    try:
        conn.execute(
            """UPDATE macro_events
               SET event_date = ?, event_time = ?, name = ?, source = ?,
                   impact = ?, region = ?, updated_at = ?
               WHERE id = ?""",
            (data["date"], data["time_utc"], data["event"],
             data.get("source", before.get("source", "manual")),
             data.get("severity", before.get("impact", "medium")),
             data.get("region", before.get("region", "US")),
             now, event_id),
        )
        after = dict(conn.execute("SELECT * FROM macro_events WHERE id = ?", (event_id,)).fetchone())

        conn.execute(
            """INSERT INTO calendar_edit_audit
               (ts, user_jwt_sub, calendar_type, action, target_id, before_json, after_json, notes)
               VALUES (?, ?, 'macro', 'update', ?, ?, ?, ?)""",
            (now, user, event_id, json.dumps(before), json.dumps(after), data.get("notes", "")),
        )
        conn.commit()
        return after
    except sqlite3.IntegrityError:
        conn.rollback()
        raise DuplicateEventError("Update would create duplicate event")
    finally:
        conn.close()


def delete_event(db_path: str, event_id: int, user: str) -> Optional[dict]:
    conn = _connect(db_path)
    now = datetime.now(timezone.utc).isoformat()

    before_row = conn.execute("SELECT * FROM macro_events WHERE id = ?", (event_id,)).fetchone()
    if not before_row:
        conn.close()
        return None

    before = dict(before_row)
    conn.execute("DELETE FROM macro_events WHERE id = ?", (event_id,))
    conn.execute(
        """INSERT INTO calendar_edit_audit
           (ts, user_jwt_sub, calendar_type, action, target_id, before_json, after_json, notes)
           VALUES (?, ?, 'macro', 'delete', ?, ?, NULL, '')""",
        (now, user, event_id, json.dumps(before)),
    )
    conn.commit()
    conn.close()
    return before


def bulk_delete(db_path: str, event_ids: list[int], user: str) -> int:
    conn = _connect(db_path)
    now = datetime.now(timezone.utc).isoformat()

    placeholders = ",".join("?" * len(event_ids))
    rows = conn.execute(
        f"SELECT * FROM macro_events WHERE id IN ({placeholders})", event_ids
    ).fetchall()
    before_list = [dict(r) for r in rows]

    conn.execute(f"DELETE FROM macro_events WHERE id IN ({placeholders})", event_ids)
    conn.execute(
        """INSERT INTO calendar_edit_audit
           (ts, user_jwt_sub, calendar_type, action, target_id, before_json, after_json, affected_count, notes)
           VALUES (?, ?, 'macro', 'bulk_delete', NULL, ?, NULL, ?, '')""",
        (now, user, json.dumps(before_list), len(before_list)),
    )
    conn.commit()
    conn.close()
    return len(before_list)


def csv_import(db_path: str, rows: list[dict], user: str) -> dict:
    """§15.4.7: Transaction-wrapped CSV import."""
    conn = _connect(db_path)
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    skipped = 0
    errors = []

    try:
        for i, row in enumerate(rows):
            from bot.calendar_ui.validation import validate_macro_event
            row_errors = validate_macro_event(row)
            if row_errors:
                errors.append({"row": i + 1, "errors": row_errors})
                continue

            try:
                conn.execute(
                    """INSERT INTO macro_events
                       (event_date, event_time, name, source, impact, region, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (row["date"], row["time_utc"], row["event"],
                     row.get("source", "csv_import"), row.get("severity", "medium"),
                     row.get("region", "US"), now, now),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1

        conn.execute(
            """INSERT INTO calendar_edit_audit
               (ts, user_jwt_sub, calendar_type, action, target_id, before_json, after_json, affected_count, notes)
               VALUES (?, ?, 'macro', 'bulk_import', NULL, NULL, NULL, ?, ?)""",
            (now, user, inserted, f"imported={inserted}, skipped={skipped}, errors={len(errors)}"),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "inserted": inserted,
        "skipped_duplicates": skipped,
        "validation_errors": errors,
    }


def get_audit_log(db_path: str, limit: int = 50, offset: int = 0,
                  calendar_type: Optional[str] = None,
                  action: Optional[str] = None) -> list[dict]:
    conn = _connect(db_path)
    query = "SELECT * FROM calendar_edit_audit"
    params = []
    conditions = []
    if calendar_type:
        conditions.append("calendar_type = ?")
        params.append(calendar_type)
    if action:
        conditions.append("action = ?")
        params.append(action)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY ts DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


class DuplicateEventError(Exception):
    pass
