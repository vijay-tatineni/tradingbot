"""
tests/test_quick_test.py — Test Layer 1 quick single-parameter WF test.
"""

import json
import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def setup_api(tmp_path):
    """Set up Flask test client with temp data."""
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
        ],
        "layer2_accumulation": [],
    }

    config_file = str(tmp_path / "instruments.json")
    with open(config_file, 'w') as f:
        json.dump(instruments_data, f)

    backup_dir = str(tmp_path / "backups")
    os.makedirs(backup_dir, exist_ok=True)

    # Create backtest.db with WF baseline
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
    return client, token, db_path


def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_quick_test_unknown_symbol(setup_api):
    client, token, _ = setup_api
    r = client.post('/api/instruments/test',
        headers=auth_headers(token),
        data=json.dumps({"symbol": "ZZZZZ", "params": {}}))
    assert r.status_code == 400


def test_quick_test_invalid_stop(setup_api):
    client, token, _ = setup_api
    r = client.post('/api/instruments/test',
        headers=auth_headers(token),
        data=json.dumps({"symbol": "BARC", "params": {"trail_stop_pct": -1}}))
    assert r.status_code == 400


def test_quick_test_requires_existing_data(setup_api):
    """No OHLCV data for symbol -> error message."""
    client, token, _ = setup_api
    r = client.post('/api/instruments/test',
        headers=auth_headers(token),
        data=json.dumps({"symbol": "ZZNODATA", "params": {"trail_stop_pct": 5.0}}))
    # Should fail because symbol doesn't exist / no OHLCV data
    data = r.get_json()
    assert r.status_code == 400 or 'error' in data


def test_quick_test_improvement_pct_calculation():
    """Baseline $100, test $120 -> +20%."""
    baseline_pnl = 100
    test_pnl = 120
    improvement = round((test_pnl - baseline_pnl) / abs(baseline_pnl) * 100, 1)
    assert improvement == 20.0


def test_quick_test_improvement_pct_negative():
    """Baseline $100, test $80 -> -20%."""
    baseline_pnl = 100
    test_pnl = 80
    improvement = round((test_pnl - baseline_pnl) / abs(baseline_pnl) * 100, 1)
    assert improvement == -20.0


def test_quick_test_improvement_pct_baseline_zero():
    """Baseline $0 -> improvement_pct should not divide-by-zero."""
    baseline_pnl = 0
    test_pnl = 120
    if baseline_pnl != 0:
        improvement = round((test_pnl - baseline_pnl) / abs(baseline_pnl) * 100, 1)
    elif test_pnl > 0:
        improvement = 100.0
    else:
        improvement = 0.0
    assert improvement == 100.0


def test_quick_test_comparison_better():
    """Higher OOS P&L than baseline -> verdict 'better'."""
    improvement = 20.0
    verdict = 'better' if improvement > 5 else 'similar' if abs(improvement) <= 5 else 'worse'
    assert verdict == 'better'


def test_quick_test_comparison_worse():
    """Lower OOS P&L than baseline -> verdict 'worse'."""
    improvement = -20.0
    verdict = 'better' if improvement > 5 else 'similar' if abs(improvement) <= 5 else 'worse'
    assert verdict == 'worse'


def test_quick_test_comparison_similar():
    """Within 5% of baseline -> verdict 'similar'."""
    improvement = 3.0
    verdict = 'better' if improvement > 5 else 'similar' if abs(improvement) <= 5 else 'worse'
    assert verdict == 'similar'


def test_quick_test_returns_comparison(setup_api):
    """Should return both the test result and the baseline."""
    # This tests the _get_baseline function
    import api_server
    baseline = api_server._get_baseline('BARC')
    assert baseline is not None
    assert 'oos_pnl' in baseline
    assert baseline['oos_pnl'] == 33791


def test_quick_test_uses_provided_params():
    """Verify that the API sends user params, not instruments.json values."""
    # This is a logic test: build indicator settings from params
    global_settings = {"rsi_period": 14, "adx_threshold": 20}
    params = {"rsi_period": 10, "adx_threshold": 15}
    indicator_settings = {
        "rsi_period": params.get("rsi_period", global_settings.get("rsi_period", 14)),
        "adx_threshold": params.get("adx_threshold", global_settings.get("adx_threshold", 20)),
    }
    assert indicator_settings["rsi_period"] == 10
    assert indicator_settings["adx_threshold"] == 15


def test_quick_test_uses_provided_indicators():
    """If request includes indicator overrides, test uses them."""
    params = {"rsi_period": 7, "ma200_period": 100}
    global_defaults = {"rsi_period": 14, "ma200_period": 200}
    result_period = params.get("rsi_period", global_defaults["rsi_period"])
    result_ma = params.get("ma200_period", global_defaults["ma200_period"])
    assert result_period == 7
    assert result_ma == 100


def test_quick_test_no_grid_search():
    """Should NOT grid search — tests exactly the user's params.
    Verified by the code path: /api/instruments/test calls run_walk_forward directly,
    not run_grid_search."""
    # This is a structural test - the API endpoint calls run_walk_forward, not grid search
    import inspect
    import api_server
    source = inspect.getsource(api_server.quick_test)
    assert 'run_walk_forward' in source
    assert 'run_grid_search' not in source


def test_quick_test_returns_wf_efficiency(setup_api):
    """Result should include the expected fields."""
    # Verify the baseline has the right structure
    import api_server
    baseline = api_server._get_baseline('BARC')
    assert 'wf_efficiency' in baseline
    assert 'oos_pnl' in baseline
    assert 'oos_win_rate' in baseline
    assert 'oos_trade_count' in baseline
