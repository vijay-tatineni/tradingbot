"""Tests for P&L display caching logic in bot/dashboard.py."""

import json
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock

import bot.dashboard as dashboard_mod


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path):
    """Redirect _PNL_CACHE_FILE to a temp file for each test."""
    cache_file = str(tmp_path / "pnl_cache.json")
    with patch.object(dashboard_mod, '_PNL_CACHE_FILE', cache_file):
        yield cache_file


def _make_cfg(web_dir):
    cfg = MagicMock()
    cfg.web_dir = web_dir
    cfg.check_interval_mins = 1
    cfg.portfolio_loss_limit = 5000
    cfg.active_instruments = []
    cfg.accum_instruments = []
    cfg.path = "test.yaml"
    return cfg


def _make_signal(symbol, pos, currency='USD', unreal_pnl=0, price=0, avg_cost=0):
    return {
        'symbol': symbol, 'pos': pos, 'currency': currency,
        'unreal_pnl': unreal_pnl, 'pnl_pct': 0, 'price': price,
        'avg_cost': avg_cost, 'flag': '', 'name': symbol,
        'market': 'OPEN', 'alligator': '--', 'direction': '--',
        'ma200': '--', 'wr': -50, 'rsi': 50, 'confidence': 'LOW',
        'signal': 'HOLD', 'action': '--', 'reason': '',
    }


def test_pnl_cache_saves_nonzero(tmp_path, tmp_cache):
    """When P&L has values, cache file is written."""
    cfg = _make_cfg(str(tmp_path))
    dash = dashboard_mod.Dashboard(cfg)

    signals = [_make_signal('SU', 10, 'EUR', unreal_pnl=750.0)]
    dash.update(1, signals, [], 750.0, False, False)

    assert os.path.exists(tmp_cache)
    with open(tmp_cache) as f:
        cached = json.load(f)
    assert cached["total_pnl"] == 750.0
    assert cached["pnl_by_currency"] == {"EUR": 750.0}


def test_pnl_cache_loads_on_zero(tmp_path, tmp_cache):
    """When broker returns all zeros but positions exist, cache is used."""
    cfg = _make_cfg(str(tmp_path))
    dash = dashboard_mod.Dashboard(cfg)

    # Cycle 1: live P&L — writes cache
    signals_live = [_make_signal('SU', 10, 'EUR', unreal_pnl=750.0)]
    dash.update(1, signals_live, [], 750.0, False, False)

    # Cycle 2: market closed, IBKR returns 0 P&L but position still exists
    signals_zero = [_make_signal('SU', 10, 'EUR', unreal_pnl=0)]
    dash.update(2, signals_zero, [], 0, False, False)

    data_path = os.path.join(str(tmp_path), 'data.json')
    with open(data_path) as f:
        data = json.load(f)
    assert data["pnl_by_currency"] == {"EUR": 750.0}
    assert data["total_pnl"] == 750.0


def test_pnl_cache_survives_restart(tmp_path, tmp_cache):
    """pnl_cache.json persists between bot restarts."""
    # Write a cache file as if a previous run saved it
    with open(tmp_cache, 'w') as f:
        json.dump({"total_pnl": 500.0, "pnl_by_currency": {"EUR": 500.0}}, f)

    # Simulate a fresh bot start with market closed (zero P&L, but position exists)
    cfg = _make_cfg(str(tmp_path))
    dash = dashboard_mod.Dashboard(cfg)

    signals_zero = [_make_signal('SU', 10, 'EUR', unreal_pnl=0)]
    dash.update(1, signals_zero, [], 0, False, False)

    data_path = os.path.join(str(tmp_path), 'data.json')
    with open(data_path) as f:
        data = json.load(f)
    assert data["pnl_by_currency"] == {"EUR": 500.0}
    assert data["total_pnl"] == 500.0


def test_pnl_filters_zero_currencies(tmp_path, tmp_cache):
    """pnl_by_currency should never contain 0 values."""
    cfg = _make_cfg(str(tmp_path))
    dash = dashboard_mod.Dashboard(cfg)

    signals = [
        _make_signal('SU', 10, 'EUR', unreal_pnl=750.0),
        _make_signal('AAPL', 0, 'USD', unreal_pnl=0),
    ]
    dash.update(1, signals, [], 750.0, False, True)

    data_path = os.path.join(str(tmp_path), 'data.json')
    with open(data_path) as f:
        data = json.load(f)
    for ccy, val in data["pnl_by_currency"].items():
        assert val != 0, f"Currency {ccy} has zero value in pnl_by_currency"


def test_pnl_no_positions_shows_zero(tmp_path, tmp_cache):
    """With no open positions, pnl_by_currency is empty and total_pnl is 0."""
    cfg = _make_cfg(str(tmp_path))
    dash = dashboard_mod.Dashboard(cfg)

    signals = [_make_signal('AAPL', 0, 'USD', unreal_pnl=0)]
    dash.update(1, signals, [], 0, False, True)

    data_path = os.path.join(str(tmp_path), 'data.json')
    with open(data_path) as f:
        data = json.load(f)
    assert data["pnl_by_currency"] == {}
    assert data["total_pnl"] == 0
