"""
tests/test_portfolio.py
Unit tests for bot/portfolio.py — P&L calculation logic with mock IBKR.
"""

import pytest
from unittest.mock import MagicMock
from bot.portfolio import Portfolio, PositionInfo


class MockContract:
    def __init__(self, symbol, currency='USD'):
        self.symbol = symbol
        self.currency = currency


class MockPosition:
    def __init__(self, symbol, qty, avg_cost, currency='USD'):
        self.contract = MockContract(symbol, currency)
        self.position = qty
        self.avgCost = avg_cost


class MockIBConn:
    """Wrapper that mimics the ib_conn.ib pattern used by Portfolio."""
    def __init__(self, positions=None):
        self.ib = MagicMock()
        self.ib.positions.return_value = positions or []


class MockConfig:
    account = 'TEST123'


@pytest.fixture
def cfg():
    return MockConfig()


# ── Long P&L tests ───────────────────────────────────────────

def test_long_pnl_positive(cfg):
    """qty=10, avg_cost=100, price=110 → unreal_pnl=100, pnl_pct=10.0"""
    ib_conn = MockIBConn([MockPosition('AAPL', 10, 100.0)])
    port = Portfolio(ib_conn, cfg)
    info = port.get_position_info('AAPL', current_price=110.0)
    assert info.qty == 10
    assert info.unreal_pnl == 100.0, f"Expected 100.0, got {info.unreal_pnl}"
    assert info.pnl_pct == 10.0, f"Expected 10.0%, got {info.pnl_pct}"


def test_long_pnl_negative(cfg):
    """qty=10, avg_cost=100, price=90 → unreal_pnl=-100, pnl_pct=-10.0"""
    ib_conn = MockIBConn([MockPosition('AAPL', 10, 100.0)])
    port = Portfolio(ib_conn, cfg)
    info = port.get_position_info('AAPL', current_price=90.0)
    assert info.unreal_pnl == -100.0, f"Expected -100.0, got {info.unreal_pnl}"
    assert info.pnl_pct == -10.0, f"Expected -10.0%, got {info.pnl_pct}"


# ── Short P&L tests ──────────────────────────────────────────

def test_short_pnl_positive(cfg):
    """qty=-10, avg_cost=100, price=90 → unreal_pnl=100, pnl_pct=10.0"""
    ib_conn = MockIBConn([MockPosition('TSLA', -10, 100.0)])
    port = Portfolio(ib_conn, cfg)
    info = port.get_position_info('TSLA', current_price=90.0)
    assert info.unreal_pnl == 100.0, f"Expected 100.0, got {info.unreal_pnl}"
    assert info.pnl_pct == 10.0, f"Expected 10.0%, got {info.pnl_pct}"


def test_short_pnl_negative(cfg):
    """qty=-10, avg_cost=100, price=110 → unreal_pnl=-100, pnl_pct=-10.0"""
    ib_conn = MockIBConn([MockPosition('TSLA', -10, 100.0)])
    port = Portfolio(ib_conn, cfg)
    info = port.get_position_info('TSLA', current_price=110.0)
    assert info.unreal_pnl == -100.0, f"Expected -100.0, got {info.unreal_pnl}"
    assert info.pnl_pct == -10.0, f"Expected -10.0%, got {info.pnl_pct}"


# ── No position test ─────────────────────────────────────────

def test_no_position_returns_zero(cfg):
    """Symbol not in positions → qty=0."""
    ib_conn = MockIBConn([MockPosition('AAPL', 10, 100.0)])
    port = Portfolio(ib_conn, cfg)
    info = port.get_position_info('MSFT', current_price=200.0)
    assert info.qty == 0, f"Expected qty=0, got {info.qty}"
    assert info.avg_cost == 0
    assert info.unreal_pnl == 0
