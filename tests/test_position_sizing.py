"""
tests/test_position_sizing.py — Tests for risk-based position sizing.
"""

import pytest
from bot.sizing import calculate_qty


def test_calculate_qty_usd():
    """$1000 target / $200 price = 5 shares."""
    inst = {'symbol': 'CVX', 'qty': 400, 'currency': 'USD'}
    assert calculate_qty(inst, 200.0, default_target_notional=1000) == 5


def test_calculate_qty_gbp_pence():
    """£1000 target / 250p price = 400 shares.
    Price in pence must be converted to pounds first."""
    inst = {'symbol': 'BARC', 'qty': 100, 'currency': 'GBP'}
    assert calculate_qty(inst, 250.0, default_target_notional=1000) == 400


def test_calculate_qty_expensive_stock():
    """$1000 target / $500 price = 2 shares."""
    inst = {'symbol': 'MSFT', 'qty': 10, 'currency': 'USD'}
    assert calculate_qty(inst, 500.0, default_target_notional=1000) == 2


def test_calculate_qty_cheap_stock():
    """$1000 target / $10 price = 100 shares."""
    inst = {'symbol': 'PLTR', 'qty': 50, 'currency': 'USD'}
    assert calculate_qty(inst, 10.0, default_target_notional=1000) == 100


def test_calculate_qty_minimum_one():
    """Even if price > target, qty should be at least 1."""
    inst = {'symbol': 'BRK', 'qty': 1, 'currency': 'USD'}
    assert calculate_qty(inst, 5000.0, default_target_notional=1000) == 1


def test_calculate_qty_zero_price():
    """If price is 0, fall back to fixed qty."""
    inst = {'symbol': 'TEST', 'qty': 42, 'currency': 'USD'}
    assert calculate_qty(inst, 0.0, default_target_notional=1000) == 42


def test_calculate_qty_no_target_uses_fixed():
    """If no target_notional set, use instrument's fixed qty."""
    inst = {'symbol': 'TEST', 'qty': 75, 'currency': 'USD'}
    assert calculate_qty(inst, 100.0, default_target_notional=None) == 75


def test_calculate_qty_per_instrument_override():
    """Per-instrument target_notional overrides global default."""
    inst = {'symbol': 'BARC', 'qty': 100, 'currency': 'USD',
            'target_notional': 2000}
    # 2000 / 100 = 20, not the global 1000/100 = 10
    assert calculate_qty(inst, 100.0, default_target_notional=1000) == 20


def test_calculate_qty_global_default():
    """Global default_target_notional used when instrument
    has no target_notional."""
    inst = {'symbol': 'CVX', 'qty': 400, 'currency': 'USD'}
    assert calculate_qty(inst, 200.0, default_target_notional=1000) == 5


def test_equal_risk_across_instruments():
    """BARC (250p) and CVX ($200) with same target_notional
    should produce similar dollar exposures."""
    barc = {'symbol': 'BARC', 'qty': 100, 'currency': 'GBP'}
    cvx = {'symbol': 'CVX', 'qty': 100, 'currency': 'USD'}

    barc_qty = calculate_qty(barc, 250.0, default_target_notional=1000)
    cvx_qty = calculate_qty(cvx, 200.0, default_target_notional=1000)

    # BARC: 250p = £2.50, qty = 1000/2.50 = 400, exposure = 400 × £2.50 = £1000
    barc_exposure = barc_qty * (250.0 / 100.0)  # convert pence to pounds
    # CVX: $200, qty = 5, exposure = 5 × $200 = $1000
    cvx_exposure = cvx_qty * 200.0

    assert barc_exposure == 1000.0
    assert cvx_exposure == 1000.0
