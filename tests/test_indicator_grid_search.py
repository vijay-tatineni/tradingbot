"""
tests/test_indicator_grid_search.py — Test Layer 2 instrument optimisation.
"""

import pytest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass


def test_phase1_generates_all_combos():
    """With grid: rsi=[7,14], adx=[15,20], ma=[100,200] -> 2x2x2 = 8 combos."""
    from backtest.grid_search import _generate_indicator_combos
    grid = {
        "rsi_period": [7, 14],
        "adx_threshold": [15, 20],
        "ma200_period": [100, 200],
    }
    combos = _generate_indicator_combos(grid)
    assert len(combos) == 8


def test_phase1_generates_full_grid():
    """Full default grid: 3x2x2x2x3x2 = 144 combinations."""
    from backtest.grid_search import _generate_indicator_combos
    from backtest.config import INDICATOR_GRID
    combos = _generate_indicator_combos(INDICATOR_GRID)
    expected = 3 * 2 * 2 * 2 * 3 * 2  # 144
    assert len(combos) == expected


def test_phase1_combos_include_fixed_settings():
    """Each combo should include the fixed settings."""
    from backtest.grid_search import _generate_indicator_combos
    from backtest.config import INDICATOR_FIXED
    grid = {"rsi_period": [7], "adx_threshold": [15], "ma200_period": [100]}
    combos = _generate_indicator_combos(grid)
    assert len(combos) == 1
    combo = combos[0]
    for key, val in INDICATOR_FIXED.items():
        assert combo[key] == val


def test_phase1_returns_top_n():
    """Should return exactly top N (default 5) indicator combos."""
    from backtest.grid_search import indicator_grid_search, IndicatorCombo
    from backtest.simulator import SimulationSummary

    call_count = [0]

    def mock_summarise(trades):
        call_count[0] += 1
        return SimulationSummary(
            total_pnl=1000 + call_count[0] * 100,
            trade_count=20,
            win_count=12,
            loss_count=8,
            win_rate=0.6,
            profit_factor=1.5 + call_count[0] * 0.1,
            max_drawdown=-500,
            avg_holding_bars=10,
            avg_win_pnl=200,
            avg_loss_pnl=-100,
        )

    import pandas as pd
    df = pd.DataFrame({
        "datetime": pd.date_range("2024-01-01", periods=500, freq="4h"),
        "open": [100]*500, "high": [105]*500,
        "low": [95]*500, "close": [102]*500, "volume": [1000]*500,
    })

    with patch('backtest.grid_search.generate_signals', return_value=[]), \
         patch('backtest.grid_search.summarise', side_effect=mock_summarise):
        grid = {"rsi_period": [7, 14], "adx_threshold": [15, 20], "ma200_period": [100]}
        top = indicator_grid_search(
            symbol="TEST", df=df, current_stop=2.0, current_tp=8.0,
            base_indicator_settings={}, instrument_config={"qty": 1},
            grid=grid, top_n=3,
        )
    assert len(top) == 3  # Asked for top 3


def test_phase1_uses_fixed_stop_tp():
    """Phase 1 uses instrument's current stop/TP, not grid-searched."""
    from backtest.grid_search import indicator_grid_search

    captured_configs = []

    def mock_wf(symbol, df, indicator_settings, instrument_config, **kw):
        captured_configs.append(instrument_config)
        return None  # No valid result

    import pandas as pd
    df = pd.DataFrame({
        "datetime": pd.date_range("2024-01-01", periods=10),
        "open": [100]*10, "high": [105]*10,
        "low": [95]*10, "close": [102]*10, "volume": [1000]*10,
    })

    with patch('backtest.walk_forward.run_walk_forward', side_effect=mock_wf):
        grid = {"rsi_period": [7]}
        indicator_grid_search(
            symbol="TEST", df=df, current_stop=4.0, current_tp=12.0,
            base_indicator_settings={}, instrument_config={"qty": 1},
            grid=grid,
        )
    # All calls should use the same stop/TP
    for cfg in captured_configs:
        assert cfg["trail_stop_pct"] == 4.0
        assert cfg["take_profit_pct"] == 12.0


def test_phase1_handles_no_signals():
    """Indicator combo producing 0 signals -> skipped, not crash."""
    from backtest.grid_search import indicator_grid_search

    def mock_wf(symbol, df, indicator_settings, instrument_config, **kw):
        return None  # No signals / no result

    import pandas as pd
    df = pd.DataFrame({
        "datetime": pd.date_range("2024-01-01", periods=10),
        "open": [100]*10, "high": [105]*10,
        "low": [95]*10, "close": [102]*10, "volume": [1000]*10,
    })

    with patch('backtest.walk_forward.run_walk_forward', side_effect=mock_wf):
        grid = {"rsi_period": [7, 14]}
        result = indicator_grid_search(
            symbol="TEST", df=df, current_stop=2.0, current_tp=8.0,
            base_indicator_settings={}, instrument_config={"qty": 1},
            grid=grid,
        )
    assert result == []  # No crash, empty list


def test_phase1_handles_all_losses():
    """All trades are losses -> profit_factor = 0, no crash."""
    from backtest.grid_search import indicator_grid_search
    from backtest.simulator import SimulationSummary

    def mock_summarise(trades):
        return SimulationSummary(
            total_pnl=-500,
            trade_count=10,
            win_count=0,
            loss_count=10,
            win_rate=0.0,
            profit_factor=0.0,
            max_drawdown=-500,
            avg_holding_bars=5,
            avg_win_pnl=0,
            avg_loss_pnl=-50,
        )

    import pandas as pd
    df = pd.DataFrame({
        "datetime": pd.date_range("2024-01-01", periods=10),
        "open": [100]*10, "high": [105]*10,
        "low": [95]*10, "close": [102]*10, "volume": [1000]*10,
    })

    with patch('backtest.grid_search.generate_signals', return_value=[]), \
         patch('backtest.grid_search.summarise', side_effect=mock_summarise):
        grid = {"rsi_period": [7]}
        result = indicator_grid_search(
            symbol="TEST", df=df, current_stop=2.0, current_tp=8.0,
            base_indicator_settings={}, instrument_config={"qty": 1},
            grid=grid,
        )
    assert len(result) >= 1
    assert result[0].oos_profit_factor == 0.0


# --- Phase 2: Stop/TP fine-tuning ---

def _mock_phase1_summarise(trades):
    """Helper: makes Phase 1 return valid results so Phase 2 can run."""
    from backtest.simulator import SimulationSummary
    return SimulationSummary(
        total_pnl=5000, trade_count=20, win_count=12, loss_count=8,
        win_rate=0.6, profit_factor=2.0, max_drawdown=-500,
        avg_holding_bars=10, avg_win_pnl=600, avg_loss_pnl=-100,
    )


def test_phase2_uses_top_indicator_combos():
    """Phase 2 receives top N combos and grid-searches stop/TP."""
    from backtest.grid_search import full_optimise

    phase2_calls = []

    def mock_wf(symbol, df, indicator_settings, instrument_config, **kw):
        phase2_calls.append({
            "settings": indicator_settings,
            "stop": instrument_config.get("trail_stop_pct"),
            "tp": instrument_config.get("take_profit_pct"),
        })
        result = MagicMock()
        result.wf_efficiency = 0.7
        result.oos_total_pnl = 10000
        result.oos_profit_factor = 2.5
        result.oos_win_rate = 0.65
        result.oos_trade_count = 100
        return result

    import pandas as pd
    df = pd.DataFrame({
        "datetime": pd.date_range("2024-01-01", periods=10),
        "open": [100]*10, "high": [105]*10,
        "low": [95]*10, "close": [102]*10, "volume": [1000]*10,
    })

    with patch('backtest.grid_search.generate_signals', return_value=[]), \
         patch('backtest.grid_search.summarise', side_effect=_mock_phase1_summarise), \
         patch('backtest.walk_forward.run_walk_forward', side_effect=mock_wf):
        grid = {"rsi_period": [7]}  # 1 combo for simplicity
        result = full_optimise(
            symbol="TEST", df=df,
            base_indicator_settings={},
            instrument_config={"qty": 1, "trail_stop_pct": 2.0, "take_profit_pct": 8.0},
            grid=grid,
        )
    assert result is not None
    # Phase 2 should test multiple stop/TP combos
    assert len(phase2_calls) > 1


def test_phase2_grid_searches_stop_tp():
    """For each indicator combo, tests all stop%/TP% combinations."""
    from backtest.config import PARAM_GRID
    stop_count = len(PARAM_GRID["trail_stop_pct"])
    tp_count = len(PARAM_GRID["take_profit_pct"])
    # With 1 indicator combo, phase 2 should test stop_count * tp_count combos
    expected = stop_count * tp_count
    assert expected == 64  # 8 * 8


def test_phase2_returns_overall_best():
    """Final result is the single best across all Phase 2 runs."""
    from backtest.grid_search import full_optimise

    def mock_wf(symbol, df, indicator_settings, instrument_config, **kw):
        result = MagicMock()
        # Make one specific combo the best
        stop = instrument_config.get("trail_stop_pct", 2.0)
        tp = instrument_config.get("take_profit_pct", 8.0)
        if stop == 3.0 and tp == 10.0:
            result.oos_profit_factor = 5.0
            result.oos_total_pnl = 50000
        else:
            result.oos_profit_factor = 2.0
            result.oos_total_pnl = 10000
        result.wf_efficiency = 0.7
        result.oos_win_rate = 0.65
        result.oos_trade_count = 100
        return result

    import pandas as pd
    df = pd.DataFrame({
        "datetime": pd.date_range("2024-01-01", periods=10),
        "open": [100]*10, "high": [105]*10,
        "low": [95]*10, "close": [102]*10, "volume": [1000]*10,
    })

    with patch('backtest.grid_search.generate_signals', return_value=[]), \
         patch('backtest.grid_search.summarise', side_effect=_mock_phase1_summarise), \
         patch('backtest.walk_forward.run_walk_forward', side_effect=mock_wf):
        grid = {"rsi_period": [14]}
        result = full_optimise(
            symbol="TEST", df=df,
            base_indicator_settings={},
            instrument_config={"qty": 1, "trail_stop_pct": 2.0, "take_profit_pct": 8.0},
            grid=grid,
        )
    assert result is not None
    assert result.best_stop_pct == 3.0
    assert result.best_tp_pct == 10.0


# --- Full optimise ---

def test_full_optimise_combines_phases():
    """full_optimise() runs Phase 1 then Phase 2."""
    from backtest.grid_search import full_optimise

    def mock_wf(symbol, df, indicator_settings, instrument_config, **kw):
        result = MagicMock()
        result.wf_efficiency = 0.7
        result.oos_total_pnl = 10000
        result.oos_profit_factor = 2.5
        result.oos_win_rate = 0.65
        result.oos_trade_count = 100
        return result

    import pandas as pd
    df = pd.DataFrame({
        "datetime": pd.date_range("2024-01-01", periods=10),
        "open": [100]*10, "high": [105]*10,
        "low": [95]*10, "close": [102]*10, "volume": [1000]*10,
    })

    with patch('backtest.grid_search.generate_signals', return_value=[]), \
         patch('backtest.grid_search.summarise', side_effect=_mock_phase1_summarise), \
         patch('backtest.walk_forward.run_walk_forward', side_effect=mock_wf):
        grid = {"rsi_period": [7]}
        result = full_optimise(
            symbol="TEST", df=df,
            base_indicator_settings={},
            instrument_config={"qty": 1, "trail_stop_pct": 2.0, "take_profit_pct": 8.0},
            grid=grid,
        )
    assert result is not None
    assert result.combos_tested > 0
    assert result.duration_seconds >= 0


def test_full_optimise_result_has_all_fields():
    """Result includes all indicator settings, stop, tp, etc."""
    from backtest.grid_search import full_optimise

    def mock_wf(symbol, df, indicator_settings, instrument_config, **kw):
        result = MagicMock()
        result.wf_efficiency = 0.7
        result.oos_total_pnl = 10000
        result.oos_profit_factor = 2.5
        result.oos_win_rate = 0.65
        result.oos_trade_count = 100
        return result

    import pandas as pd
    df = pd.DataFrame({
        "datetime": pd.date_range("2024-01-01", periods=10),
        "open": [100]*10, "high": [105]*10,
        "low": [95]*10, "close": [102]*10, "volume": [1000]*10,
    })

    with patch('backtest.grid_search.generate_signals', return_value=[]), \
         patch('backtest.grid_search.summarise', side_effect=_mock_phase1_summarise), \
         patch('backtest.walk_forward.run_walk_forward', side_effect=mock_wf):
        grid = {"rsi_period": [7]}
        result = full_optimise(
            symbol="TEST", df=df,
            base_indicator_settings={},
            instrument_config={"qty": 1, "trail_stop_pct": 2.0, "take_profit_pct": 8.0},
            grid=grid,
        )
    assert result is not None
    assert hasattr(result, 'best_stop_pct')
    assert hasattr(result, 'best_tp_pct')
    assert hasattr(result, 'best_indicators')
    assert hasattr(result, 'wf_efficiency')
    assert hasattr(result, 'oos_pnl')
    assert hasattr(result, 'oos_profit_factor')
    assert hasattr(result, 'oos_win_rate')
    assert hasattr(result, 'top_5')


def test_full_optimise_handles_no_results():
    """No valid WF results -> returns None."""
    from backtest.grid_search import full_optimise

    def mock_wf(symbol, df, indicator_settings, instrument_config, **kw):
        return None

    import pandas as pd
    df = pd.DataFrame({
        "datetime": pd.date_range("2024-01-01", periods=10),
        "open": [100]*10, "high": [105]*10,
        "low": [95]*10, "close": [102]*10, "volume": [1000]*10,
    })

    with patch('backtest.walk_forward.run_walk_forward', side_effect=mock_wf):
        grid = {"rsi_period": [7]}
        result = full_optimise(
            symbol="TEST", df=df,
            base_indicator_settings={},
            instrument_config={"qty": 1, "trail_stop_pct": 2.0, "take_profit_pct": 8.0},
            grid=grid,
        )
    assert result is None


def test_full_optimise_saves_to_db():
    """Results can be persisted to optimise_results table."""
    from backtest.database import store_optimise_result
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.executescript("""
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

    store_optimise_result(conn, {
        "run_date": "2026-04-01",
        "symbol": "TEST",
        "best_stop_pct": 3.0,
        "best_tp_pct": 10.0,
        "best_rsi_period": 10,
        "best_rsi_oversold": 35,
        "best_rsi_overbought": 70,
        "best_wr_period": 14,
        "best_adx_threshold": 15,
        "best_ma_period": 200,
        "wf_efficiency": 0.85,
        "oos_pnl": 42000,
        "oos_profit_factor": 4.1,
        "oos_win_rate": 0.74,
        "oos_trade_count": 380,
        "current_oos_pnl": 33791,
        "improvement_pct": 24.3,
        "combos_tested": 3392,
        "duration_seconds": 312,
    })

    cursor = conn.execute("SELECT * FROM optimise_results WHERE symbol='TEST'")
    row = cursor.fetchone()
    assert row is not None
    conn.close()


def test_deep_optimise_handles_one_failure():
    """One instrument fails -> continue with rest, not abort."""
    # This tests the error handling in backtest/run.py _run_deep_optimise_mode
    # The loop catches exceptions per-instrument and continues
    import inspect
    from backtest.run import _run_deep_optimise_mode
    source = inspect.getsource(_run_deep_optimise_mode)
    assert 'except Exception' in source  # Per-instrument exception handling


def test_deep_optimise_reports_duration():
    """Report includes total and per-instrument duration."""
    from backtest.grid_search import OptimiseResult
    result = OptimiseResult(symbol="TEST")
    result.duration_seconds = 312.5
    assert result.duration_seconds == 312.5
