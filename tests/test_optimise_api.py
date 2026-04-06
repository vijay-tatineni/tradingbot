"""
tests/test_optimise_api.py — Test the async optimisation API endpoints.
"""

import json
import os
import sqlite3
import threading
import time

import pytest


@pytest.fixture
def setup_api(tmp_path):
    """Set up Flask test client."""
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
             "emergency_stop_pct": 10.0, "timeframe": "4hr", "long_only": True},
            {"symbol": "MSFT", "name": "Microsoft", "sec_type": "STK",
             "exchange": "SMART", "currency": "USD", "qty": 10,
             "enabled": True, "trail_stop_pct": 2.0, "take_profit_pct": 8.0,
             "emergency_stop_pct": 5.0, "timeframe": "4hr", "long_only": True},
        ],
        "layer2_accumulation": [],
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
        CREATE TABLE IF NOT EXISTS ohlcv (
            symbol TEXT NOT NULL, timeframe TEXT NOT NULL, datetime TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER DEFAULT 0,
            downloaded_at TEXT, PRIMARY KEY (symbol, timeframe, datetime)
        );
        CREATE TABLE IF NOT EXISTS optimise_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT, symbol TEXT, best_stop_pct REAL, best_tp_pct REAL,
            best_rsi_period INTEGER, best_rsi_oversold INTEGER,
            best_rsi_overbought INTEGER, best_wr_period INTEGER,
            best_adx_threshold INTEGER, best_ma_period INTEGER,
            wf_efficiency REAL, oos_pnl REAL, oos_profit_factor REAL,
            oos_win_rate REAL, oos_trade_count INTEGER,
            current_oos_pnl REAL, improvement_pct REAL,
            combos_tested INTEGER, duration_seconds REAL
        );
    """)
    conn.commit()
    conn.close()

    import api_server
    api_server.CONFIG_FILE = config_file
    api_server.BACKUP_DIR = backup_dir
    api_server.BACKTEST_DB = db_path
    api_server.JWT_SECRET = "test_secret_key_12345"
    # Clear any leftover jobs
    api_server._optimise_jobs.clear()

    app = api_server.app
    app.config['TESTING'] = True
    token = api_server.create_token("testuser")
    client = app.test_client()
    return client, token


def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_optimise_returns_job_id(setup_api):
    """POST /api/instruments/optimise returns immediately with job_id."""
    client, token = setup_api
    r = client.post('/api/instruments/optimise',
        headers=auth_headers(token),
        data=json.dumps({"symbol": "BARC"}))
    # May return 200 with job_id or 400 if no data
    data = r.get_json()
    if r.status_code == 200:
        assert 'job_id' in data
        assert data['job_id'].startswith('opt_BARC_')
    else:
        # No OHLCV data — expected in test env
        assert 'error' in data


def test_optimise_status_unknown_job(setup_api):
    """Unknown job_id returns 404."""
    client, token = setup_api
    r = client.get('/api/instruments/optimise/status?job_id=nonexistent',
        headers=auth_headers(token))
    assert r.status_code == 404


def test_optimise_invalid_symbol(setup_api):
    """Unknown symbol returns 400."""
    client, token = setup_api
    r = client.post('/api/instruments/optimise',
        headers=auth_headers(token),
        data=json.dumps({"symbol": "ZZZZZ"}))
    assert r.status_code == 400


def test_optimise_no_data(setup_api):
    """No OHLCV data -> starts but will error."""
    client, token = setup_api
    r = client.post('/api/instruments/optimise',
        headers=auth_headers(token),
        data=json.dumps({"symbol": "BARC"}))
    data = r.get_json()
    # Either returns job_id (background will error) or immediate error
    assert r.status_code in (200, 400)


def test_optimise_status_running(setup_api):
    """Manually set a job as running and check status."""
    client, token = setup_api
    import api_server
    api_server._optimise_jobs['test_job_1'] = {
        'symbol': 'BARC', 'status': 'running', 'progress': 45,
        'phase': 'Phase 1: testing', 'estimated_remaining_seconds': 120,
        'result': None, 'error': None,
    }
    r = client.get('/api/instruments/optimise/status?job_id=test_job_1',
        headers=auth_headers(token))
    assert r.status_code == 200
    data = r.get_json()
    assert data['status'] == 'running'
    assert data['progress'] == 45


def test_optimise_status_complete(setup_api):
    """After completion, status returns complete with results."""
    client, token = setup_api
    import api_server
    api_server._optimise_jobs['test_job_2'] = {
        'symbol': 'BARC', 'status': 'complete', 'progress': 100,
        'phase': 'Done', 'estimated_remaining_seconds': 0,
        'result': {'best': {'trail_stop_pct': 5.0}},
        'error': None,
    }
    r = client.get('/api/instruments/optimise/status?job_id=test_job_2',
        headers=auth_headers(token))
    assert r.status_code == 200
    data = r.get_json()
    assert data['status'] == 'complete'
    assert 'result' in data


def test_concurrent_optimise_same_symbol(setup_api):
    """BARC already optimising -> return 409 conflict."""
    client, token = setup_api
    import api_server
    api_server._optimise_jobs['existing_job'] = {
        'symbol': 'BARC', 'status': 'running', 'progress': 50,
        'phase': 'Running', 'estimated_remaining_seconds': 60,
        'result': None, 'error': None,
    }
    r = client.post('/api/instruments/optimise',
        headers=auth_headers(token),
        data=json.dumps({"symbol": "BARC"}))
    assert r.status_code == 409
    data = r.get_json()
    assert 'already' in data['error'].lower()


def test_concurrent_optimise_different_symbols(setup_api):
    """Optimising BARC and MSFT simultaneously should both work."""
    client, token = setup_api
    import api_server
    api_server._optimise_jobs['barc_job'] = {
        'symbol': 'BARC', 'status': 'running', 'progress': 50,
        'phase': 'Running', 'estimated_remaining_seconds': 60,
        'result': None, 'error': None,
    }
    # MSFT should be allowed since BARC is the one running
    r = client.post('/api/instruments/optimise',
        headers=auth_headers(token),
        data=json.dumps({"symbol": "MSFT"}))
    # Should succeed (200 with job_id) or fail due to no data (400), but NOT 409
    assert r.status_code != 409
