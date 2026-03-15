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
import shutil
import datetime
import secrets
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


# ── Config helpers ────────────────────────────────────

def load():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def save(data):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    shutil.copy(CONFIG_FILE, f'{BACKUP_DIR}/instruments_{ts}.json')
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=2)


# ── Protected routes ──────────────────────────────────

@app.route('/api/instruments', methods=['GET'])
@require_auth
def get_instruments():
    return jsonify(load())


@app.route('/api/instruments', methods=['POST'])
@require_auth
def save_instruments():
    data = request.get_json()
    try:
        save(data)
        return jsonify({'ok': True, 'message': 'Saved successfully'})
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)}), 500


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
