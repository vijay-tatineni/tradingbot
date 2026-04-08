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

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = str(BASE_DIR / 'instruments.json')
BACKUP_DIR  = str(BASE_DIR / 'backups')
USERS_FILE  = str(BASE_DIR / 'users.json')

# JWT secret — auto-generated on first run, persisted in .env
JWT_SECRET = os.getenv('JWT_SECRET', '')
if not JWT_SECRET:
    JWT_SECRET = secrets.token_hex(32)
    env_path = BASE_DIR / '.env'
    with open(env_path, 'a') as f:
        f.write(f'\nJWT_SECRET={JWT_SECRET}\n')

JWT_EXPIRY_HOURS = 24

app = Flask(__name__)
CORS(app, origins=['http://188.166.150.137:8082', 'http://127.0.0.1:8082'])

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
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    shutil.copy(CONFIG_FILE, f'{BACKUP_DIR}/instruments_{ts}.json')
    # Atomic write: write to temp file then rename to avoid corrupt reads
    tmp_path = CONFIG_FILE + '.tmp'
    with open(tmp_path, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, CONFIG_FILE)


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

    try:
        save(data)
        return jsonify({'ok': True, 'message': 'Saved successfully'})
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
        required_settings = [
            'host', 'port', 'client_id', 'account',
            'check_interval_mins', 'portfolio_loss_limit', 'web_dir',
        ]
        for key in required_settings:
            if key not in data['settings']:
                errors.append(f"Missing required setting: '{key}'")

    # ── layer1_active section ────────────────────────────────
    if 'layer1_active' not in data:
        errors.append("Missing 'layer1_active' section")
    elif not isinstance(data['layer1_active'], list):
        errors.append("'layer1_active' must be a list")
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

    return errors


@app.route('/api/instruments/layer1', methods=['POST'])
@require_auth
def save_layer1():
    instruments = request.get_json()
    data = load()
    data['layer1_active'] = instruments
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


if __name__ == '__main__':
    # Check users exist
    users = load_users()
    if not users:
        print('\n  WARNING: No users configured!')
        print('  Run: python3 manage_users.py add <username>')
        print()

    host = '127.0.0.1'
    print(f'API server running on http://{host}:8081')
    print(f'Config file: {CONFIG_FILE}')
    print(f'Users: {len(users)} configured')
    print(f'JWT expiry: {JWT_EXPIRY_HOURS}h')
    print(f'Rate limit: 5 login attempts per minute per IP')
    app.run(host=host, port=8081, debug=False)
