"""
tests/test_instrument_api.py — Test API endpoints for instrument management.
Uses Flask test client and temp files.
"""

import json
import os
import shutil
import sqlite3
import tempfile
from unittest.mock import patch

import pytest


@pytest.fixture
def setup_api(tmp_path):
    """Set up Flask test client with temp instruments.json and backtest.db."""
    # Create test instruments.json
    instruments_data = {
        "settings": {
            "host": "127.0.0.1", "port": 4000, "client_id": 1,
            "account": "TEST", "check_interval_mins": 1,
            "portfolio_loss_limit": 1000, "web_dir": "web",
            "rsi_period": 14, "rsi_oversold": 35, "rsi_overbought": 70,
            "williams_r_period": 14, "williams_r_mid": -50,
            "williams_r_oversold": -80, "williams_r_overbought": -20,
            "adx_period": 14, "adx_threshold": 20,
            "ma200_period": 200, "alligator_min_gap_pct": 0.003,
        },
        "layer1_active": [
            {"symbol": "BARC", "name": "Barclays", "sec_type": "STK",
             "exchange": "SMART", "currency": "GBP", "qty": 1000,
             "enabled": True, "trail_stop_pct": 4.0, "take_profit_pct": 12.0,
             "emergency_stop_pct": 10.0},
            {"symbol": "MSFT", "name": "Microsoft", "sec_type": "STK",
             "exchange": "SMART", "currency": "USD", "qty": 10,
             "enabled": True, "trail_stop_pct": 2.0, "take_profit_pct": 8.0,
             "emergency_stop_pct": 5.0},
            {"symbol": "SHEL", "name": "Shell", "sec_type": "STK",
             "exchange": "SMART", "currency": "GBP", "qty": 40,
             "enabled": False, "trail_stop_pct": 1.5, "take_profit_pct": 5.0,
             "emergency_stop_pct": 5.0},
        ],
        "layer2_accumulation": [],
    }

    config_file = str(tmp_path / "instruments.json")
    with open(config_file, 'w') as f:
        json.dump(instruments_data, f)

    backup_dir = str(tmp_path / "backups")
    os.makedirs(backup_dir, exist_ok=True)

    # Create test backtest.db with WF results
    db_path = str(tmp_path / "backtest.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wf_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT, symbol TEXT, timeframe TEXT,
            is_pnl REAL, is_profit_factor REAL, is_win_rate REAL, is_trade_count INTEGER,
            oos_pnl REAL, oos_profit_factor REAL, oos_win_rate REAL, oos_trade_count INTEGER,
            wf_efficiency REAL, best_stop_pct REAL, best_tp_pct REAL,
            param_stability TEXT, verdict TEXT, train_months INTEGER, test_months INTEGER
        );
    """)
    conn.execute(
        "INSERT INTO wf_results VALUES (NULL, '2026-03-25', 'BARC', '4hr', "
        "50000, 3.5, 0.72, 400, 33791, 2.85, 0.70, 373, 0.76, 5.0, 12.0, "
        "'stable', 'robust', 6, 3)"
    )
    conn.execute(
        "INSERT INTO wf_results VALUES (NULL, '2026-03-25', 'MSFT', '4hr', "
        "10000, 2.5, 0.65, 200, 8097, 2.68, 0.68, 180, 0.55, 2.0, 8.0, "
        "'stable', 'robust', 6, 3)"
    )
    conn.execute(
        "INSERT INTO wf_results VALUES (NULL, '2026-03-25', 'SHEL', '4hr', "
        "5000, 1.0, 0.50, 100, -500, 0.8, 0.45, 90, 0.2, 1.5, 5.0, "
        "'unstable', 'no_edge', 6, 3)"
    )
    conn.commit()
    conn.close()

    import api_server
    api_server.CONFIG_FILE = config_file
    api_server.BACKUP_DIR = backup_dir
    api_server.BACKTEST_DB = db_path
    api_server.JWT_SECRET = "test_secret_key_12345"

    app = api_server.app
    app.config['TESTING'] = True

    # Create a valid JWT token
    token = api_server.create_token("testuser")

    client = app.test_client()
    return client, token, config_file, db_path


def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# --- GET /api/instruments/wf-recommendations ---

def test_wf_recommendations_merges_sources(setup_api):
    client, token, _, _ = setup_api
    r = client.get('/api/instruments/wf-recommendations', headers=auth_headers(token))
    assert r.status_code == 200
    data = r.get_json()
    instruments = data['instruments']
    barc = next(i for i in instruments if i['symbol'] == 'BARC')
    assert barc['wf_verdict'] == 'robust'
    assert barc['wf_efficiency'] == 0.76


def test_wf_recommendations_no_edge_nulls_params(setup_api):
    client, token, _, _ = setup_api
    r = client.get('/api/instruments/wf-recommendations', headers=auth_headers(token))
    data = r.get_json()
    shel = next(i for i in data['instruments'] if i['symbol'] == 'SHEL')
    assert shel['wf_verdict'] == 'no_edge'
    assert shel['wf_stop'] is None
    assert shel['wf_tp'] is None


def test_wf_recommendations_params_match_true(setup_api):
    client, token, _, _ = setup_api
    r = client.get('/api/instruments/wf-recommendations', headers=auth_headers(token))
    data = r.get_json()
    # BARC: current stop=4.0, wf_stop=5.0 -> mismatch
    # MSFT: current stop=2.0, wf_stop=2.0 -> match
    msft = next(i for i in data['instruments'] if i['symbol'] == 'MSFT')
    assert msft['params_match'] is True


def test_wf_recommendations_params_match_false(setup_api):
    client, token, _, _ = setup_api
    r = client.get('/api/instruments/wf-recommendations', headers=auth_headers(token))
    data = r.get_json()
    barc = next(i for i in data['instruments'] if i['symbol'] == 'BARC')
    assert barc['params_match'] is False  # 4.0 vs 5.0


def test_wf_recommendations_params_match_within_tolerance(setup_api):
    """current_stop=5.0, wf_stop=5.2 -> params_match=true (within 0.5%)."""
    client, token, config_file, _ = setup_api
    # Update BARC stop to 5.0 (WF says 5.0, exact match)
    with open(config_file) as f:
        data = json.load(f)
    data['layer1_active'][0]['trail_stop_pct'] = 5.2
    with open(config_file, 'w') as f:
        json.dump(data, f)

    r = client.get('/api/instruments/wf-recommendations', headers=auth_headers(token))
    resp = r.get_json()
    barc = next(i for i in resp['instruments'] if i['symbol'] == 'BARC')
    assert barc['params_match'] is True  # 5.2 vs 5.0 within 0.5


def test_wf_recommendations_empty_db(setup_api):
    """If backtest.db has no wf_results, all wf fields are null."""
    client, token, _, db_path = setup_api
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM wf_results")
    conn.commit()
    conn.close()

    r = client.get('/api/instruments/wf-recommendations', headers=auth_headers(token))
    data = r.get_json()
    barc = next(i for i in data['instruments'] if i['symbol'] == 'BARC')
    assert barc['wf_verdict'] is None
    assert barc['wf_stop'] is None


# --- POST /api/instruments/update ---

def test_update_trading_params(setup_api):
    client, token, config_file, _ = setup_api
    r = client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [
            {"symbol": "BARC", "trail_stop_pct": 6.0, "take_profit_pct": 8.0}
        ]}))
    assert r.status_code == 200
    assert r.get_json()['ok'] is True

    with open(config_file) as f:
        data = json.load(f)
    barc = next(i for i in data['layer1_active'] if i['symbol'] == 'BARC')
    assert barc['trail_stop_pct'] == 6.0
    assert barc['take_profit_pct'] == 8.0


def test_update_qty(setup_api):
    client, token, config_file, _ = setup_api
    r = client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "qty": 800}]}))
    assert r.status_code == 200
    with open(config_file) as f:
        data = json.load(f)
    barc = next(i for i in data['layer1_active'] if i['symbol'] == 'BARC')
    assert barc['qty'] == 800


def test_update_indicator_params(setup_api):
    client, token, config_file, _ = setup_api
    r = client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [
            {"symbol": "BARC", "indicators": {"rsi_period": 10}}
        ]}))
    assert r.status_code == 200
    with open(config_file) as f:
        data = json.load(f)
    barc = next(i for i in data['layer1_active'] if i['symbol'] == 'BARC')
    assert barc['indicators']['rsi_period'] == 10


def test_update_multiple_instruments(setup_api):
    client, token, _, _ = setup_api
    r = client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [
            {"symbol": "BARC", "trail_stop_pct": 6.0},
            {"symbol": "MSFT", "trail_stop_pct": 3.0},
        ]}))
    assert r.status_code == 200
    result = r.get_json()
    assert len(result['updated']) == 2


def test_update_partial_fields(setup_api):
    """Only include stop_pct — other fields should remain unchanged."""
    client, token, config_file, _ = setup_api
    r = client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "trail_stop_pct": 6.0}]}))
    assert r.status_code == 200
    with open(config_file) as f:
        data = json.load(f)
    barc = next(i for i in data['layer1_active'] if i['symbol'] == 'BARC')
    assert barc['trail_stop_pct'] == 6.0
    assert barc['take_profit_pct'] == 12.0  # unchanged


def test_update_creates_backup(setup_api):
    client, token, _, _ = setup_api
    import api_server
    backup_dir = api_server.BACKUP_DIR
    before = len(os.listdir(backup_dir))
    client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "trail_stop_pct": 6.0}]}))
    after = len(os.listdir(backup_dir))
    assert after > before


def test_update_rejects_negative_stop(setup_api):
    client, token, _, _ = setup_api
    r = client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "trail_stop_pct": -1.0}]}))
    assert r.status_code == 400


def test_update_rejects_zero_stop(setup_api):
    client, token, _, _ = setup_api
    r = client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "trail_stop_pct": 0}]}))
    assert r.status_code == 400


def test_update_rejects_stop_over_20(setup_api):
    client, token, _, _ = setup_api
    r = client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "trail_stop_pct": 25.0}]}))
    assert r.status_code == 400


def test_update_rejects_zero_qty(setup_api):
    client, token, _, _ = setup_api
    r = client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "qty": 0}]}))
    assert r.status_code == 400


def test_update_rejects_negative_qty(setup_api):
    client, token, _, _ = setup_api
    r = client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "qty": -5}]}))
    assert r.status_code == 400


def test_update_rejects_tp_over_50(setup_api):
    client, token, _, _ = setup_api
    r = client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "take_profit_pct": 60.0}]}))
    assert r.status_code == 400


def test_update_rejects_emergency_below_trail(setup_api):
    client, token, _, _ = setup_api
    r = client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [
            {"symbol": "BARC", "trail_stop_pct": 5.0, "emergency_stop_pct": 3.0}
        ]}))
    assert r.status_code == 400


def test_update_rejects_unknown_symbol(setup_api):
    client, token, _, _ = setup_api
    r = client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "ZZZZZ", "trail_stop_pct": 5.0}]}))
    assert r.status_code == 400


def test_update_rejects_forbidden_fields(setup_api):
    """Trying to update 'sec_type', 'exchange' -> rejected."""
    client, token, _, _ = setup_api
    r = client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "sec_type": "CFD"}]}))
    assert r.status_code == 400
    # Also test exchange
    r2 = client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "exchange": "NYSE"}]}))
    assert r2.status_code == 400


def test_update_preserves_json_format(setup_api):
    client, token, config_file, _ = setup_api
    client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "trail_stop_pct": 6.0}]}))
    with open(config_file) as f:
        data = json.load(f)
    # All original keys still present
    assert 'settings' in data
    assert 'layer1_active' in data
    # MSFT unchanged
    msft = next(i for i in data['layer1_active'] if i['symbol'] == 'MSFT')
    assert msft['trail_stop_pct'] == 2.0


def test_update_indicator_override_creates_block(setup_api):
    """If instrument has no 'indicators' block, updating creates it."""
    client, token, config_file, _ = setup_api
    r = client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [
            {"symbol": "MSFT", "indicators": {"rsi_period": 10}}
        ]}))
    assert r.status_code == 200
    with open(config_file) as f:
        data = json.load(f)
    msft = next(i for i in data['layer1_active'] if i['symbol'] == 'MSFT')
    assert 'indicators' in msft
    assert msft['indicators']['rsi_period'] == 10


def test_update_indicator_null_removes_override(setup_api):
    """Setting indicators.rsi_period to null removes the override."""
    client, token, config_file, _ = setup_api
    # First add an override
    client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [
            {"symbol": "BARC", "indicators": {"rsi_period": 10}}
        ]}))
    # Now remove it
    r = client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [
            {"symbol": "BARC", "indicators": {"rsi_period": None}}
        ]}))
    assert r.status_code == 200
    with open(config_file) as f:
        data = json.load(f)
    barc = next(i for i in data['layer1_active'] if i['symbol'] == 'BARC')
    # indicators block should be removed (empty)
    assert 'indicators' not in barc or 'rsi_period' not in barc.get('indicators', {})


# --- POST /api/instruments/apply-wf ---

def test_apply_wf_updates_stop_tp(setup_api):
    client, token, config_file, _ = setup_api
    r = client.post('/api/instruments/apply-wf',
        headers=auth_headers(token),
        data=json.dumps({"symbols": ["BARC"]}))
    assert r.status_code == 200
    result = r.get_json()
    assert "BARC" in result['applied']

    with open(config_file) as f:
        data = json.load(f)
    barc = next(i for i in data['layer1_active'] if i['symbol'] == 'BARC')
    assert barc['trail_stop_pct'] == 5.0
    assert barc['take_profit_pct'] == 12.0


def test_apply_wf_skips_no_edge(setup_api):
    client, token, _, _ = setup_api
    r = client.post('/api/instruments/apply-wf',
        headers=auth_headers(token),
        data=json.dumps({"symbols": ["SHEL"]}))
    result = r.get_json()
    assert any(s['symbol'] == 'SHEL' for s in result['skipped'])


def test_apply_wf_all_instruments(setup_api):
    client, token, _, _ = setup_api
    r = client.post('/api/instruments/apply-wf',
        headers=auth_headers(token),
        data=json.dumps({"symbols": "all"}))
    result = r.get_json()
    assert "BARC" in result['applied']
    assert "MSFT" in result['applied']
    assert any(s['symbol'] == 'SHEL' for s in result['skipped'])


def test_apply_wf_does_not_change_indicators(setup_api):
    """Apply WF should only change stop/TP, never indicator settings."""
    client, token, config_file, _ = setup_api
    # Add indicator override first
    with open(config_file) as f:
        data = json.load(f)
    data['layer1_active'][0]['indicators'] = {"rsi_period": 10}
    with open(config_file, 'w') as f:
        json.dump(data, f)

    client.post('/api/instruments/apply-wf',
        headers=auth_headers(token),
        data=json.dumps({"symbols": ["BARC"]}))

    with open(config_file) as f:
        data = json.load(f)
    barc = next(i for i in data['layer1_active'] if i['symbol'] == 'BARC')
    assert barc['indicators']['rsi_period'] == 10  # unchanged


def test_apply_wf_creates_backup(setup_api):
    client, token, _, _ = setup_api
    import api_server
    backup_dir = api_server.BACKUP_DIR
    before = len(os.listdir(backup_dir))
    client.post('/api/instruments/apply-wf',
        headers=auth_headers(token),
        data=json.dumps({"symbols": ["BARC"]}))
    after = len(os.listdir(backup_dir))
    assert after > before


# --- POST /api/instruments/toggle-enable ---

def test_toggle_enable_on(setup_api):
    client, token, config_file, _ = setup_api
    r = client.post('/api/instruments/toggle-enable',
        headers=auth_headers(token),
        data=json.dumps({"symbol": "SHEL", "enabled": True}))
    assert r.status_code == 200
    with open(config_file) as f:
        data = json.load(f)
    shel = next(i for i in data['layer1_active'] if i['symbol'] == 'SHEL')
    assert shel['enabled'] is True


def test_toggle_enable_off(setup_api):
    client, token, config_file, _ = setup_api
    r = client.post('/api/instruments/toggle-enable',
        headers=auth_headers(token),
        data=json.dumps({"symbol": "BARC", "enabled": False}))
    assert r.status_code == 200
    with open(config_file) as f:
        data = json.load(f)
    barc = next(i for i in data['layer1_active'] if i['symbol'] == 'BARC')
    assert barc['enabled'] is False


def test_toggle_creates_backup(setup_api):
    client, token, _, _ = setup_api
    import api_server
    backup_dir = api_server.BACKUP_DIR
    before = len(os.listdir(backup_dir))
    client.post('/api/instruments/toggle-enable',
        headers=auth_headers(token),
        data=json.dumps({"symbol": "BARC", "enabled": False}))
    after = len(os.listdir(backup_dir))
    assert after > before


def test_toggle_unknown_symbol(setup_api):
    client, token, _, _ = setup_api
    r = client.post('/api/instruments/toggle-enable',
        headers=auth_headers(token),
        data=json.dumps({"symbol": "ZZZZZ", "enabled": True}))
    assert r.status_code == 400
