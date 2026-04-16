"""Tests for bot/order_validator.py — pre-order validation gate."""

import logging
import pytest
from bot.order_validator import validate_order, OrderValidationError


def _default_settings(**overrides):
    s = {
        "max_qty_per_order": 500,
        "max_notional_per_order": 5000,
        "max_open_positions": 5,
        "daily_loss_limit": 500,
        "weekly_loss_limit": 1500,
    }
    s.update(overrides)
    return s


def test_valid_order_passes():
    """Normal order passes all checks."""
    validate_order(
        symbol="AAPL", qty=10, price=150.0, direction="BUY",
        currency="USD", settings=_default_settings(),
        open_positions=2, daily_pnl=0.0, weekly_pnl=0.0,
    )


def test_qty_zero_rejected():
    """qty=0 is rejected."""
    with pytest.raises(OrderValidationError, match="qty must be positive"):
        validate_order(
            symbol="AAPL", qty=0, price=150.0, direction="BUY",
            currency="USD", settings=_default_settings(),
            open_positions=0, daily_pnl=0.0, weekly_pnl=0.0,
        )


def test_qty_exceeds_max_rejected():
    """qty=600 with max_qty=500 is rejected."""
    with pytest.raises(OrderValidationError, match="exceeds max_qty"):
        validate_order(
            symbol="AAPL", qty=600, price=10.0, direction="BUY",
            currency="USD", settings=_default_settings(),
            open_positions=0, daily_pnl=0.0, weekly_pnl=0.0,
        )


def test_notional_exceeds_max_rejected():
    """qty=100 at $200 = $20K exceeds $5K max."""
    with pytest.raises(OrderValidationError, match="notional"):
        validate_order(
            symbol="AAPL", qty=100, price=200.0, direction="BUY",
            currency="USD", settings=_default_settings(),
            open_positions=0, daily_pnl=0.0, weekly_pnl=0.0,
        )


def test_notional_gbp_pence_converted():
    """GBP pence: qty=400 at 432p = 1,728 (not 172,800)."""
    # 400 * (432/100) = 1728 — should pass the 5000 limit
    validate_order(
        symbol="BARC", qty=400, price=432.0, direction="BUY",
        currency="GBP", settings=_default_settings(),
        open_positions=0, daily_pnl=0.0, weekly_pnl=0.0,
    )


def test_notional_gbp_pence_exceeds_rejected():
    """GBP pence: qty=1000 at 600p = 6,000 exceeds 5,000 max."""
    with pytest.raises(OrderValidationError, match="notional"):
        validate_order(
            symbol="BARC", qty=1000, price=600.0, direction="BUY",
            currency="GBP", settings=_default_settings(),
            open_positions=0, daily_pnl=0.0, weekly_pnl=0.0,
        )


def test_price_zero_rejected():
    """price=0 is rejected."""
    with pytest.raises(OrderValidationError, match="price must be positive"):
        validate_order(
            symbol="AAPL", qty=10, price=0.0, direction="BUY",
            currency="USD", settings=_default_settings(),
            open_positions=0, daily_pnl=0.0, weekly_pnl=0.0,
        )


def test_max_positions_reached_rejected():
    """5 open positions, max is 5 -> rejected."""
    with pytest.raises(OrderValidationError, match="max positions"):
        validate_order(
            symbol="AAPL", qty=10, price=150.0, direction="BUY",
            currency="USD", settings=_default_settings(),
            open_positions=5, daily_pnl=0.0, weekly_pnl=0.0,
        )


def test_daily_loss_limit_rejected():
    """Daily P&L of -$500 with limit $500 -> rejected."""
    with pytest.raises(OrderValidationError, match="daily loss limit"):
        validate_order(
            symbol="AAPL", qty=10, price=150.0, direction="BUY",
            currency="USD", settings=_default_settings(),
            open_positions=0, daily_pnl=-500.0, weekly_pnl=0.0,
        )


def test_weekly_loss_limit_rejected():
    """Weekly P&L of -$1500 with limit $1500 -> rejected."""
    with pytest.raises(OrderValidationError, match="weekly loss limit"):
        validate_order(
            symbol="AAPL", qty=10, price=150.0, direction="BUY",
            currency="USD", settings=_default_settings(),
            open_positions=0, daily_pnl=0.0, weekly_pnl=-1500.0,
        )


def test_multiple_errors_all_reported():
    """If multiple checks fail, all errors are in the message."""
    with pytest.raises(OrderValidationError) as exc_info:
        validate_order(
            symbol="AAPL", qty=0, price=0.0, direction="BUY",
            currency="USD", settings=_default_settings(),
            open_positions=5, daily_pnl=-600.0, weekly_pnl=-2000.0,
        )
    msg = str(exc_info.value)
    assert "qty must be positive" in msg
    assert "price must be positive" in msg
    assert "max positions" in msg
    assert "daily loss limit" in msg
    assert "weekly loss limit" in msg


def test_successful_validation_logged(caplog):
    """Passing order logs the validation details."""
    with caplog.at_level(logging.INFO, logger="order_validator"):
        validate_order(
            symbol="AAPL", qty=10, price=150.0, direction="BUY",
            currency="USD", settings=_default_settings(),
            open_positions=2, daily_pnl=0.0, weekly_pnl=0.0,
        )
    assert "Order validated" in caplog.text
    assert "AAPL" in caplog.text


def test_default_limits_used():
    """If settings don't specify limits, sensible defaults are used."""
    # Empty settings — should use defaults (500 qty, 5000 notional, etc.)
    validate_order(
        symbol="AAPL", qty=10, price=150.0, direction="BUY",
        currency="USD", settings={},
        open_positions=2, daily_pnl=0.0, weekly_pnl=0.0,
    )
