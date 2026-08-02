"""
api_server.py
Flask API for reading and writing instruments.json.
Runs on port 8081. The instruments.html UI calls this.

Auth: JWT tokens via /api/login. Manage users with manage_users.py.

Start:  python3 api_server.py
Screen: screen -S api  ->  python3 api_server.py  ->  Ctrl+A D
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import datetime
import secrets
import threading
import time as _time
from pathlib import Path
from functools import wraps

import bcrypt
import jwt
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

load_dotenv(Path(__file__).parent / '.env')

import argparse as _argparse

from bot.guardrails import (
    validate_no_edge_guardrails,
    validate_hard_disabled_instruments,
    HardDisabledViolation,
)

import logging

# Audit logger — every hard-disabled rejection (and the admin override
# endpoint, when added) must leave a durable trail. Propagates so pytest's
# caplog can assert on it; also writes to a dedicated audit file.
_audit_log = logging.getLogger("cogniflowai.audit")
if not _audit_log.handlers:
    try:
        _audit_dir = Path(__file__).parent / 'logs'
        _audit_dir.mkdir(exist_ok=True)
        _ah = logging.FileHandler(str(_audit_dir / 'api_audit.log'))
        _ah.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        _audit_log.addHandler(_ah)
    except OSError:
        # If the log dir is not writable, still emit via root/propagation.
        pass
    _audit_log.setLevel(logging.INFO)


def _audit_hard_disabled_rejection(route: str, errors: list) -> None:
    """Emit one audit entry for a rejected hard-disabled-enable attempt."""
    _audit_log.warning(
        "HARD_DISABLED_REJECT route=%s errors=%s", route, "; ".join(errors)
    )


def _reject_if_hard_disabled(data: dict, route: str):
    """Explicit pre-save guard for the named writer routes. Returns a Flask
    (response, 409) tuple to return-early, or None if the config is clean.
    Audits the rejection. The save() backstop + errorhandler are the safety
    net for any route that does not call this; this gives the explicitly
    enumerated routes a clean 409 before save() is ever reached."""
    hard_errors = validate_hard_disabled_instruments(data)
    if hard_errors:
        _audit_hard_disabled_rejection(route, hard_errors)
        return jsonify({
            'ok': False,
            'message': 'Hard-disabled invariant failed',
            'errors': hard_errors,
        }), 409
    return None

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = str(BASE_DIR / 'instruments.json')
BACKUP_DIR  = str(BASE_DIR / 'backups')
USERS_FILE  = str(BASE_DIR / 'users.json')

# Parse --config early so all route handlers see the correct file
if __name__ == '__main__':
    _parser = _argparse.ArgumentParser(description="CogniflowAI API Server")
    _parser.add_argument("--config", default=None,
                         help="Path to instruments JSON config (default: instruments.json)")
    _cli_args = _parser.parse_args()
    if _cli_args.config:
        CONFIG_FILE = str(Path(_cli_args.config).resolve())

# JWT secret — auto-generated on first run, persisted in .env
JWT_SECRET = os.getenv('JWT_SECRET', '')
if not JWT_SECRET:
    JWT_SECRET = secrets.token_hex(32)
    env_path = BASE_DIR / '.env'
    with open(env_path, 'a') as f:
        f.write(f'\nJWT_SECRET={JWT_SECRET}\n')

JWT_EXPIRY_HOURS = 24

app = Flask(__name__)
_API_PORT = int(os.environ.get('API_PORT', 8081))
_WEB_PORT = int(os.environ.get('WEB_PORT', 8082))
CORS(app, origins=[
    f'http://188.166.150.137:{_WEB_PORT}',
    f'http://127.0.0.1:{_WEB_PORT}',
])

# Rate limiter — keyed by IP
limiter = Limiter(get_remote_address, app=app, default_limits=[],
                  storage_uri='memory://')


# ── SQLite helper ────────────────────────────────────

def _connect_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


# ── User helpers ──────────────────────────────────────

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE) as f:
        return json.load(f)


def verify_password(plain, hashed):
    return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))


# ── JWT helpers ───────────────────────────────────────

def create_token(username):
    payload = {
        'sub': username,
        'iat': datetime.datetime.utcnow(),
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        header = request.headers.get('Authorization', '')
        if not header.startswith('Bearer '):
            return jsonify({'error': 'Missing or invalid token'}), 401
        token = header[7:]
        try:
            jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated


# ── Auth routes ───────────────────────────────────────

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})


@app.route('/api/login', methods=['POST'])
@limiter.limit('5 per minute')
def login():
    body = request.get_json(silent=True) or {}
    username = body.get('username', '').strip()
    password = body.get('password', '')

    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400

    users = load_users()
    user = users.get(username)

    if not user or not verify_password(password, user['password']):
        return jsonify({'error': 'Invalid credentials'}), 401

    token = create_token(username)
    return jsonify({'token': token, 'username': username})


@app.route('/api/verify', methods=['GET'])
@require_auth
def verify():
    """Check if the current token is still valid."""
    return jsonify({'ok': True})


@app.route('/api/auth/verify', methods=['GET'])
def auth_verify():
    """
    nginx auth_request subrequest endpoint.
    Checks JWT from Authorization header OR jwt_token cookie.
    Returns 200 (allow) or 401 (deny).
    """
    token = None
    # Try Authorization header first
    header = request.headers.get('Authorization', '')
    if header.startswith('Bearer '):
        token = header[7:]
    # Fall back to cookie (set by login page JS)
    if not token:
        token = request.cookies.get('jwt_token', '')
    if not token:
        return '', 401
    try:
        jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return '', 200
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return '', 401


# ── Config helpers ────────────────────────────────────

def load():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def save(data):
    # Universal no-edge guardrail backstop: every write path (layer1,
    # layer2, settings, toggle-enable, update, apply-wf, optimise) funnels
    # through save(). Refuse before any write — the atomic write below has
    # not started, so a raise leaves instruments.json untouched.
    guard_errors = validate_no_edge_guardrails(data)
    if guard_errors:
        raise ValueError("No-edge guardrail failed: " + "; ".join(guard_errors))
    # Hard-disabled invariant backstop: every write path funnels through
    # save(). Raising here — before the backup copy and atomic write below —
    # leaves instruments.json untouched. The errorhandler maps the typed
    # exception to HTTP 409 and emits the audit entry for routes that did
    # not pre-check (layer2, settings, update, global-settings).
    hard_errors = validate_hard_disabled_instruments(data)
    if hard_errors:
        exc = HardDisabledViolation(
            "Hard-disabled invariant failed: " + "; ".join(hard_errors))
        exc.errors = hard_errors
        raise exc
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    shutil.copy(CONFIG_FILE, f'{BACKUP_DIR}/instruments_{ts}.json')
    # Atomic write: write to temp file then rename to avoid corrupt reads
    tmp_path = CONFIG_FILE + '.tmp'
    with open(tmp_path, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, CONFIG_FILE)


@app.errorhandler(HardDisabledViolation)
def _handle_hard_disabled(exc):
    """Final safety net: any writer route whose save() backstop raises the
    hard-disabled violation returns a clean 409 (never an unhandled 500),
    leaving the previous config untouched, and emits an audit entry."""
    errs = getattr(exc, 'errors', [str(exc)])
    _audit_hard_disabled_rejection(request.path, errs)
    return jsonify({
        'ok': False,
        'message': 'Hard-disabled invariant failed',
        'errors': errs,
    }), 409


# ── Protected routes ──────────────────────────────────

@app.route('/api/instruments', methods=['GET'])
@require_auth
def get_instruments():
    return jsonify(load())


@app.route('/api/instruments', methods=['POST'])
@require_auth
def save_instruments():
    data = request.get_json()

    # ── Validate config structure before saving ──────────────
    errors = validate_config(data)
    if errors:
        return jsonify({'ok': False, 'message': 'Validation failed',
                        'errors': errors}), 400

    # ── Hard-disabled invariant (full-editor / bulk save) ────
    rejection = _reject_if_hard_disabled(data, '/api/instruments')
    if rejection:
        return rejection

    try:
        save(data)
        return jsonify({'ok': True, 'message': 'Saved successfully'})
    except HardDisabledViolation:
        # Defense in depth — never collapse the invariant into a 500.
        raise
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)}), 500


def validate_config(data: dict) -> list:
    """
    Validate the full instruments.json structure.
    Returns a list of error strings (empty = valid).
    """
    errors = []

    if not isinstance(data, dict):
        return ['Config must be a JSON object']

    # ── settings section ─────────────────────────────────────
    if 'settings' not in data:
        errors.append("Missing 'settings' section")
    elif not isinstance(data['settings'], dict):
        errors.append("'settings' must be a JSON object")
    else:
        broker = data['settings'].get('broker', 'ibkr')
        required_settings = ['check_interval_mins', 'portfolio_loss_limit', 'web_dir']
        if broker == 'ibkr':
            required_settings += ['host', 'port', 'client_id', 'account']
        for key in required_settings:
            if key not in data['settings']:
                errors.append(f"Missing required setting: '{key}'")

    # ── layer1_active section ────────────────────────────────
    if 'layer1_active' not in data:
        errors.append("Missing 'layer1_active' section")
    elif not isinstance(data['layer1_active'], list):
        errors.append("'layer1_active' must be a list")
    else:
        broker = data.get('settings', {}).get('broker', 'ibkr')
        if broker == 'ig':
            required_inst_fields = ['symbol', 'name', 'ig_epic', 'currency']
        else:
            required_inst_fields = ['symbol', 'name', 'sec_type', 'exchange', 'currency', 'qty']
        for i, inst in enumerate(data['layer1_active']):
            if not isinstance(inst, dict):
                errors.append(f"layer1_active[{i}] must be a JSON object")
                continue
            for field in required_inst_fields:
                if field not in inst:
                    sym = inst.get('symbol', f'index {i}')
                    errors.append(f"layer1_active '{sym}' missing required field: '{field}'")

    # ── no-edge guardrail ────────────────────────────────────
    errors.extend(validate_no_edge_guardrails(data))

    return errors


@app.route('/api/instruments/layer1', methods=['POST'])
@require_auth
def save_layer1():
    instruments = request.get_json()
    data = load()
    data['layer1_active'] = instruments
    guard_errors = validate_no_edge_guardrails(data)
    if guard_errors:
        return jsonify({'ok': False, 'message': 'No-edge guardrail failed',
                        'errors': guard_errors}), 400
    rejection = _reject_if_hard_disabled(data, '/api/instruments/layer1')
    if rejection:
        return rejection
    save(data)
    return jsonify({'ok': True})


@app.route('/api/instruments/layer2', methods=['POST'])
@require_auth
def save_layer2():
    instruments = request.get_json()
    data = load()
    data['layer2_accumulation'] = instruments
    save(data)
    return jsonify({'ok': True})


@app.route('/api/settings', methods=['POST'])
@require_auth
def save_settings():
    settings = request.get_json()
    data = load()
    data['settings'].update(settings)
    guard_errors = validate_no_edge_guardrails(data)
    if guard_errors:
        return jsonify({'ok': False, 'message': 'No-edge guardrail failed',
                        'errors': guard_errors}), 400
    save(data)
    return jsonify({'ok': True})


@app.route('/api/backups', methods=['GET'])
@require_auth
def list_backups():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    files = sorted(os.listdir(BACKUP_DIR), reverse=True)[:10]
    return jsonify(files)


# ── Test dashboard routes ─────────────────────────────

BACKTEST_DB = str(BASE_DIR / 'backtest.db')
BACKTEST_RESULTS_DIR = str(BASE_DIR / 'backtest' / 'results')


@app.route('/api/tests/unit', methods=['POST'])
@require_auth
def run_unit_tests():
    """Run pytest and return structured results."""
    try:
        result = subprocess.run(
            ['python3', '-m', 'pytest', 'tests/', '-v', '--tb=short', '-q'],
            capture_output=True, text=True, timeout=120,
            cwd=str(BASE_DIR),
        )
        lines = result.stdout.strip().split('\n')
        tests = []
        for line in lines:
            if '::' in line and (' PASSED' in line or ' FAILED' in line or ' ERROR' in line):
                parts = line.rsplit(' ', 1)
                name = parts[0].strip()
                status = parts[1].strip() if len(parts) > 1 else 'UNKNOWN'
                # Extract module and test name
                if '::' in name:
                    module, test_name = name.split('::', 1)
                    module = module.replace('tests/', '')
                else:
                    module, test_name = '', name
                tests.append({
                    'module': module,
                    'name': test_name,
                    'status': status,
                })
        # Parse summary line like "46 passed, 2 warnings in 0.95s"
        summary_line = lines[-1] if lines else ''
        passed = 0
        failed = 0
        errors = 0
        duration = ''
        m = re.search(r'(\d+) passed', summary_line)
        if m:
            passed = int(m.group(1))
        m = re.search(r'(\d+) failed', summary_line)
        if m:
            failed = int(m.group(1))
        m = re.search(r'(\d+) error', summary_line)
        if m:
            errors = int(m.group(1))
        m = re.search(r'in ([\d.]+)s', summary_line)
        if m:
            duration = m.group(1) + 's'

        return jsonify({
            'tests': tests,
            'passed': passed,
            'failed': failed,
            'errors': errors,
            'duration': duration,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'returncode': result.returncode,
        })
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Tests timed out after 120s'}), 504
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/tests/walkforward', methods=['GET'])
@require_auth
def get_walkforward_results():
    """Return walk-forward results from backtest.db."""
    if not os.path.exists(BACKTEST_DB):
        return jsonify({'runs': [], 'latest': []})

    conn = _connect_db(BACKTEST_DB)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get all distinct run dates
    cursor.execute("SELECT DISTINCT run_date FROM wf_results ORDER BY run_date DESC")
    runs = [row['run_date'] for row in cursor.fetchall()]

    # Get results for requested run (default: latest)
    run_date = request.args.get('run_date', runs[0] if runs else '')
    results = []
    if run_date:
        cursor.execute(
            "SELECT * FROM wf_results WHERE run_date = ? ORDER BY oos_pnl DESC",
            (run_date,)
        )
        results = [dict(row) for row in cursor.fetchall()]

    conn.close()
    return jsonify({'runs': runs, 'run_date': run_date, 'results': results})


@app.route('/api/tests/backtest/list', methods=['GET'])
@require_auth
def list_backtest_reports():
    """List available backtest report files."""
    if not os.path.exists(BACKTEST_RESULTS_DIR):
        return jsonify([])

    files = []
    for f in sorted(os.listdir(BACKTEST_RESULTS_DIR), reverse=True):
        if f.endswith('.txt'):
            fpath = os.path.join(BACKTEST_RESULTS_DIR, f)
            stat = os.stat(fpath)
            files.append({
                'name': f,
                'size': stat.st_size,
                'modified': datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
                'type': 'walkforward' if f.startswith('wf_') else 'backtest',
            })
    return jsonify(files)


@app.route('/api/tests/backtest/report', methods=['GET'])
@require_auth
def get_backtest_report():
    """Read contents of a specific backtest report file."""
    filename = request.args.get('file', '')
    if not filename or '..' in filename or '/' in filename:
        return jsonify({'error': 'Invalid filename'}), 400

    filepath = os.path.join(BACKTEST_RESULTS_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    with open(filepath) as f:
        content = f.read()
    return jsonify({'name': filename, 'content': content})


@app.route('/api/tests/comparison', methods=['GET'])
@require_auth
def get_backtest_vs_walkforward():
    """Compare backtest (IS) vs walk-forward (OOS) for all instruments."""
    if not os.path.exists(BACKTEST_DB):
        return jsonify([])

    conn = _connect_db(BACKTEST_DB)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get latest run
    cursor.execute("SELECT DISTINCT run_date FROM wf_results ORDER BY run_date DESC LIMIT 1")
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify([])

    run_date = row['run_date']
    cursor.execute(
        "SELECT symbol, timeframe, is_pnl, is_profit_factor, is_trade_count, "
        "oos_pnl, oos_profit_factor, oos_win_rate, oos_trade_count, "
        "wf_efficiency, best_stop_pct, best_tp_pct, verdict "
        "FROM wf_results WHERE run_date = ? ORDER BY oos_pnl DESC",
        (run_date,)
    )
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({'run_date': run_date, 'results': results})


# ── Instrument management routes ─────────────────────

# Store running optimisation jobs: {job_id: {status, progress, phase, result, ...}}
_optimise_jobs = {}
_optimise_lock = threading.Lock()

_JOB_MAX_AGE = 86400  # 24 hours


def _cleanup_old_jobs():
    """Remove optimise jobs older than 24 hours."""
    cutoff = _time.time() - _JOB_MAX_AGE
    with _optimise_lock:
        expired = [
            jid for jid, job in _optimise_jobs.items()
            if job.get("_created_at", 0) < cutoff
        ]
        for jid in expired:
            del _optimise_jobs[jid]

INDICATOR_FIELDS = {
    "rsi_period", "rsi_oversold", "rsi_overbought",
    "williams_r_period", "adx_period", "adx_threshold", "ma200_period",
}

EDITABLE_TRADING_FIELDS = {
    "trail_stop_pct", "take_profit_pct", "qty", "emergency_stop_pct",
}

FORBIDDEN_FIELDS = {
    "sec_type", "exchange", "currency", "name",
}


def _resolve_indicator_settings_api(data: dict, instrument: dict) -> dict:
    """Resolve indicator settings for an instrument (API helper)."""
    s = data.get("settings", {})
    settings = {
        "rsi_period": s.get("rsi_period", 14),
        "rsi_oversold": s.get("rsi_oversold", 35),
        "rsi_overbought": s.get("rsi_overbought", 70),
        "williams_r_period": s.get("williams_r_period", 14),
        "williams_r_mid": s.get("williams_r_mid", -50),
        "williams_r_oversold": s.get("williams_r_oversold", -80),
        "williams_r_overbought": s.get("williams_r_overbought", -20),
        "adx_period": s.get("adx_period", 14),
        "adx_threshold": s.get("adx_threshold", 20),
        "ma200_period": s.get("ma200_period", 200),
        "alligator_min_gap_pct": s.get("alligator_min_gap_pct", 0.003),
    }
    overrides = instrument.get("indicators", {})
    for key, val in overrides.items():
        if val is not None:
            settings[key] = val
    return settings


def _validate_trading_params(changes: dict) -> list:
    """Validate trading params in an update request. Returns list of errors."""
    errors = []
    stop = changes.get("trail_stop_pct")
    tp = changes.get("take_profit_pct")
    qty = changes.get("qty")
    emergency = changes.get("emergency_stop_pct")

    if stop is not None:
        if not isinstance(stop, (int, float)) or stop <= 0:
            errors.append("trail_stop_pct must be a positive number")
        elif stop > 20:
            errors.append("trail_stop_pct must be <= 20")

    if tp is not None:
        if not isinstance(tp, (int, float)) or tp <= 0:
            errors.append("take_profit_pct must be a positive number")
        elif tp > 50:
            errors.append("take_profit_pct must be <= 50")

    if qty is not None:
        if not isinstance(qty, (int, float)) or qty <= 0:
            errors.append("qty must be a positive number")

    if emergency is not None:
        if not isinstance(emergency, (int, float)) or emergency <= 0:
            errors.append("emergency_stop_pct must be a positive number")
        # Emergency must be >= trail stop
        effective_stop = stop if stop is not None else None
        if effective_stop is not None and emergency < effective_stop:
            errors.append("emergency_stop_pct must be >= trail_stop_pct")

    # Check forbidden fields
    for field in changes:
        if field in FORBIDDEN_FIELDS:
            errors.append(f"Cannot update protected field: '{field}'")

    return errors


@app.route('/api/instruments/wf-recommendations', methods=['GET'])
@require_auth
def get_wf_recommendations():
    """Return instruments merged with latest WF results."""
    data = load()
    instruments = data.get('layer1_active', [])

    wf_data = {}
    if os.path.exists(BACKTEST_DB):
        conn = _connect_db(BACKTEST_DB)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT run_date FROM wf_results ORDER BY run_date DESC LIMIT 1"
        )
        row = cursor.fetchone()
        if row:
            run_date = row['run_date']
            cursor.execute(
                "SELECT * FROM wf_results WHERE run_date = ?", (run_date,)
            )
            for r in cursor.fetchall():
                wf_data[r['symbol']] = dict(r)
        conn.close()

    result = []
    for inst in instruments:
        sym = inst['symbol']
        resolved = _resolve_indicator_settings_api(data, inst)
        entry = {
            **inst,
            'resolved_indicators': resolved,
            'wf_stop': None,
            'wf_tp': None,
            'wf_efficiency': None,
            'wf_verdict': None,
            'wf_oos_pnl': None,
            'wf_oos_win_rate': None,
            'params_match': None,
        }

        wf = wf_data.get(sym)
        if wf:
            verdict = wf.get('verdict', 'no_edge')
            entry['wf_verdict'] = verdict
            entry['wf_efficiency'] = wf.get('wf_efficiency')
            entry['wf_oos_pnl'] = wf.get('oos_pnl')
            entry['wf_oos_win_rate'] = wf.get('oos_win_rate')

            if verdict != 'no_edge':
                entry['wf_stop'] = wf.get('best_stop_pct')
                entry['wf_tp'] = wf.get('best_tp_pct')
                # Check params match (within 0.5% tolerance)
                cur_stop = inst.get('trail_stop_pct', 0)
                cur_tp = inst.get('take_profit_pct', 0)
                wf_stop = wf.get('best_stop_pct', 0) or 0
                wf_tp = wf.get('best_tp_pct', 0) or 0
                stop_match = abs(cur_stop - wf_stop) <= 0.5
                tp_match = abs(cur_tp - wf_tp) <= 0.5
                entry['params_match'] = stop_match and tp_match

        result.append(entry)

    return jsonify({
        'instruments': result,
        'wf_run_date': next(iter(wf_data.values()), {}).get('run_date') if wf_data else None,
        'global_settings': data.get('settings', {}),
    })


@app.route('/api/instruments/update', methods=['POST'])
@require_auth
def update_instruments():
    """Update trading params and/or indicator settings for specified instruments."""
    body = request.get_json(silent=True) or {}
    changes_list = body.get('changes', [])

    if not changes_list:
        return jsonify({'error': 'No changes provided'}), 400

    data = load()
    instruments = data.get('layer1_active', [])
    sym_map = {inst['symbol']: inst for inst in instruments}

    all_errors = []
    updated_symbols = []

    for changes in changes_list:
        sym = changes.get('symbol')
        if not sym or sym not in sym_map:
            all_errors.append(f"Unknown symbol: '{sym}'")
            continue

        # Validate trading params
        errors = _validate_trading_params(changes)
        if errors:
            all_errors.extend(errors)
            continue

        # Check emergency >= trail for existing values
        inst = sym_map[sym]
        new_stop = changes.get('trail_stop_pct', inst.get('trail_stop_pct'))
        new_emergency = changes.get('emergency_stop_pct', inst.get('emergency_stop_pct'))
        if new_stop is not None and new_emergency is not None:
            if new_emergency < new_stop:
                all_errors.append(
                    f"{sym}: emergency_stop_pct ({new_emergency}) must be >= "
                    f"trail_stop_pct ({new_stop})"
                )
                continue

        # Apply trading param updates
        for field in EDITABLE_TRADING_FIELDS:
            if field in changes:
                inst[field] = changes[field]

        # Apply indicator updates
        if 'indicators' in changes:
            if 'indicators' not in inst:
                inst['indicators'] = {}
            for key, val in changes['indicators'].items():
                if val is None:
                    # Remove override — revert to global
                    inst['indicators'].pop(key, None)
                else:
                    inst['indicators'][key] = val
            # Remove empty indicators block
            if not inst['indicators']:
                del inst['indicators']

        updated_symbols.append(sym)

    if all_errors:
        return jsonify({'error': 'Validation failed', 'errors': all_errors}), 400

    save(data)
    return jsonify({'ok': True, 'updated': updated_symbols})


@app.route('/api/instruments/apply-wf', methods=['POST'])
@require_auth
def apply_wf():
    """Apply WF-recommended stop%/TP% for specified symbols."""
    body = request.get_json(silent=True) or {}
    symbols = body.get('symbols', [])

    if not os.path.exists(BACKTEST_DB):
        return jsonify({'error': 'No walk-forward results available'}), 400

    conn = _connect_db(BACKTEST_DB)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT run_date FROM wf_results ORDER BY run_date DESC LIMIT 1"
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'No walk-forward results available'}), 400

    run_date = row['run_date']
    cursor.execute("SELECT * FROM wf_results WHERE run_date = ?", (run_date,))
    wf_map = {}
    for r in cursor.fetchall():
        wf_map[r['symbol']] = dict(r)
    conn.close()

    data = load()
    instruments = data.get('layer1_active', [])
    sym_map = {inst['symbol']: inst for inst in instruments}

    # If 'all', apply to all instruments with edge
    if symbols == 'all' or symbols == ['all']:
        symbols = list(sym_map.keys())

    applied = []
    skipped = []

    for sym in symbols:
        if sym not in sym_map:
            skipped.append({'symbol': sym, 'reason': 'unknown symbol'})
            continue

        wf = wf_map.get(sym)
        if not wf or wf.get('verdict') == 'no_edge':
            skipped.append({'symbol': sym, 'reason': 'no_edge'})
            continue

        inst = sym_map[sym]
        inst['trail_stop_pct'] = wf['best_stop_pct']
        inst['take_profit_pct'] = wf['best_tp_pct']
        applied.append(sym)

    if applied:
        rejection = _reject_if_hard_disabled(data, '/api/instruments/apply-wf')
        if rejection:
            return rejection
        save(data)

    return jsonify({'ok': True, 'applied': applied, 'skipped': skipped})


@app.route('/api/instruments/toggle-enable', methods=['POST'])
@require_auth
def toggle_enable():
    """Enable or disable an instrument."""
    body = request.get_json(silent=True) or {}
    sym = body.get('symbol', '')
    enabled = body.get('enabled')

    if not sym:
        return jsonify({'error': 'Symbol required'}), 400

    data = load()
    instruments = data.get('layer1_active', [])
    found = False

    for inst in instruments:
        if inst['symbol'] == sym:
            inst['enabled'] = bool(enabled)
            found = True
            break

    if not found:
        return jsonify({'error': f"Unknown symbol: '{sym}'"}), 400

    rejection = _reject_if_hard_disabled(data, '/api/instruments/toggle-enable')
    if rejection:
        return rejection

    save(data)
    return jsonify({'ok': True, 'symbol': sym, 'enabled': bool(enabled)})


@app.route('/api/instruments/test', methods=['POST'])
@require_auth
def quick_test():
    """Layer 1: quick WF test with user-specified params for one instrument."""
    body = request.get_json(silent=True) or {}
    sym = body.get('symbol', '')
    params = body.get('params', {})
    train_months = body.get('train_months', 6)
    test_months = body.get('test_months', 3)

    if not sym:
        return jsonify({'error': 'Symbol required'}), 400

    # Validate stop
    stop_pct = params.get('trail_stop_pct')
    if stop_pct is not None and (not isinstance(stop_pct, (int, float)) or stop_pct <= 0):
        return jsonify({'error': 'trail_stop_pct must be a positive number'}), 400

    data = load()
    instruments = data.get('layer1_active', [])
    inst = next((i for i in instruments if i['symbol'] == sym), None)
    if not inst:
        return jsonify({'error': f"Unknown symbol: '{sym}'"}), 400

    # Check data exists
    if not os.path.exists(BACKTEST_DB):
        return jsonify({'error': 'No OHLCV data available — run backtest download first'}), 400

    try:
        from backtest.database import get_connection, load_bars
        from backtest.walk_forward import run_walk_forward

        conn = get_connection()
        timeframe = inst.get('timeframe', 'daily')
        df = load_bars(conn, sym, timeframe)
        if df.empty and timeframe != 'daily':
            df = load_bars(conn, sym, 'daily')
            timeframe = 'daily'
        conn.close()

        if df.empty:
            return jsonify({'error': f'No OHLCV data for {sym}'}), 400

        # Build indicator settings from params
        global_settings = data.get('settings', {})
        indicator_settings = {
            "rsi_period": params.get("rsi_period", global_settings.get("rsi_period", 14)),
            "rsi_oversold": params.get("rsi_oversold", global_settings.get("rsi_oversold", 35)),
            "rsi_overbought": params.get("rsi_overbought", global_settings.get("rsi_overbought", 70)),
            "williams_r_period": params.get("williams_r_period", global_settings.get("williams_r_period", 14)),
            "williams_r_mid": global_settings.get("williams_r_mid", -50),
            "williams_r_oversold": global_settings.get("williams_r_oversold", -80),
            "williams_r_overbought": global_settings.get("williams_r_overbought", -20),
            "adx_period": params.get("adx_period", global_settings.get("adx_period", 14)),
            "adx_threshold": params.get("adx_threshold", global_settings.get("adx_threshold", 20)),
            "ma200_period": params.get("ma200_period", global_settings.get("ma200_period", 200)),
            "alligator_min_gap_pct": global_settings.get("alligator_min_gap_pct", 0.003),
        }

        inst_config = {
            **inst,
            "trail_stop_pct": params.get("trail_stop_pct", inst.get("trail_stop_pct", 2.0)),
            "take_profit_pct": params.get("take_profit_pct", inst.get("take_profit_pct", 8.0)),
        }

        # Run simple backtest (full dataset)
        from backtest.offline_signals import generate_signals
        from backtest.simulator import simulate_trades, summarise

        bt_signals = generate_signals(df, indicator_settings, symbol=sym)
        bt_stop = inst_config.get("trail_stop_pct", 2.0)
        bt_tp = inst_config.get("take_profit_pct", 8.0)
        bt_qty = inst_config.get("qty", 1)
        bt_long_only = inst_config.get("long_only", True)
        bt_currency = inst_config.get("currency", "USD")
        bt_trades = simulate_trades(
            bt_signals, df, bt_stop, bt_tp, bt_qty, bt_long_only, bt_currency,
        )
        bt_summary = summarise(bt_trades)

        # Run walk-forward
        result = run_walk_forward(
            symbol=sym,
            df=df,
            indicator_settings=indicator_settings,
            instrument_config=inst_config,
            train_months=train_months,
            test_months=test_months,
        )

        if result is None:
            return jsonify({'error': f'Insufficient data for {sym} walk-forward'}), 400

        # Calculate reality discount
        bt_pnl = bt_summary.total_pnl
        wf_pnl = result.oos_total_pnl
        if bt_pnl > 0:
            reality_discount_pct = round((1 - wf_pnl / bt_pnl) * 100, 1)
        elif bt_pnl < 0 and wf_pnl < 0:
            reality_discount_pct = 0
        else:
            reality_discount_pct = 0

        # Get baseline from DB
        baseline = _get_baseline(sym)

        # Calculate improvement
        baseline_pnl = baseline.get('oos_pnl', 0) if baseline else 0
        test_pnl = result.oos_total_pnl
        if baseline_pnl != 0:
            improvement_pct = round((test_pnl - baseline_pnl) / abs(baseline_pnl) * 100, 1)
        elif test_pnl > 0:
            improvement_pct = 100.0
        else:
            improvement_pct = 0.0

        # Determine verdict
        if abs(improvement_pct) <= 5:
            verdict = 'similar'
        elif improvement_pct > 0:
            verdict = 'better'
        else:
            verdict = 'worse'

        return jsonify({
            'symbol': sym,
            'timeframe': timeframe,
            'backtest': {
                'total_pnl': bt_summary.total_pnl,
                'profit_factor': bt_summary.profit_factor,
                'trade_count': bt_summary.trade_count,
                'win_rate': bt_summary.win_rate,
                'max_drawdown': bt_summary.max_drawdown,
            },
            'walkforward': {
                'oos_pnl': result.oos_total_pnl,
                'wf_efficiency': result.wf_efficiency,
                'oos_profit_factor': result.oos_profit_factor,
                'oos_trade_count': result.oos_trade_count,
                'oos_win_rate': result.oos_win_rate,
            },
            'reality_discount_pct': reality_discount_pct,
            'baseline': baseline,
            'improvement_pct': improvement_pct,
            'verdict': verdict,
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _get_baseline(symbol: str) -> dict | None:
    """Get the latest WF result for a symbol as baseline."""
    if not os.path.exists(BACKTEST_DB):
        return None
    try:
        conn = _connect_db(BACKTEST_DB)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM wf_results WHERE symbol = ? "
            "ORDER BY run_date DESC LIMIT 1",
            (symbol,)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                'wf_efficiency': row['wf_efficiency'],
                'oos_pnl': row['oos_pnl'],
                'oos_win_rate': row['oos_win_rate'],
                'oos_trade_count': row['oos_trade_count'],
            }
    except Exception:
        pass
    return None


@app.route('/api/instruments/optimise', methods=['POST'])
@require_auth
def start_optimise():
    """Layer 2: start async instrument optimisation."""
    body = request.get_json(silent=True) or {}
    sym = body.get('symbol', '')
    train_months = body.get('train_months', 6)
    test_months = body.get('test_months', 3)

    if not sym:
        return jsonify({'error': 'Symbol required'}), 400

    _cleanup_old_jobs()

    data = load()
    instruments = data.get('layer1_active', [])
    inst = next((i for i in instruments if i['symbol'] == sym), None)
    if not inst:
        return jsonify({'error': f"Unknown symbol: '{sym}'"}), 400

    if not os.path.exists(BACKTEST_DB):
        return jsonify({'error': 'No OHLCV data available'}), 400

    job_id = f"opt_{sym}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"

    # Check for running job on same symbol
    with _optimise_lock:
        for jid, job in _optimise_jobs.items():
            if job.get('symbol') == sym and job.get('status') == 'running':
                return jsonify({'error': f'{sym} is already being optimised',
                                'existing_job_id': jid}), 409

        _optimise_jobs[job_id] = {
            'symbol': sym,
            'status': 'running',
            'progress': 0,
            'phase': 'Starting...',
            'estimated_remaining_seconds': None,
            'result': None,
            'error': None,
            '_created_at': _time.time(),
        }

    # Run in background thread
    thread = threading.Thread(
        target=_run_optimise_job,
        args=(job_id, sym, inst, data, train_months, test_months),
        daemon=True,
    )
    thread.start()

    return jsonify({'job_id': job_id})


def _run_optimise_job(job_id, symbol, inst, data, train_months, test_months):
    """Background worker for instrument optimisation."""
    try:
        from backtest.database import get_connection, load_bars
        from backtest.grid_search import full_optimise

        conn = get_connection()
        timeframe = inst.get('timeframe', 'daily')
        df = load_bars(conn, symbol, timeframe)
        if df.empty and timeframe != 'daily':
            df = load_bars(conn, symbol, 'daily')
        conn.close()

        if df.empty:
            with _optimise_lock:
                _optimise_jobs[job_id]['status'] = 'error'
                _optimise_jobs[job_id]['error'] = f'No OHLCV data for {symbol}'
            return

        settings = _resolve_indicator_settings_api(data, inst)
        start_time = _time.time()

        def progress_cb(phase, current, total, detail):
            elapsed = _time.time() - start_time
            if current > 0:
                est_total = elapsed * total / current
                remaining = max(0, est_total - elapsed)
            else:
                remaining = None
            pct = int(current * 100 / total) if total > 0 else 0
            with _optimise_lock:
                _optimise_jobs[job_id]['progress'] = pct
                _optimise_jobs[job_id]['phase'] = detail
                _optimise_jobs[job_id]['estimated_remaining_seconds'] = (
                    round(remaining) if remaining is not None else None
                )

        result = full_optimise(
            symbol=symbol,
            df=df,
            base_indicator_settings=settings,
            instrument_config=inst,
            train_months=train_months,
            test_months=test_months,
            progress_callback=progress_cb,
        )

        if result is None:
            with _optimise_lock:
                _optimise_jobs[job_id]['status'] = 'error'
                _optimise_jobs[job_id]['error'] = 'No valid results found'
            return

        # Get baseline
        baseline = _get_baseline(symbol)
        baseline_pnl = baseline.get('oos_pnl', 0) if baseline else 0
        if baseline_pnl != 0:
            result.improvement_pct = round(
                (result.oos_pnl - baseline_pnl) / abs(baseline_pnl) * 100, 1
            )
        result.current_oos_pnl = baseline_pnl

        with _optimise_lock:
            _optimise_jobs[job_id]['status'] = 'complete'
            _optimise_jobs[job_id]['progress'] = 100
            _optimise_jobs[job_id]['result'] = {
                'symbol': symbol,
                'duration_seconds': result.duration_seconds,
                'best': {
                    'trail_stop_pct': result.best_stop_pct,
                    'take_profit_pct': result.best_tp_pct,
                    **result.best_indicators,
                    'wf_efficiency': result.wf_efficiency,
                    'oos_pnl': result.oos_pnl,
                    'oos_profit_factor': result.oos_profit_factor,
                    'oos_win_rate': result.oos_win_rate,
                },
                'current': {
                    'wf_efficiency': baseline.get('wf_efficiency') if baseline else None,
                    'oos_pnl': baseline_pnl,
                },
                'improvement_pct': result.improvement_pct,
                'top_5': result.top_5,
            }

    except Exception as e:
        with _optimise_lock:
            _optimise_jobs[job_id]['status'] = 'error'
            _optimise_jobs[job_id]['error'] = str(e)


@app.route('/api/instruments/optimise/status', methods=['GET'])
@require_auth
def optimise_status():
    """Poll for optimisation progress and results."""
    job_id = request.args.get('job_id', '')
    if not job_id:
        return jsonify({'error': 'job_id required'}), 400

    with _optimise_lock:
        job = _optimise_jobs.get(job_id)

    if not job:
        return jsonify({'error': 'Unknown job_id'}), 404

    response = {
        'job_id': job_id,
        'status': job['status'],
        'progress': job['progress'],
        'phase': job['phase'],
        'estimated_remaining_seconds': job.get('estimated_remaining_seconds'),
    }

    if job['status'] == 'complete':
        response['result'] = job['result']
    elif job['status'] == 'error':
        response['error'] = job['error']

    return jsonify(response)


@app.route('/api/instruments/global-settings', methods=['POST'])
@require_auth
def update_global_settings():
    """Update global indicator settings."""
    body = request.get_json(silent=True) or {}
    data = load()

    for key in INDICATOR_FIELDS:
        if key in body:
            data['settings'][key] = body[key]

    # Also allow alligator_min_gap_pct
    if 'alligator_min_gap_pct' in body:
        data['settings']['alligator_min_gap_pct'] = body['alligator_min_gap_pct']

    save(data)
    return jsonify({'ok': True})


# ── LLM Advisor routes ───────────────────────────────

LEARNING_DB = str(BASE_DIR / 'learning_loop.db')
NEWS_DB = str(BASE_DIR / 'news.db')
ADVISOR_DB = str(BASE_DIR / 'advisor.db')


def _init_advisor_db():
    """Create advisor reports table."""
    conn = _connect_db(ADVISOR_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS advisor_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            report_json TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


@app.route('/api/advisor/latest', methods=['GET'])
@require_auth
def advisor_latest():
    """Get the latest weekly advisor report."""
    try:
        _init_advisor_db()
        conn = _connect_db(ADVISOR_DB)
        cursor = conn.execute(
            "SELECT timestamp, report_json FROM advisor_reports "
            "ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return jsonify({
                'timestamp': row[0],
                'report': json.loads(row[1]),
            })
        return jsonify({'report': None, 'message': 'No reports yet'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/advisor/generate', methods=['POST'])
@require_auth
def advisor_generate():
    """Generate a weekly advisor report on demand."""
    try:
        from dotenv import load_dotenv as _load_env
        _load_env(BASE_DIR / '.env')
        from bot.llm import create_llm
        from bot.llm.advisor import generate_weekly_report

        data = load()
        settings = data.get('settings', {})
        provider = settings.get('llm_provider_advisor',
                                settings.get('llm_provider', 'groq'))
        llm = create_llm(provider)

        # Fetch last 7 days of trades
        trades = []
        if os.path.exists(LEARNING_DB):
            conn = _connect_db(LEARNING_DB)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM trades WHERE open=0 "
                "AND timestamp > datetime('now', '-7 days') "
                "ORDER BY timestamp DESC"
            )
            trades = [dict(row) for row in cursor.fetchall()]
            conn.close()

        # Fetch trade reviews
        reviews = []
        if os.path.exists(LEARNING_DB):
            conn = _connect_db(LEARNING_DB)
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.execute(
                    "SELECT * FROM trade_reviews "
                    "WHERE timestamp > datetime('now', '-7 days') "
                    "ORDER BY timestamp DESC"
                )
                reviews = [dict(row) for row in cursor.fetchall()]
            except sqlite3.OperationalError:
                pass  # table may not exist yet
            conn.close()

        # Fetch WF results
        wf_results = []
        if os.path.exists(BACKTEST_DB):
            conn = _connect_db(BACKTEST_DB)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT DISTINCT run_date FROM wf_results "
                "ORDER BY run_date DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row:
                cursor = conn.execute(
                    "SELECT * FROM wf_results WHERE run_date = ?",
                    (row['run_date'],)
                )
                wf_results = [dict(r) for r in cursor.fetchall()]
            conn.close()

        instruments = data.get('layer1_active', [])

        report = generate_weekly_report(llm, trades, reviews,
                                        wf_results, instruments)

        # Save report
        _init_advisor_db()
        conn = _connect_db(ADVISOR_DB)
        conn.execute(
            "INSERT INTO advisor_reports (timestamp, report_json) VALUES (?, ?)",
            (datetime.datetime.utcnow().isoformat(), json.dumps(report))
        )
        conn.commit()
        conn.close()

        return jsonify({'report': report})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/advisor/reviews', methods=['GET'])
@require_auth
def advisor_reviews():
    """Get recent trade reviews."""
    try:
        if not os.path.exists(LEARNING_DB):
            return jsonify([])
        conn = _connect_db(LEARNING_DB)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                "SELECT * FROM trade_reviews ORDER BY timestamp DESC LIMIT 50"
            )
            reviews = [dict(row) for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            reviews = []
        conn.close()
        return jsonify(reviews)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/advisor/sentiment-log', methods=['GET'])
@require_auth
def advisor_sentiment_log():
    """Get recent sentiment checks from news.db."""
    try:
        if not os.path.exists(NEWS_DB):
            return jsonify([])
        conn = _connect_db(NEWS_DB)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                "SELECT * FROM headlines ORDER BY collected_at DESC LIMIT 100"
            )
            headlines = [dict(row) for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            headlines = []
        conn.close()
        return jsonify(headlines)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/advisor/news', methods=['GET'])
@require_auth
def advisor_news():
    """Get recent news for a symbol."""
    symbol = request.args.get('symbol', '')
    if not symbol:
        return jsonify({'error': 'symbol parameter required'}), 400
    try:
        if not os.path.exists(NEWS_DB):
            return jsonify([])
        conn = _connect_db(NEWS_DB)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                "SELECT * FROM headlines WHERE symbol = ? "
                "ORDER BY collected_at DESC LIMIT 20",
                (symbol,)
            )
            headlines = [dict(row) for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            headlines = []
        conn.close()
        return jsonify(headlines)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Regime dashboard routes (§15.5) ─────────────────────
REGIME_DB = str(BASE_DIR / 'regime.db')


def _init_overlay_registry_safe():
    try:
        from bot.overlays.registry import init_overlay_registry
        init_overlay_registry(REGIME_DB)
    except Exception as e:
        print(f"[Regime] Overlay registry init failed: {e}")


_init_overlay_registry_safe()


def _active_instruments_from_config():
    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        return [
            i['symbol']
            for i in data.get('layer1_active', [])
            if i.get('enabled', True) and i.get('symbol')
        ]
    except Exception:
        return []


@app.route('/api/regime/states', methods=['GET'])
@require_auth
def regime_states_route():
    from bot.regime import dashboard_data
    return jsonify(dashboard_data.get_regime_states(REGIME_DB))


@app.route('/api/overlays/active', methods=['GET'])
@require_auth
def regime_overlays_active_route():
    """
    Live-compute active overlays by calling active_overlays() per
    configured instrument with an empty ctx. MACRO_LOCKOUT will fire
    correctly because macro events load from the DB. DATA_QUALITY and
    LOW_LIQUIDITY require bot runtime context (recent bars, volume) and
    will not appear in this view — see docs/TECH_DEBT.md.
    """
    try:
        from bot.overlays.registry import active_overlays as _active_overlays
    except Exception as e:
        return jsonify({'error': f'overlays unavailable: {e}'}), 500

    now = datetime.datetime.now(datetime.timezone.utc)
    by_overlay = {}
    for sym in _active_instruments_from_config():
        try:
            checks = _active_overlays(sym, now, {})
        except Exception:
            continue
        for check in checks:
            key = check.overlay_name
            if key not in by_overlay:
                by_overlay[key] = {
                    'overlay_name': key,
                    'reason': check.reason,
                    'instruments_affected': [],
                }
            by_overlay[key]['instruments_affected'].append(sym)
    return jsonify(list(by_overlay.values()))


@app.route('/api/routing/decisions', methods=['GET'])
@require_auth
def regime_routing_decisions_route():
    from bot.regime import dashboard_data
    return jsonify(dashboard_data.get_current_routing(REGIME_DB))


@app.route('/api/shadow/comparison', methods=['GET'])
@require_auth
def regime_shadow_comparison_route():
    from bot.regime import dashboard_data
    return jsonify(dashboard_data.get_shadow_decisions(REGIME_DB, limit=50))


@app.route('/api/degradation/events', methods=['GET'])
@require_auth
def regime_degradation_events_route():
    from bot.regime import dashboard_data
    return jsonify(dashboard_data.get_degradation_events(REGIME_DB, limit=50))


@app.route('/api/pauses/list', methods=['GET'])
@require_auth
def regime_pauses_route():
    from bot.regime import dashboard_data
    return jsonify(dashboard_data.get_instrument_pauses(REGIME_DB, active_only=True))


# ── Manual regime classification routes (Classify tab) ───
# Operator-triggered classifier re-rolls. Bypasses enable_classifier_shadow
# and the daily input-hash cache. Reads features from the most recent cached
# classification per instrument — daily bars don't change intraday, so the
# scheduler's last fetch is the freshest features available outside the bot
# process.

_CLASSIFY_RATE_LIMIT_SECONDS = 300  # 5 min per instrument
_classify_last_ts: dict = {}  # symbol → unix timestamp of last classification
_classify_lock = threading.Lock()
ESTIMATED_COST_PER_CLASSIFICATION = 0.02  # display-only; logged value is real


def _cached_features(db_path: str, instrument: str) -> "tuple[dict, str] | None":
    """Read features + their source trading_date from the most recent
    regime_classification_cache row. Returns (features, trading_date) or
    None if the instrument has never been classified."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT classification_json, trading_date "
            "FROM regime_classification_cache "
            "WHERE instrument = ? ORDER BY created_at DESC LIMIT 1",
            (instrument,),
        ).fetchone()
    if row is None:
        return None
    try:
        features = json.loads(row[0]).get("features")
        if features is None:
            return None
        return features, row[1]
    except (json.JSONDecodeError, TypeError):
        return None


def _rate_limit_remaining(symbol: str) -> float:
    last = _classify_last_ts.get(symbol)
    if last is None:
        return 0.0
    remaining = _CLASSIFY_RATE_LIMIT_SECONDS - (_time.time() - last)
    return max(remaining, 0.0)


def _classify_one_locked(symbol: str) -> "tuple[dict | None, int, str | None]":
    """Returns (result_payload, http_status, error_msg).

    Caller already holds _classify_lock. Persists to cache + smoothed store
    and bumps the rate-limit timestamp on success."""
    from bot.regime.cache import RegimeCache
    from bot.regime.classifier import RegimeClassifier
    from bot.regime.cost_tracker import CostTracker, DEFAULT_MAX_DAILY_COST_USD
    from bot.regime.smoothing import update, update_first_run, initial_state
    from bot.regime.smoothing_store import SmoothedStateStore

    cache = RegimeCache(REGIME_DB)
    cost_tracker = CostTracker(REGIME_DB)
    store = SmoothedStateStore(REGIME_DB)

    trading_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    if cost_tracker.is_budget_exceeded(trading_date):
        return None, 402, (
            f"Daily classifier budget exhausted "
            f"(${cost_tracker.get_daily_spend(trading_date):.4f} of "
            f"${DEFAULT_MAX_DAILY_COST_USD:.2f})"
        )

    cached = _cached_features(REGIME_DB, symbol)
    if cached is None:
        return None, 409, (
            f"This instrument hasn't been classified yet. The next "
            f"scheduled classification will run at ~21:30 UTC (US) / "
            f"~17:00 UTC (LSE). Or restart the bot to force a fresh cycle."
        )
    features, features_trading_date = cached

    classifier = RegimeClassifier(cache, cost_tracker)
    if not classifier.is_available():
        return None, 503, "Anthropic client unavailable (ANTHROPIC_API_KEY not set)"

    # Cost before/after delta for the per-call cost_usd in the response.
    spend_before = cost_tracker.get_daily_spend(trading_date)
    classification = classifier.classify(symbol, trading_date, features, force=True)
    spend_after = cost_tracker.get_daily_spend(trading_date)
    cost_usd = max(spend_after - spend_before, 0.0)

    # Make sure the cache row exists even on fallback paths (classifier only
    # writes happy-path; mirroring the scheduler's behaviour for idempotency).
    cache.put(classification)

    prior = store.get_latest(symbol)
    if prior is None:
        prior = initial_state(symbol)
        smoothed = update_first_run(prior, classification)
    else:
        smoothed = update(prior, classification)
    store.put(smoothed)

    _classify_last_ts[symbol] = _time.time()

    return {
        "instrument": symbol,
        "raw_regime": classification.raw_regime,
        "confidence": classification.confidence,
        "smoothed_regime": smoothed.effective_regime,
        "days_in_regime": smoothed.days_in_regime,
        "cost_usd": cost_usd,
        "rationale": classification.rationale,
        "features_dated": features_trading_date,
    }, 200, None


@app.route('/api/regime/budget', methods=['GET'])
@require_auth
def regime_budget_route():
    from bot.regime.cost_tracker import CostTracker, DEFAULT_MAX_DAILY_COST_USD
    trading_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    spent = CostTracker(REGIME_DB).get_daily_spend(trading_date)
    return jsonify({
        "spent_today_usd": round(spent, 6),
        "max_daily_cost_usd": DEFAULT_MAX_DAILY_COST_USD,
        "remaining_usd": round(max(DEFAULT_MAX_DAILY_COST_USD - spent, 0.0), 6),
    })


@app.route('/api/regime/instruments', methods=['GET'])
@require_auth
def regime_instruments_route():
    """Active instrument symbols + per-instrument display metadata for the
    Classify dropdown. Excludes disabled/no-edge entries."""
    return jsonify({
        "instruments": _active_instruments_from_config(),
        "estimated_cost_per_classification_usd": ESTIMATED_COST_PER_CLASSIFICATION,
    })


@app.route('/api/regime/classify', methods=['POST'])
@require_auth
def regime_classify_route():
    body = request.get_json(silent=True) or {}
    do_all = bool(body.get('all'))
    symbol = body.get('instrument')

    if not do_all and not symbol:
        return jsonify({'error': "Body must include 'instrument' or 'all: true'"}), 400

    active = set(_active_instruments_from_config())

    if do_all:
        targets = sorted(active)
        if not targets:
            return jsonify({'error': 'No active instruments configured'}), 400
    else:
        if symbol not in active:
            return jsonify({'error': f"Unknown or inactive instrument: {symbol}"}), 400
        targets = [symbol]

    # Serialise all classifier calls — Anthropic billing is shared and we
    # want a stable per-day budget check between iterations.
    with _classify_lock:
        # Pre-flight rate limit: any target inside the cooldown window aborts.
        for s in targets:
            remaining = _rate_limit_remaining(s)
            if remaining > 0:
                return jsonify({
                    'error': f"Rate limited for {s}: retry in "
                             f"{int(remaining)}s",
                    'retry_after_seconds': int(remaining),
                }), 429

        results = []
        for s in targets:
            payload, status, err = _classify_one_locked(s)
            if status == 200:
                results.append(payload)
            else:
                # Budget / no-features / API-unavailable failures stop the
                # 'all' loop so the operator sees a clean partial result
                # instead of churning through more failed calls.
                if do_all:
                    return jsonify({
                        'partial_results': results,
                        'stopped_on': s,
                        'error': err,
                    }), status
                return jsonify({'error': err}), status

    if do_all:
        return jsonify({'results': results})
    return jsonify(results[0])


# ── Calendar UI routes (§15.4) ─────────────────────────
def _maybe_register_calendar():
    """Register calendar blueprint if enable_calendar_ui is true."""
    try:
        cfg_path = Path(CONFIG_FILE)
        if cfg_path.exists():
            with open(cfg_path) as f:
                data = json.load(f)
            flag = data.get('settings', {}).get('feature_flags', {}).get(
                'enable_calendar_ui', False)
        else:
            flag = False

        if flag:
            from bot.calendar_ui.routes import calendar_bp, init_calendar_routes
            regime_db = str(BASE_DIR / 'regime.db')
            init_calendar_routes(regime_db, JWT_SECRET)
            app.register_blueprint(calendar_bp)
    except Exception as e:
        print(f"[Calendar UI] Failed to register: {e}")

_maybe_register_calendar()


# ── Labelling worksheet endpoints (classifier evaluation) ──
import fcntl as _fcntl  # noqa: E402

LABELS_FILE = str(BASE_DIR / 'specs' / 'regime_labels.json')
LABELS_BARS_FILE = str(BASE_DIR / 'specs' / 'regime_labels_bars.json')
VALID_LABELS = {"TRENDING", "RANGING", "UNCLEAR"}
MAX_NOTES_LEN = 500


@app.route('/api/labels', methods=['GET'])
@require_auth
def labels_get():
    """Return the full labelling worksheet."""
    if not os.path.exists(LABELS_FILE):
        return jsonify({"error": "worksheet not generated yet"}), 404
    with open(LABELS_FILE, 'r') as f:
        return jsonify(json.load(f))


@app.route('/api/labels/bars', methods=['GET'])
@require_auth
def labels_bars_get():
    """Return the chart-bar dataset for the labelling worksheet."""
    if not os.path.exists(LABELS_BARS_FILE):
        return jsonify({"error": "bars not generated yet"}), 404
    with open(LABELS_BARS_FILE, 'r') as f:
        return jsonify(json.load(f))


@app.route('/api/labels/save', methods=['POST'])
@require_auth
def labels_save():
    """Save one window's label (read-modify-write under fcntl lock)."""
    payload = request.get_json(silent=True) or {}
    window_id = payload.get('window_id')
    my_label = payload.get('my_label')
    my_confidence = payload.get('my_confidence')
    my_notes = payload.get('my_notes')

    if not window_id or not isinstance(window_id, str):
        return jsonify({"error": "window_id required"}), 400
    if my_label not in VALID_LABELS:
        return jsonify({
            "error": f"my_label must be one of {sorted(VALID_LABELS)}"
        }), 400
    if not isinstance(my_confidence, int) or not (1 <= my_confidence <= 5):
        return jsonify({"error": "my_confidence must be int 1..5"}), 400
    if my_notes is not None:
        if not isinstance(my_notes, str):
            return jsonify({"error": "my_notes must be string"}), 400
        if len(my_notes) > MAX_NOTES_LEN:
            return jsonify({
                "error": f"my_notes exceeds {MAX_NOTES_LEN} chars"
            }), 400

    if not os.path.exists(LABELS_FILE):
        return jsonify({"error": "worksheet not generated yet"}), 404

    # Read-modify-write under exclusive lock so concurrent POSTs don't
    # clobber each other.
    with open(LABELS_FILE, 'r+') as f:
        _fcntl.flock(f.fileno(), _fcntl.LOCK_EX)
        try:
            data = json.load(f)
            updated = False
            for inst in data.get('instruments', []):
                for w in inst.get('windows', []):
                    if w.get('window_id') == window_id:
                        w['my_label'] = my_label
                        w['my_confidence'] = my_confidence
                        w['my_notes'] = my_notes
                        updated = True
                        break
                if updated:
                    break
            if not updated:
                return jsonify({"error": "window_id not found"}), 404
            f.seek(0)
            f.truncate()
            json.dump(data, f, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        finally:
            _fcntl.flock(f.fileno(), _fcntl.LOCK_UN)

    return jsonify({"ok": True, "window_id": window_id})


# ── Regime filter experiment performance endpoint ──
@app.route('/api/regime/filter_performance', methods=['GET'])
@require_auth
def regime_filter_performance_route():
    """Aggregate live-trades vs would-have-been-blocked shadow-trades
    P&L for the regime-filter experiment. Optional ?since=YYYY-MM-DD
    narrows live trades and blocked-entries to a date window; default
    is the earliest blocked-entry timestamp (so the comparison covers
    only the period since the experiment started); falls back to
    all-time if no blocked entries exist yet.
    """
    from bot.regime.filter_performance import aggregate as _agg

    since = request.args.get("since")

    def _connect_ro(path):
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    # Determine the comparison window if the caller didn't override.
    blocked_rows: list = []
    try:
        rdb = _connect_ro(REGIME_DB)
        if since is None:
            row = rdb.execute(
                "SELECT MIN(ts) FROM regime_blocked_entries"
            ).fetchone()
            since = row[0]  # may still be None if no rows
        if since:
            blocked_rows = [dict(r) for r in rdb.execute(
                "SELECT * FROM regime_blocked_entries WHERE ts >= ? "
                "ORDER BY id", (since,)
            ).fetchall()]
        else:
            blocked_rows = [dict(r) for r in rdb.execute(
                "SELECT * FROM regime_blocked_entries ORDER BY id"
            ).fetchall()]
        shadow_ids = [r["shadow_trade_id"] for r in blocked_rows
                      if r.get("shadow_trade_id")]
        if shadow_ids:
            placeholders = ",".join("?" for _ in shadow_ids)
            shadow_rows = [dict(r) for r in rdb.execute(
                f"SELECT * FROM shadow_hypothetical_trades "
                f"WHERE id IN ({placeholders})", shadow_ids
            ).fetchall()]
        else:
            shadow_rows = []
        rdb.close()
    except sqlite3.Error as e:
        return jsonify({"error": f"regime DB read failed: {e}"}), 500

    try:
        ldb = _connect_ro(LEARNING_DB)
        if since:
            live_rows = [dict(r) for r in ldb.execute(
                "SELECT entry_price, exit_price, pnl_usd FROM trades "
                "WHERE open = 0 AND timestamp >= ? ORDER BY id",
                (since,)
            ).fetchall()]
        else:
            live_rows = [dict(r) for r in ldb.execute(
                "SELECT entry_price, exit_price, pnl_usd FROM trades "
                "WHERE open = 0 ORDER BY id"
            ).fetchall()]
        ldb.close()
    except sqlite3.Error as e:
        return jsonify({"error": f"learning_loop DB read failed: {e}"}), 500

    summary = _agg(live_rows, blocked_rows, shadow_rows)
    summary["since"] = since
    return jsonify(summary)


if __name__ == '__main__':
    # Check users exist
    users = load_users()
    if not users:
        print('\n  WARNING: No users configured!')
        print('  Run: python3 manage_users.py add <username>')
        print()

    host = '127.0.0.1'
    print(f'API server running on http://{host}:{_API_PORT}')
    print(f'Config file: {CONFIG_FILE}')
    print(f'Users: {len(users)} configured')
    print(f'JWT expiry: {JWT_EXPIRY_HOURS}h')
    print(f'Rate limit: 5 login attempts per minute per IP')
    app.run(host=host, port=_API_PORT, debug=False)
