"""
§15.4.4: Calendar UI Flask Blueprint.

All routes under /api/calendar/. Behind existing JWT auth.
Gated by enable_calendar_ui flag.
"""
import csv
import io
import json
import logging

import jwt as pyjwt
from flask import Blueprint, jsonify, request

from bot.calendar_ui.validation import validate_macro_event
from bot.calendar_ui import db as calendar_db

logger = logging.getLogger("calendar_ui.routes")

calendar_bp = Blueprint("calendar", __name__, url_prefix="/api/calendar")

_db_path = None
_jwt_secret = None


def init_calendar_routes(db_path: str, jwt_secret: str) -> None:
    global _db_path, _jwt_secret
    _db_path = db_path
    _jwt_secret = jwt_secret
    calendar_db.ensure_tables(db_path)


def _get_user() -> str:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        try:
            payload = pyjwt.decode(header[7:], _jwt_secret, algorithms=["HS256"])
            return payload.get("sub", "unknown")
        except Exception:
            pass
    return "unknown"


def _require_auth(f):
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid token"}), 401
        try:
            pyjwt.decode(header[7:], _jwt_secret, algorithms=["HS256"])
        except pyjwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except pyjwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401
        return f(*args, **kwargs)

    return decorated


@calendar_bp.route("/macro", methods=["GET"])
@_require_auth
def list_macro():
    from_date = request.args.get("from")
    to_date = request.args.get("to")
    events = calendar_db.list_events(_db_path, from_date=from_date, to_date=to_date)
    return jsonify(events)


@calendar_bp.route("/macro/<int:event_id>", methods=["GET"])
@_require_auth
def get_macro(event_id):
    event = calendar_db.get_event(_db_path, event_id)
    if not event:
        return jsonify({"error": "Not found"}), 404
    return jsonify(event)


@calendar_bp.route("/macro", methods=["POST"])
@_require_auth
def create_macro():
    data = request.get_json(silent=True) or {}
    errors = validate_macro_event(data)
    if errors:
        return jsonify({"error": "validation_failed", "details": errors}), 400

    try:
        event = calendar_db.create_event(_db_path, data, _get_user())
        return jsonify(event), 201
    except calendar_db.DuplicateEventError as e:
        return jsonify({"error": "duplicate", "message": str(e)}), 409


@calendar_bp.route("/macro/<int:event_id>", methods=["PUT"])
@_require_auth
def update_macro(event_id):
    data = request.get_json(silent=True) or {}
    errors = validate_macro_event(data)
    if errors:
        return jsonify({"error": "validation_failed", "details": errors}), 400

    try:
        event = calendar_db.update_event(_db_path, event_id, data, _get_user())
        if not event:
            return jsonify({"error": "Not found"}), 404
        return jsonify(event)
    except calendar_db.DuplicateEventError as e:
        return jsonify({"error": "duplicate", "message": str(e)}), 409


@calendar_bp.route("/macro/<int:event_id>", methods=["DELETE"])
@_require_auth
def delete_macro(event_id):
    event = calendar_db.get_event(_db_path, event_id)
    if not event:
        return jsonify({"error": "Not found"}), 404

    confirm = request.headers.get("X-Confirm-Event", "")
    if confirm != event["name"]:
        return jsonify({
            "error": "Confirmation required",
            "message": f"Set X-Confirm-Event header to '{event['name']}' to confirm deletion",
        }), 400

    calendar_db.delete_event(_db_path, event["id"], _get_user())
    return jsonify({"deleted": True, "id": event_id})


@calendar_bp.route("/macro/bulk", methods=["DELETE"])
@_require_auth
def bulk_delete_macro():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids", [])
    if not ids:
        return jsonify({"error": "No IDs provided"}), 400

    confirm = request.headers.get("X-Confirm-Count", "")
    if confirm != str(len(ids)):
        return jsonify({
            "error": "Confirmation required",
            "message": f"Set X-Confirm-Count header to '{len(ids)}' to confirm deletion",
        }), 400

    deleted = calendar_db.bulk_delete(_db_path, ids, _get_user())
    return jsonify({"deleted": deleted})


@calendar_bp.route("/macro/import", methods=["POST"])
@_require_auth
def import_csv():
    if "file" not in request.files:
        raw = request.get_data(as_text=True)
        if not raw:
            return jsonify({"error": "No file or CSV data provided"}), 400
    else:
        raw = request.files["file"].read().decode("utf-8")

    reader = csv.DictReader(io.StringIO(raw))
    rows = list(reader)
    if not rows:
        return jsonify({"error": "Empty CSV"}), 400

    result = calendar_db.csv_import(_db_path, rows, _get_user())
    return jsonify(result), 200


@calendar_bp.route("/audit", methods=["GET"])
@_require_auth
def audit_log():
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    calendar_type = request.args.get("type")
    action = request.args.get("action")
    rows = calendar_db.get_audit_log(
        _db_path, limit=limit, offset=offset,
        calendar_type=calendar_type, action=action,
    )
    return jsonify(rows)
