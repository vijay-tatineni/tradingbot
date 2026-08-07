"""Tests for P&L display caching logic in bot/dashboard.py."""

import json
import os
import sqlite3
import pytest
from unittest.mock import patch, MagicMock

import bot.dashboard as dashboard_mod


@pytest.fixture(autouse=True)
def tmp_db(tmp_path):
    """Redirect _PNL_DB to a temp SQLite database for each test."""
    db_path = str(tmp_path / "positions.db")
    with patch.object(dashboard_mod, '_PNL_DB', db_path):
        yield db_path


def _make_cfg(web_dir):
    cfg = MagicMock()
    cfg.web_dir = web_dir
    cfg.check_interval_mins = 1
    cfg.portfolio_loss_limit = 5000
    cfg.active_instruments = []
    cfg.accum_instruments = []
    cfg.path = "test.yaml"
    cfg.broker_type = "ibkr"
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


def _read_pnl_cache(db_path):
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT currency, pnl_value FROM pnl_cache").fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def _seed_pnl_cache(db_path, pnl_by_ccy):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS pnl_cache (
        currency TEXT PRIMARY KEY,
        pnl_value REAL NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    for ccy, val in pnl_by_ccy.items():
        conn.execute(
            "INSERT OR REPLACE INTO pnl_cache (currency, pnl_value, updated_at) VALUES (?, ?, ?)",
            (ccy, val, "2026-01-01T00:00:00"))
    conn.commit()
    conn.close()


def test_pnl_cache_saves_nonzero(tmp_path, tmp_db):
    """When P&L has values, cache rows are written to SQLite."""
    cfg = _make_cfg(str(tmp_path))
    dash = dashboard_mod.Dashboard(cfg)

    signals = [_make_signal('SU', 10, 'EUR', unreal_pnl=750.0)]
    dash.update(1, signals, [], 750.0, False, False)

    cached = _read_pnl_cache(tmp_db)
    assert cached == {"EUR": 750.0}


def test_pnl_cache_loads_on_zero(tmp_path, tmp_db):
    """When broker returns all zeros but positions exist, cache is used."""
    cfg = _make_cfg(str(tmp_path))
    dash = dashboard_mod.Dashboard(cfg)

    signals_live = [_make_signal('SU', 10, 'EUR', unreal_pnl=750.0)]
    dash.update(1, signals_live, [], 750.0, False, False)

    signals_zero = [_make_signal('SU', 10, 'EUR', unreal_pnl=0)]
    dash.update(2, signals_zero, [], 0, False, False)

    data_path = os.path.join(str(tmp_path), 'data.json')
    with open(data_path) as f:
        data = json.load(f)
    assert data["pnl_by_currency"] == {"EUR": 750.0}
    assert data["total_pnl"] == 750.0


def test_pnl_cache_survives_restart(tmp_path, tmp_db):
    """SQLite pnl_cache persists between bot restarts."""
    _seed_pnl_cache(tmp_db, {"EUR": 500.0})

    cfg = _make_cfg(str(tmp_path))
    dash = dashboard_mod.Dashboard(cfg)

    signals_zero = [_make_signal('SU', 10, 'EUR', unreal_pnl=0)]
    dash.update(1, signals_zero, [], 0, False, False)

    data_path = os.path.join(str(tmp_path), 'data.json')
    with open(data_path) as f:
        data = json.load(f)
    assert data["pnl_by_currency"] == {"EUR": 500.0}
    assert data["total_pnl"] == 500.0


def test_pnl_filters_zero_currencies(tmp_path, tmp_db):
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


def test_pnl_no_positions_shows_zero(tmp_path, tmp_db):
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


def _read_data(tmp_path):
    with open(os.path.join(str(tmp_path), 'data.json')) as f:
        return json.load(f)


def test_live_breakeven_currency_shows_zero(tmp_path, tmp_db):
    """Open position with a LIVE price at break-even → currency shows $0,
    so the operator sees the positions exist (the AVGO/NBIS-during-session case)."""
    cfg = _make_cfg(str(tmp_path))
    dash = dashboard_mod.Dashboard(cfg)

    # Live price present (price>0, avg_cost>0) and equal → genuine break-even.
    signals = [_make_signal('AVGO', 2, 'USD', unreal_pnl=0, price=440.0, avg_cost=440.0)]
    dash.update(1, signals, [], 0, False, True)

    data = _read_data(tmp_path)
    assert data["pnl_by_currency"] == {"USD": 0}
    assert data["total_pnl"] == 0


def test_no_usd_positions_currency_absent(tmp_path, tmp_db):
    """No USD positions at all → USD never appears (not even $0)."""
    cfg = _make_cfg(str(tmp_path))
    dash = dashboard_mod.Dashboard(cfg)

    signals = [_make_signal('SU', 10, 'EUR', unreal_pnl=750.0, price=100.0, avg_cost=25.0)]
    dash.update(1, signals, [], 750.0, False, False)

    data = _read_data(tmp_path)
    assert "USD" not in data["pnl_by_currency"]
    assert data["pnl_by_currency"] == {"EUR": 750.0}


def test_live_position_with_profit_unchanged(tmp_path, tmp_db):
    """Open position with profit → currency shows the profit (unchanged)."""
    cfg = _make_cfg(str(tmp_path))
    dash = dashboard_mod.Dashboard(cfg)

    signals = [_make_signal('AVGO', 2, 'USD', unreal_pnl=37.38, price=458.09, avg_cost=439.4)]
    dash.update(1, signals, [], 37.38, False, True)

    data = _read_data(tmp_path)
    assert data["pnl_by_currency"] == {"USD": 37.38}
    assert data["total_pnl"] == 37.38


def test_market_closed_zero_does_not_fabricate(tmp_path, tmp_db):
    """Open position but NO live price (market closed, price=0) → do NOT show a
    misleading $0; edf283b cache-preserve still governs. Guards against the
    regression my first attempt at this fix introduced."""
    cfg = _make_cfg(str(tmp_path))
    dash = dashboard_mod.Dashboard(cfg)

    # Seed a last-known USD value, then a cycle with the market closed (price=0).
    _seed_pnl_cache(tmp_db, {"USD": 120.0})
    signals = [_make_signal('AVGO', 2, 'USD', unreal_pnl=0, price=0, avg_cost=440.0)]
    dash.update(1, signals, [], 0, False, False)

    data = _read_data(tmp_path)
    # Last-known value preserved, NOT overwritten with $0.
    assert data["pnl_by_currency"] == {"USD": 120.0}
