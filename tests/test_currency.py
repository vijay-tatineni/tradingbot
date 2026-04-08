"""
tests/test_currency.py — Tests for the centralized currency conversion utilities.
"""

from bot.currency import (
    pence_to_pounds, pounds_to_pence,
    convert_pnl_to_base, is_pence_instrument,
)


def test_pence_to_pounds():
    """1560p -> £15.60"""
    assert pence_to_pounds(1560) == 15.60


def test_pounds_to_pence():
    """£15.60 -> 1560p"""
    assert pounds_to_pence(15.60) == 1560.0


def test_convert_pnl_gbp():
    """GBP P&L should be divided by 100."""
    assert convert_pnl_to_base(1560, "GBP") == 15.60


def test_convert_pnl_usd():
    """USD P&L should not be converted."""
    assert convert_pnl_to_base(150.0, "USD") == 150.0


def test_convert_pnl_eur():
    """EUR P&L should not be converted."""
    assert convert_pnl_to_base(200.0, "EUR") == 200.0


def test_is_pence_instrument_gbp():
    """GBP should be identified as pence instrument."""
    assert is_pence_instrument("GBP") is True


def test_is_pence_instrument_usd():
    """USD should not be identified as pence instrument."""
    assert is_pence_instrument("USD") is False


def test_convert_pnl_gbp_negative():
    """Negative GBP P&L should still convert correctly."""
    assert convert_pnl_to_base(-1560, "GBP") == -15.60


def test_convert_pnl_zero():
    """Zero P&L should remain zero."""
    assert convert_pnl_to_base(0.0, "GBP") == 0.0


def test_is_pence_instrument_case_insensitive():
    """Should handle lowercase currency codes."""
    assert is_pence_instrument("gbp") is True
    assert is_pence_instrument("usd") is False
