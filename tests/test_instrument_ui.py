"""
tests/test_instrument_ui.py — Test the /api/instruments/test and /api/instruments/optimise endpoints.
Validates result structure, verdicts, error handling, and optimise job lifecycle.
"""

import json
import os
import sqlite3
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

import pytest


@pytest.fixture
def setup_api(tmp_path):
    """Set up Flask test client with temp instruments.json and backtest.db."""
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
            {"symbol": "NODATA", "name": "No Data Corp", "sec_type": "STK",
             "exchange": "SMART", "currency": "USD", "qty": 10,
             "enabled": True, "trail_stop_pct": 2.0, "take_profit_pct": 8.0,
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
    # BARC: robust baseline with positive OOS P&L
    conn.execute(
        "INSERT INTO wf_results VALUES (NULL, '2026-03-25', 'BARC', '4hr', "
        "50000, 3.5, 0.72, 400, 33791, 2.85, 0.70, 373, 0.76, 5.0, 12.0, "
        "'stable', 'robust', 6, 3)"
    )
    # MSFT: robust baseline
    conn.execute(
        "INSERT INTO wf_results VALUES (NULL, '2026-03-25', 'MSFT', '4hr', "
        "10000, 2.5, 0.65, 200, 8097, 2.68, 0.68, 180, 0.55, 2.0, 8.0, "
        "'stable', 'robust', 6, 3)"
    )
    # SHEL: no_edge baseline with negative OOS P&L
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

    token = api_server.create_token("testuser")
    client = app.test_client()
    return client, token, config_file, db_path


def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _make_wf_result(oos_pnl=38000, wf_eff=0.82, win_rate=0.72, trade_count=350, pf=3.1):
    """Create a mock walk-forward result object."""
    return SimpleNamespace(
        wf_efficiency=wf_eff,
        oos_total_pnl=oos_pnl,
        oos_win_rate=win_rate,
        oos_trade_count=trade_count,
        oos_profit_factor=pf,
    )


def _make_bt_summary(total_pnl=36113, pf=4.34, trade_count=624, win_rate=0.71, max_drawdown=-2400):
    """Create a mock backtest simulation summary."""
    return SimpleNamespace(
        total_pnl=total_pnl,
        profit_factor=pf,
        trade_count=trade_count,
        win_rate=win_rate,
        max_drawdown=max_drawdown,
        win_count=int(trade_count * win_rate),
        loss_count=trade_count - int(trade_count * win_rate),
        avg_holding_bars=5,
        avg_win_pnl=100,
        avg_loss_pnl=-50,
    )


def _mock_bt_and_wf(wf_result, bt_summary=None):
    """Context manager that mocks both backtest + walk-forward for the test endpoint."""
    import pandas as pd
    if bt_summary is None:
        bt_summary = _make_bt_summary()
    return (
        patch('backtest.walk_forward.run_walk_forward', return_value=wf_result),
        patch('backtest.offline_signals.generate_signals', return_value=[]),
        patch('backtest.simulator.simulate_trades', return_value=[]),
        patch('backtest.simulator.summarise', return_value=bt_summary),
        patch('backtest.database.load_bars', return_value=pd.DataFrame({'close': [100, 101, 102]})),
        patch('backtest.database.get_connection'),
    )


# --- Test: robust instrument returns valid result ---

def test_test_api_returns_result_for_robust_instrument(setup_api):
    """POST /api/instruments/test for a robust instrument returns valid result with all required fields."""
    client, token, _, _ = setup_api

    mock_result = _make_wf_result(oos_pnl=38000, wf_eff=0.82)
    patches = _mock_bt_and_wf(mock_result)

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post('/api/instruments/test',
                        headers=auth_headers(token),
                        json={"symbol": "BARC", "params": {"trail_stop_pct": 5.0, "take_profit_pct": 12.0}})

    assert r.status_code == 200
    data = r.get_json()
    required_fields = ['symbol', 'backtest', 'walkforward', 'reality_discount_pct',
                       'baseline', 'improvement_pct', 'verdict']
    for field in required_fields:
        assert field in data, f"Missing required field: {field}"
    assert data['symbol'] == 'BARC'
    assert isinstance(data['walkforward']['wf_efficiency'], (int, float))
    assert isinstance(data['walkforward']['oos_pnl'], (int, float))


def test_test_api_returns_result_for_no_edge_instrument(setup_api):
    """POST /api/instruments/test for a no-edge instrument returns valid result with negative oos_pnl."""
    client, token, _, _ = setup_api

    mock_result = _make_wf_result(oos_pnl=-19000, wf_eff=0.84, win_rate=0.077, pf=0.6)
    bt_summary = _make_bt_summary(total_pnl=22189, pf=0.26, win_rate=0.08, trade_count=58)
    patches = _mock_bt_and_wf(mock_result, bt_summary)

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post('/api/instruments/test',
                        headers=auth_headers(token),
                        json={"symbol": "SHEL", "params": {"trail_stop_pct": 1.5, "take_profit_pct": 5.0}})

    assert r.status_code == 200
    data = r.get_json()
    assert data['walkforward']['oos_pnl'] < 0
    assert data['verdict'] in ('worse', 'similar')


def test_test_api_returns_error_for_missing_data(setup_api):
    """POST /api/instruments/test for a symbol with no OHLCV data returns a clear error, not a 500."""
    client, token, _, _ = setup_api

    with patch('backtest.database.load_bars') as mock_load, \
         patch('backtest.database.get_connection'):
        import pandas as pd
        mock_load.return_value = pd.DataFrame()  # empty = no data

        r = client.post('/api/instruments/test',
                        headers=auth_headers(token),
                        json={"symbol": "NODATA", "params": {}})

    assert r.status_code == 400
    data = r.get_json()
    assert 'error' in data
    assert 'NODATA' in data['error'] or 'data' in data['error'].lower()


def test_test_api_returns_error_for_invalid_params(setup_api):
    """POST /api/instruments/test with trail_stop_pct=-1 returns 400 with validation error."""
    client, token, _, _ = setup_api

    r = client.post('/api/instruments/test',
                    headers=auth_headers(token),
                    json={"symbol": "BARC", "params": {"trail_stop_pct": -1}})

    assert r.status_code == 400
    data = r.get_json()
    assert 'error' in data


def test_test_api_response_has_baseline(setup_api):
    """Response must include a 'baseline' object with the previous WF results for comparison."""
    client, token, _, _ = setup_api

    mock_result = _make_wf_result(oos_pnl=38000, wf_eff=0.82)
    patches = _mock_bt_and_wf(mock_result)

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post('/api/instruments/test',
                        headers=auth_headers(token),
                        json={"symbol": "BARC", "params": {"trail_stop_pct": 5.0}})

    assert r.status_code == 200
    data = r.get_json()
    assert 'baseline' in data
    assert data['baseline'] is not None
    assert 'oos_pnl' in data['baseline']
    assert 'wf_efficiency' in data['baseline']


def test_test_api_verdict_better_when_improved(setup_api):
    """When test OOS P&L is >5% better than baseline, verdict should be 'better'."""
    client, token, _, _ = setup_api

    # BARC baseline oos_pnl = 33791. We return 38000 which is ~12.5% better.
    mock_result = _make_wf_result(oos_pnl=38000)
    patches = _mock_bt_and_wf(mock_result)

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post('/api/instruments/test',
                        headers=auth_headers(token),
                        json={"symbol": "BARC", "params": {"trail_stop_pct": 5.0}})

    data = r.get_json()
    assert data['verdict'] == 'better'
    assert data['improvement_pct'] > 5


def test_test_api_verdict_worse_when_degraded(setup_api):
    """When test OOS P&L is >5% worse than baseline, verdict should be 'worse'."""
    client, token, _, _ = setup_api

    # BARC baseline oos_pnl = 33791. We return 20000 which is ~40% worse.
    mock_result = _make_wf_result(oos_pnl=20000)
    patches = _mock_bt_and_wf(mock_result)

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post('/api/instruments/test',
                        headers=auth_headers(token),
                        json={"symbol": "BARC", "params": {"trail_stop_pct": 5.0}})

    data = r.get_json()
    assert data['verdict'] == 'worse'
    assert data['improvement_pct'] < -5


def test_test_api_verdict_similar_when_close(setup_api):
    """When test OOS P&L is within 5% of baseline, verdict should be 'similar'."""
    client, token, _, _ = setup_api

    # BARC baseline oos_pnl = 33791. We return 34000 which is ~0.6% better.
    mock_result = _make_wf_result(oos_pnl=34000)
    patches = _mock_bt_and_wf(mock_result)

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post('/api/instruments/test',
                        headers=auth_headers(token),
                        json={"symbol": "BARC", "params": {"trail_stop_pct": 5.0}})

    data = r.get_json()
    assert data['verdict'] == 'similar'
    assert abs(data['improvement_pct']) <= 5


def test_optimise_api_returns_job_id(setup_api):
    """POST /api/instruments/optimise returns immediately with job_id, not blocking."""
    client, token, _, _ = setup_api

    # Mock the background thread to not actually run
    with patch('api_server.threading.Thread') as mock_thread:
        mock_thread.return_value = MagicMock()
        r = client.post('/api/instruments/optimise',
                        headers=auth_headers(token),
                        json={"symbol": "BARC"})

    assert r.status_code == 200
    data = r.get_json()
    assert 'job_id' in data
    assert data['job_id'].startswith('opt_BARC_')


def test_optimise_status_endpoint_returns_progress(setup_api):
    """GET /api/instruments/optimise/status returns progress percentage and phase description."""
    client, token, _, _ = setup_api
    import api_server

    # Manually insert a running job
    job_id = 'opt_BARC_test123'
    with api_server._optimise_lock:
        api_server._optimise_jobs[job_id] = {
            'symbol': 'BARC',
            'status': 'running',
            'progress': 42,
            'phase': 'Testing fold 3/5',
            'estimated_remaining_seconds': 120,
            'result': None,
            'error': None,
        }

    try:
        r = client.get(f'/api/instruments/optimise/status?job_id={job_id}',
                       headers=auth_headers(token))
        assert r.status_code == 200
        data = r.get_json()
        assert data['status'] == 'running'
        assert data['progress'] == 42
        assert 'phase' in data
        assert data['phase'] == 'Testing fold 3/5'
    finally:
        with api_server._optimise_lock:
            api_server._optimise_jobs.pop(job_id, None)


# --- Test button: backtest + WF combined ---

def test_test_api_returns_both_backtest_and_wf(setup_api):
    """POST /api/instruments/test should return both 'backtest' and 'walkforward' objects."""
    client, token, _, _ = setup_api
    mock_result = _make_wf_result(oos_pnl=33791, wf_eff=0.76, win_rate=0.70, trade_count=373, pf=3.31)
    bt_summary = _make_bt_summary(total_pnl=36113, pf=4.34, trade_count=624, win_rate=0.71)
    patches = _mock_bt_and_wf(mock_result, bt_summary)

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post('/api/instruments/test',
                        headers=auth_headers(token),
                        json={"symbol": "BARC", "params": {"trail_stop_pct": 5.0}})

    assert r.status_code == 200
    data = r.get_json()
    assert 'backtest' in data
    assert 'walkforward' in data


def test_test_api_backtest_has_required_fields(setup_api):
    """backtest object must have: total_pnl, profit_factor, trade_count, win_rate, max_drawdown."""
    client, token, _, _ = setup_api
    mock_result = _make_wf_result()
    patches = _mock_bt_and_wf(mock_result)

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post('/api/instruments/test',
                        headers=auth_headers(token),
                        json={"symbol": "BARC", "params": {"trail_stop_pct": 5.0}})

    bt = r.get_json()['backtest']
    for field in ['total_pnl', 'profit_factor', 'trade_count', 'win_rate', 'max_drawdown']:
        assert field in bt, f"Missing backtest field: {field}"


def test_test_api_walkforward_has_required_fields(setup_api):
    """walkforward object must have: oos_pnl, wf_efficiency, oos_profit_factor, oos_trade_count, oos_win_rate."""
    client, token, _, _ = setup_api
    mock_result = _make_wf_result()
    patches = _mock_bt_and_wf(mock_result)

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post('/api/instruments/test',
                        headers=auth_headers(token),
                        json={"symbol": "BARC", "params": {"trail_stop_pct": 5.0}})

    wf = r.get_json()['walkforward']
    for field in ['oos_pnl', 'wf_efficiency', 'oos_profit_factor', 'oos_trade_count', 'oos_win_rate']:
        assert field in wf, f"Missing walkforward field: {field}"


def test_test_api_reality_discount_positive(setup_api):
    """BT $36K, WF $34K → reality_discount_pct ≈ 6."""
    client, token, _, _ = setup_api
    mock_result = _make_wf_result(oos_pnl=33791)
    bt_summary = _make_bt_summary(total_pnl=36113)
    patches = _mock_bt_and_wf(mock_result, bt_summary)

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post('/api/instruments/test',
                        headers=auth_headers(token),
                        json={"symbol": "BARC", "params": {"trail_stop_pct": 5.0}})

    data = r.get_json()
    assert 'reality_discount_pct' in data
    assert 0 < data['reality_discount_pct'] < 30  # ~6.4%


def test_test_api_reality_discount_wf_negative(setup_api):
    """BT $22K, WF -$20K → reality_discount_pct > 100."""
    client, token, _, _ = setup_api
    mock_result = _make_wf_result(oos_pnl=-19846)
    bt_summary = _make_bt_summary(total_pnl=22189)
    patches = _mock_bt_and_wf(mock_result, bt_summary)

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post('/api/instruments/test',
                        headers=auth_headers(token),
                        json={"symbol": "BARC", "params": {"trail_stop_pct": 5.0}})

    data = r.get_json()
    assert data['reality_discount_pct'] > 100


def test_test_api_reality_discount_both_negative(setup_api):
    """BT -$5K, WF -$8K → handle gracefully, no crash."""
    client, token, _, _ = setup_api
    mock_result = _make_wf_result(oos_pnl=-8000)
    bt_summary = _make_bt_summary(total_pnl=-5000)
    patches = _mock_bt_and_wf(mock_result, bt_summary)

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post('/api/instruments/test',
                        headers=auth_headers(token),
                        json={"symbol": "BARC", "params": {"trail_stop_pct": 5.0}})

    assert r.status_code == 200
    data = r.get_json()
    assert 'reality_discount_pct' in data


def test_test_api_reality_discount_bt_zero(setup_api):
    """BT $0 → reality_discount_pct should be 0, not divide-by-zero."""
    client, token, _, _ = setup_api
    mock_result = _make_wf_result(oos_pnl=1000)
    bt_summary = _make_bt_summary(total_pnl=0)
    patches = _mock_bt_and_wf(mock_result, bt_summary)

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post('/api/instruments/test',
                        headers=auth_headers(token),
                        json={"symbol": "BARC", "params": {"trail_stop_pct": 5.0}})

    assert r.status_code == 200
    data = r.get_json()
    assert data['reality_discount_pct'] == 0


def test_test_api_backtest_trade_count_gte_wf(setup_api):
    """Backtest uses full dataset so bt trade_count should generally be >= wf trade_count."""
    client, token, _, _ = setup_api
    mock_result = _make_wf_result(oos_pnl=33791, trade_count=373)
    bt_summary = _make_bt_summary(total_pnl=36113, trade_count=624)
    patches = _mock_bt_and_wf(mock_result, bt_summary)

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post('/api/instruments/test',
                        headers=auth_headers(token),
                        json={"symbol": "BARC", "params": {"trail_stop_pct": 5.0}})

    data = r.get_json()
    assert data['backtest']['trade_count'] >= data['walkforward']['oos_trade_count']


def test_test_api_backtest_includes_max_drawdown(setup_api):
    """Backtest result must include max_drawdown field."""
    client, token, _, _ = setup_api
    mock_result = _make_wf_result()
    bt_summary = _make_bt_summary(max_drawdown=-2400)
    patches = _mock_bt_and_wf(mock_result, bt_summary)

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post('/api/instruments/test',
                        headers=auth_headers(token),
                        json={"symbol": "BARC", "params": {"trail_stop_pct": 5.0}})

    data = r.get_json()
    assert 'max_drawdown' in data['backtest']
    assert data['backtest']['max_drawdown'] == -2400


def test_test_api_no_apply_when_wf_negative(setup_api):
    """When walkforward.oos_pnl is negative, verdict logic should not suggest apply."""
    client, token, _, _ = setup_api
    mock_result = _make_wf_result(oos_pnl=-19846)
    bt_summary = _make_bt_summary(total_pnl=22189)
    patches = _mock_bt_and_wf(mock_result, bt_summary)

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        r = client.post('/api/instruments/test',
                        headers=auth_headers(token),
                        json={"symbol": "BARC", "params": {"trail_stop_pct": 5.0}})

    data = r.get_json()
    # When WF is negative, verdict should be worse (not "better")
    assert data['walkforward']['oos_pnl'] < 0
    assert data['verdict'] != 'better'


# --- Optimise speed ---

def test_optimise_phase1_uses_simple_backtest():
    """Phase 1 should use simulate_trades on full dataset, NOT run_walk_forward."""
    from backtest.grid_search import indicator_grid_search
    import inspect
    source = inspect.getsource(indicator_grid_search)
    # Phase 1 should NOT import/call run_walk_forward
    assert 'run_walk_forward' not in source
    # Phase 1 SHOULD use generate_signals and simulate_trades
    assert 'generate_signals' in source
    assert 'simulate_trades' in source


def test_optimise_phase1_returns_top_5():
    """Phase 1 should return exactly 5 indicator combos ranked by profit factor."""
    from backtest.grid_search import indicator_grid_search
    import pandas as pd

    df = pd.DataFrame({'open': [100]*50, 'high': [101]*50, 'low': [99]*50,
                        'close': [100]*50, 'volume': [1000]*50})

    # Mock generate_signals and simulate_trades to return predictable results
    call_count = [0]
    def mock_summarise(trades):
        call_count[0] += 1
        return SimpleNamespace(
            total_pnl=call_count[0] * 100, profit_factor=1.0 + call_count[0] * 0.1,
            trade_count=50, win_rate=0.6, max_drawdown=-100,
        )

    with patch('backtest.grid_search.generate_signals', return_value=[]), \
         patch('backtest.grid_search.simulate_trades', return_value=[]), \
         patch('backtest.grid_search.summarise', side_effect=mock_summarise):
        results = indicator_grid_search(
            symbol='TEST', df=df, current_stop=2.0, current_tp=8.0,
            base_indicator_settings={}, instrument_config={'qty': 1},
        )

    assert len(results) == 5


def test_optimise_phase2_uses_walkforward():
    """Phase 2 should use run_walk_forward for each of the top 5 combos × stop/TP grid."""
    from backtest.grid_search import full_optimise
    import inspect
    source = inspect.getsource(full_optimise)
    assert 'run_walk_forward' in source


def test_optimise_reduced_grid_144_combos():
    """The indicator grid should produce exactly 144 combinations (3×2×2×2×3×2)."""
    from backtest.grid_search import _generate_indicator_combos
    from backtest.config import INDICATOR_GRID
    combos = _generate_indicator_combos(INDICATOR_GRID)
    assert len(combos) == 144


# --- Progress bar ---

def test_optimise_status_includes_progress(setup_api):
    """GET /api/instruments/optimise/status should return progress (0-100), phase, and status."""
    client, token, _, _ = setup_api
    import api_server

    job_id = 'opt_TEST_progress'
    with api_server._optimise_lock:
        api_server._optimise_jobs[job_id] = {
            'symbol': 'BARC', 'status': 'running', 'progress': 55,
            'phase': 'Phase 1: testing indicator combo 80/144',
            'estimated_remaining_seconds': 30, 'result': None, 'error': None,
        }

    try:
        r = client.get(f'/api/instruments/optimise/status?job_id={job_id}',
                       headers=auth_headers(token))
        data = r.get_json()
        assert 0 <= data['progress'] <= 100
        assert 'phase' in data
        assert 'status' in data
    finally:
        with api_server._optimise_lock:
            api_server._optimise_jobs.pop(job_id, None)


def test_optimise_status_phase1_label(setup_api):
    """During Phase 1, phase should say 'Phase 1: testing indicator combo X/144'."""
    client, token, _, _ = setup_api
    import api_server

    job_id = 'opt_TEST_ph1'
    with api_server._optimise_lock:
        api_server._optimise_jobs[job_id] = {
            'symbol': 'BARC', 'status': 'running', 'progress': 31,
            'phase': 'Phase 1: testing indicator combo 45/144',
            'estimated_remaining_seconds': 60, 'result': None, 'error': None,
        }

    try:
        r = client.get(f'/api/instruments/optimise/status?job_id={job_id}',
                       headers=auth_headers(token))
        data = r.get_json()
        assert data['phase'].startswith('Phase 1:')
        assert 'indicator combo' in data['phase']
    finally:
        with api_server._optimise_lock:
            api_server._optimise_jobs.pop(job_id, None)


def test_optimise_status_phase2_label(setup_api):
    """During Phase 2, phase should say 'Phase 2: walk-forward testing top combos X/320'."""
    client, token, _, _ = setup_api
    import api_server

    job_id = 'opt_TEST_ph2'
    with api_server._optimise_lock:
        api_server._optimise_jobs[job_id] = {
            'symbol': 'BARC', 'status': 'running', 'progress': 27,
            'phase': 'Phase 2: walk-forward testing top combos 85/320',
            'estimated_remaining_seconds': 180, 'result': None, 'error': None,
        }

    try:
        r = client.get(f'/api/instruments/optimise/status?job_id={job_id}',
                       headers=auth_headers(token))
        data = r.get_json()
        assert data['phase'].startswith('Phase 2:')
        assert 'walk-forward' in data['phase']
    finally:
        with api_server._optimise_lock:
            api_server._optimise_jobs.pop(job_id, None)
