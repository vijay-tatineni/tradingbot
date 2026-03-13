"""
api_server.py
Lightweight Flask API for reading and writing instruments.json.
Runs on port 8081. The instruments.html UI calls this.

Start:  python3 api_server.py
Screen: screen -S api  →  python3 api_server.py  →  Ctrl+A D
"""

import json
import os
import shutil
import datetime
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

load_dotenv(Path(__file__).parent / '.env')

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = str(BASE_DIR / 'instruments.json')
BACKUP_DIR  = str(BASE_DIR / 'backups')
API_TOKEN   = os.getenv('API_TOKEN', '')

app = Flask(__name__)
CORS(app, origins=['http://188.166.150.137:8080', 'http://127.0.0.1:8080'])


@app.before_request
def check_auth():
    """Require X-API-Token header on all requests."""
    if not API_TOKEN:
        return  # no token configured = auth disabled (dev mode)
    token = request.headers.get('X-API-Token', '')
    if token != API_TOKEN:
        return jsonify({'error': 'Unauthorized'}), 401


def load():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def save(data):
    # Always backup before saving
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    shutil.copy(CONFIG_FILE, f'{BACKUP_DIR}/instruments_{ts}.json')
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=2)


# ── Routes ────────────────────────────────────────────────────

@app.route('/api/instruments', methods=['GET'])
def get_instruments():
    return jsonify(load())


@app.route('/api/instruments', methods=['POST'])
def save_instruments():
    data = request.get_json()
    try:
        save(data)
        return jsonify({'ok': True, 'message': 'Saved successfully'})
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)}), 500


@app.route('/api/instruments/layer1', methods=['POST'])
def save_layer1():
    instruments = request.get_json()
    data = load()
    data['layer1_active'] = instruments
    save(data)
    return jsonify({'ok': True})


@app.route('/api/instruments/layer2', methods=['POST'])
def save_layer2():
    instruments = request.get_json()
    data = load()
    data['layer2_accumulation'] = instruments
    save(data)
    return jsonify({'ok': True})


@app.route('/api/settings', methods=['POST'])
def save_settings():
    settings = request.get_json()
    data = load()
    data['settings'].update(settings)
    save(data)
    return jsonify({'ok': True})


@app.route('/api/backups', methods=['GET'])
def list_backups():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    files = sorted(os.listdir(BACKUP_DIR), reverse=True)[:10]
    return jsonify(files)


if __name__ == '__main__':
    host = '127.0.0.1'
    print(f"API server running on http://{host}:8081")
    print(f"Config file: {CONFIG_FILE}")
    print(f"Auth: {'enabled' if API_TOKEN else 'DISABLED (set API_TOKEN in .env)'}")
    app.run(host=host, port=8081, debug=False)
