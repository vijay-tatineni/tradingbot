"""
tests/test_broker_abstraction.py
Test the broker abstraction layer works correctly.
"""

import pytest
from bot.brokers import create_broker
from bot.brokers.base import BaseBroker, BrokerPosition, FillResult, PositionInfo


# ── Factory tests ──────────────────────────────────────────────

def test_create_broker_ibkr():
    """create_broker('ibkr', config) should return IBKRBroker instance."""
    from unittest.mock import patch, MagicMock
    from bot.brokers.ibkr import IBKRBroker

    # Mock IBConnection to avoid real IBKR connection
    with patch('bot.brokers.ibkr.IBConnection') as MockConn, \
         patch('bot.brokers.ibkr.DataFeed'), \
         patch('bot.brokers.ibkr.OrderManager'), \
         patch('bot.brokers.ibkr.Portfolio'):
        MockConn.return_value = MagicMock()
        cfg = MagicMock()
        broker = create_broker('ibkr', cfg)
        assert isinstance(broker, IBKRBroker)
        assert isinstance(broker, BaseBroker)


def test_create_broker_unknown_raises():
    """create_broker('unknown', config) should raise ValueError."""
    with pytest.raises(ValueError, match="Unknown broker type"):
        create_broker('unknown', None)


# ── Interface compliance ───────────────────────────────────────

def test_ibkr_broker_implements_base():
    """IBKRBroker must implement all abstract methods from BaseBroker."""
    from bot.brokers.ibkr import IBKRBroker
    assert issubclass(IBKRBroker, BaseBroker)

    # Check all abstract methods are present
    abstract_methods = set()
    for cls in BaseBroker.__mro__:
        for name, val in vars(cls).items():
            if getattr(val, '__isabstractmethod__', False):
                abstract_methods.add(name)

    for method_name in abstract_methods:
        assert hasattr(IBKRBroker, method_name), \
            f"IBKRBroker missing abstract method: {method_name}"
        method = getattr(IBKRBroker, method_name)
        assert not getattr(method, '__isabstractmethod__', False), \
            f"IBKRBroker has not implemented: {method_name}"


# ── Dataclass tests ────────────────────────────────────────────

def test_broker_position_dataclass():
    """BrokerPosition should have: symbol, qty, avg_cost, currency."""
    pos = BrokerPosition(
        symbol='SHEL', qty=40, avg_cost=3450.0,
        currency='GBP', contract=None,
    )
    assert pos.symbol == 'SHEL'
    assert pos.qty == 40
    assert pos.avg_cost == 3450.0
    assert pos.currency == 'GBP'
    assert pos.contract is None


def test_fill_result_dataclass():
    """FillResult should have: success, fill_price, filled_qty."""
    result = FillResult(success=True, fill_price=150.25, filled_qty=10)
    assert result.success is True
    assert result.fill_price == 150.25
    assert result.filled_qty == 10
    assert bool(result) is True

    fail = FillResult(success=False)
    assert bool(fail) is False
    assert fail.fill_price == 0.0
    assert fail.filled_qty == 0.0


def test_position_info_dataclass():
    """PositionInfo should have required fields with defaults."""
    info = PositionInfo(
        symbol='MSFT', qty=10, avg_cost=400.0, currency='USD',
    )
    assert info.symbol == 'MSFT'
    assert info.price == 0.0
    assert info.unreal_pnl == 0.0
    assert info.pnl_pct == 0.0
