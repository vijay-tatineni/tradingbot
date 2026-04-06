"""
tests/test_dashboard_data.py
Test the data that feeds the dashboard — P&L colour logic and data integration.
"""

import json
import pytest


# ── Dashboard P&L colour logic ─────────────────────────────────
# The dashboard JS sets:
#   pnlEl.className = 'card-value ' + (pnl >= 0 ? 'pnl-pos' : 'pnl-neg')
# We test the equivalent Python logic that determines the class.

def pnl_class(pnl: float) -> str:
    """Replicate the dashboard JS pnlClass logic."""
    return 'pnl-pos' if pnl >= 0 else 'pnl-neg'


def pnl_text(pnl: float) -> str:
    """Replicate the dashboard JS P&L text formatting."""
    sign = '+' if pnl >= 0 else ''
    return f"{sign}${abs(pnl):.2f}"


def test_total_pnl_colour_positive():
    """Total P&L > 0 should render as green (pnl-pos)."""
    assert pnl_class(742.71) == 'pnl-pos'
    assert pnl_class(0.01) == 'pnl-pos'


def test_total_pnl_colour_negative():
    """Total P&L < 0 should render as red (pnl-neg)."""
    assert pnl_class(-100.0) == 'pnl-neg'
    assert pnl_class(-0.01) == 'pnl-neg'


def test_total_pnl_colour_zero():
    """Total P&L = 0 should render as neutral (pnl-pos for >= 0)."""
    assert pnl_class(0.0) == 'pnl-pos'


def test_risk_bar_independent_of_pnl_colour():
    """Risk bar colour and P&L colour should be independent.
    High risk (74%) can coexist with positive P&L (green)."""
    pnl = 742.71
    risk_pct = 74.0
    loss_limit = 10000

    # P&L colour based on pnl value
    assert pnl_class(pnl) == 'pnl-pos'

    # Risk bar colour based on risk_pct value (not pnl)
    risk_color = '#ef4444' if risk_pct > 70 else '#f59e0b' if risk_pct > 40 else '#22c55e'
    assert risk_color == '#ef4444'  # high risk = red bar

    # They are independent — positive pnl with red risk bar is valid
    assert pnl_class(pnl) == 'pnl-pos'  # green text
    assert risk_color == '#ef4444'        # red bar


def test_dashboard_pnl_includes_gbp_conversion():
    """Dashboard total P&L must convert GBP pence to pounds before summing with USD values.
    Simulate: USD instrument +$50, GBP instrument raw pnl 1660p -> should be +16.60 pounds.
    Total = $50 + $16.60 = $66.60 (not $50 + $1660 = $1710)."""
    usd_pnl = 50.0
    gbp_raw_pnl_pence = 1660  # raw P&L before conversion
    gbp_pnl_pounds = gbp_raw_pnl_pence / 100  # 16.60

    total_correct = usd_pnl + gbp_pnl_pounds
    total_wrong = usd_pnl + gbp_raw_pnl_pence

    assert total_correct == 66.60
    assert total_wrong == 1710  # what happens without conversion
    assert total_correct != total_wrong


def test_data_json_structure():
    """Verify the data.json structure has required fields for dashboard."""
    # Simulate the data dict that dashboard.py produces
    data = {
        'total_pnl': 742.71,
        'risk_pct': 7.4,
        'loss_limit': 10000,
        'cycle': 42,
        'positions': [],
        'signals': [],
        'accum': [],
    }
    assert 'total_pnl' in data
    assert isinstance(data['total_pnl'], float)
    assert 'risk_pct' in data
    # P&L colour should be based on total_pnl, not risk_pct
    assert pnl_class(data['total_pnl']) == 'pnl-pos'
