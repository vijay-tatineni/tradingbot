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
from flask import Flask, jsonify, request
from flask_cors import CORS

BASE_DIR    = Path(__file__).parent
CONFIG_FILE = str(BASE_DIR / 'instruments.json')
BACKUP_DIR  = str(BASE_DIR / 'backups')

app = Flask(__name__)
CORS(app)  # allow requests from dashboard page


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
    print(f"API server running on http://0.0.0.0:8081")
    print(f"Config file: {CONFIG_FILE}")
    app.run(host='0.0.0.0', port=8081, debug=False)
