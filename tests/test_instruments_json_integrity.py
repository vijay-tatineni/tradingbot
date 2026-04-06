"""
tests/test_instruments_json_integrity.py — Test that instruments.json stays valid.
"""

import json
import os
import sqlite3

import pytest


@pytest.fixture
def setup_api(tmp_path):
    """Set up Flask test client with temp instruments.json."""
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
        ],
        "layer2_accumulation": [
            {"symbol": "QQQ", "name": "NASDAQ ETF", "sec_type": "STK",
             "exchange": "SMART", "currency": "USD", "qty": 3, "enabled": True},
        ],
        "layer3_silver": [
            {"symbol": "SSLN", "name": "Silver ETF", "qty": 164},
        ],
    }

    config_file = str(tmp_path / "instruments.json")
    with open(config_file, 'w') as f:
        json.dump(instruments_data, f)

    backup_dir = str(tmp_path / "backups")
    os.makedirs(backup_dir, exist_ok=True)

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
    conn.commit()
    conn.close()

    import api_server
    api_server.CONFIG_FILE = config_file
    api_server.BACKUP_DIR = backup_dir
    api_server.BACKTEST_DB = db_path
    api_server.JWT_SECRET = "test_secret_key_12345"

    app = api_server.app
    app.config['TESTING'] = True
    token = api_server.create_token("testuser")
    client = app.test_client()
    return client, token, config_file, backup_dir


def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _load_json(path):
    with open(path) as f:
        return json.load(f)


def test_json_valid_after_update(setup_api):
    client, token, config_file, _ = setup_api
    client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "trail_stop_pct": 6.0}]}))
    data = _load_json(config_file)
    assert isinstance(data, dict)
    assert 'settings' in data
    assert 'layer1_active' in data


def test_json_valid_after_apply_wf(setup_api):
    client, token, config_file, _ = setup_api
    client.post('/api/instruments/apply-wf',
        headers=auth_headers(token),
        data=json.dumps({"symbols": ["BARC"]}))
    data = _load_json(config_file)
    assert isinstance(data, dict)
    assert 'settings' in data


def test_json_valid_after_toggle(setup_api):
    client, token, config_file, _ = setup_api
    client.post('/api/instruments/toggle-enable',
        headers=auth_headers(token),
        data=json.dumps({"symbol": "BARC", "enabled": False}))
    data = _load_json(config_file)
    assert isinstance(data, dict)
    assert 'settings' in data


def test_json_preserves_all_keys(setup_api):
    client, token, config_file, _ = setup_api
    client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "trail_stop_pct": 6.0}]}))
    data = _load_json(config_file)
    assert 'settings' in data
    assert 'layer1_active' in data
    assert 'layer2_accumulation' in data
    assert 'layer3_silver' in data


def test_json_preserves_non_modified_instruments(setup_api):
    client, token, config_file, _ = setup_api
    client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "trail_stop_pct": 6.0}]}))
    data = _load_json(config_file)
    msft = next(i for i in data['layer1_active'] if i['symbol'] == 'MSFT')
    assert msft['trail_stop_pct'] == 2.0
    assert msft['take_profit_pct'] == 8.0
    assert msft['qty'] == 10


def test_json_preserves_settings_block(setup_api):
    client, token, config_file, _ = setup_api
    client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [
            {"symbol": "BARC", "indicators": {"rsi_period": 10}}
        ]}))
    data = _load_json(config_file)
    assert data['settings']['rsi_period'] == 14
    assert data['settings']['adx_threshold'] == 20
    assert data['settings']['ma200_period'] == 200


def test_json_preserves_layer2_layer3(setup_api):
    client, token, config_file, _ = setup_api
    client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "trail_stop_pct": 6.0}]}))
    data = _load_json(config_file)
    assert len(data['layer2_accumulation']) == 1
    assert data['layer2_accumulation'][0]['symbol'] == 'QQQ'
    assert len(data['layer3_silver']) == 1
    assert data['layer3_silver'][0]['symbol'] == 'SSLN'


def test_backup_file_is_valid_json(setup_api):
    client, token, _, backup_dir = setup_api
    client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "trail_stop_pct": 6.0}]}))
    backups = os.listdir(backup_dir)
    assert len(backups) > 0
    backup_path = os.path.join(backup_dir, backups[0])
    data = _load_json(backup_path)
    assert isinstance(data, dict)
    assert 'settings' in data


def test_backup_is_created_before_write(setup_api):
    """Backup timestamp is before new instruments.json."""
    client, token, config_file, backup_dir = setup_api
    client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [{"symbol": "BARC", "trail_stop_pct": 6.0}]}))
    backups = os.listdir(backup_dir)
    assert len(backups) > 0
    backup_path = os.path.join(backup_dir, backups[0])
    # Backup has original value
    backup_data = _load_json(backup_path)
    barc_backup = next(i for i in backup_data['layer1_active'] if i['symbol'] == 'BARC')
    assert barc_backup['trail_stop_pct'] == 4.0  # Original value


def test_indicators_block_position_in_json(setup_api):
    """'indicators' block is inside instrument object, not at top level."""
    client, token, config_file, _ = setup_api
    client.post('/api/instruments/update',
        headers=auth_headers(token),
        data=json.dumps({"changes": [
            {"symbol": "BARC", "indicators": {"rsi_period": 10}}
        ]}))
    data = _load_json(config_file)
    # indicators should NOT be at top level
    assert 'indicators' not in data
    # Should be inside the instrument
    barc = next(i for i in data['layer1_active'] if i['symbol'] == 'BARC')
    assert 'indicators' in barc
    assert barc['indicators']['rsi_period'] == 10
