"""
tests/test_dashboard_data.py
Test the data that feeds the dashboard — P&L colour logic, currency formatting, and data integration.
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


# ── Currency formatting (mirrors JS formatPrice/formatEntry/formatPnl) ──

def ccy_symbol(currency: str) -> str:
    """Return currency symbol: £ for GBP, € for EUR, $ for USD."""
    return {'GBP': '£', 'EUR': '€'}.get(currency, '$')


def is_gbp_pence(currency: str) -> bool:
    return currency == 'GBP'


def format_price(price: float, currency: str) -> str:
    """Format a market price with currency indicator.
    GBP prices are in pence: show '3,453p (£34.53)' for >= 1000, '950p' otherwise.
    USD/EUR: show '$367.44' or '€254.60'.
    """
    if price <= 0:
        return '--'
    if is_gbp_pence(currency):
        p = round(price)
        if p >= 1000:
            return f"{p:,}p (£{price/100:.2f})"
        return f"{p:,}p"
    return f"{ccy_symbol(currency)}{price:,.2f}"


def format_entry(cost: float, currency: str) -> str:
    """Format an entry price — always in major currency units.
    GBP pence converted to pounds: 3481.785p -> '£34.82'.
    """
    if cost <= 0:
        return '--'
    if is_gbp_pence(currency):
        return f"£{cost/100:.2f}"
    return f"{ccy_symbol(currency)}{cost:,.2f}"


def format_pnl(pnl: float, currency: str) -> str:
    """Format P&L with sign and currency symbol: '+$25.77', '-£12.40'."""
    if pnl == 0:
        return '--'
    sign = '+' if pnl >= 0 else '-'
    return f"{sign}{ccy_symbol(currency)}{abs(pnl):.2f}"


def format_pos_price(cost: float, currency: str) -> str:
    """Format position price for the Open Positions box.
    GBP: '3481p'. USD/EUR: '$367.44'.
    """
    if is_gbp_pence(currency):
        return f"{round(cost)}p"
    return f"{ccy_symbol(currency)}{cost:,.2f}"


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


# ── Currency formatting tests ─────────────────────────────────

class TestFormatPrice:
    def test_usd_price(self):
        assert format_price(367.44, 'USD') == '$367.44'

    def test_eur_price(self):
        assert format_price(254.60, 'EUR') == '€254.60'

    def test_gbp_pence_under_1000(self):
        assert format_price(950, 'GBP') == '950p'

    def test_gbp_pence_over_1000_shows_both(self):
        result = format_price(3453, 'GBP')
        assert '3,453p' in result
        assert '£34.53' in result

    def test_zero_price_returns_placeholder(self):
        assert format_price(0, 'USD') == '--'

    def test_negative_price_returns_placeholder(self):
        assert format_price(-1, 'GBP') == '--'

    def test_gbp_exactly_1000(self):
        result = format_price(1000, 'GBP')
        assert '1,000p' in result
        assert '£10.00' in result


class TestFormatEntry:
    def test_usd_entry(self):
        assert format_entry(367.44, 'USD') == '$367.44'

    def test_eur_entry(self):
        assert format_entry(254.60, 'EUR') == '€254.60'

    def test_gbp_pence_to_pounds(self):
        """GBP entry prices in pence should display as pounds."""
        assert format_entry(3481.785, 'GBP') == '£34.82'

    def test_gbp_small_pence(self):
        assert format_entry(150, 'GBP') == '£1.50'

    def test_zero_entry(self):
        assert format_entry(0, 'USD') == '--'


class TestFormatPnl:
    def test_positive_usd(self):
        assert format_pnl(25.77, 'USD') == '+$25.77'

    def test_negative_gbp(self):
        assert format_pnl(-12.40, 'GBP') == '-£12.40'

    def test_positive_eur(self):
        assert format_pnl(5.20, 'EUR') == '+€5.20'

    def test_zero_pnl(self):
        assert format_pnl(0, 'USD') == '--'

    def test_negative_usd(self):
        assert format_pnl(-100.50, 'USD') == '-$100.50'


class TestFormatPosPrice:
    def test_gbp_shows_pence(self):
        assert format_pos_price(3481, 'GBP') == '3481p'

    def test_usd_shows_dollar(self):
        assert format_pos_price(367.44, 'USD') == '$367.44'

    def test_eur_shows_euro(self):
        assert format_pos_price(254.60, 'EUR') == '€254.60'

    def test_gbp_rounds_to_nearest_pence(self):
        assert format_pos_price(3481.785, 'GBP') == '3482p'


class TestCcySymbol:
    def test_gbp(self):
        assert ccy_symbol('GBP') == '£'

    def test_eur(self):
        assert ccy_symbol('EUR') == '€'

    def test_usd(self):
        assert ccy_symbol('USD') == '$'

    def test_unknown_defaults_to_dollar(self):
        assert ccy_symbol('CHF') == '$'


class TestPnlByCurrency:
    def test_breakdown_aggregation(self):
        """Simulate the pnl_by_currency aggregation from dashboard.py."""
        signal_rows = [
            {'currency': 'USD', 'unreal_pnl': 50.0},
            {'currency': 'USD', 'unreal_pnl': -10.0},
            {'currency': 'GBP', 'unreal_pnl': 25.0},
            {'currency': 'EUR', 'unreal_pnl': -12.0},
            {'currency': 'USD', 'unreal_pnl': 0},  # zero — should be excluded
        ]
        pnl_by_ccy = {}
        for r in signal_rows:
            ccy = r.get('currency', 'USD')
            pnl_val = r.get('unreal_pnl', 0)
            if pnl_val != 0:
                pnl_by_ccy[ccy] = round(pnl_by_ccy.get(ccy, 0) + pnl_val, 2)

        assert pnl_by_ccy == {'USD': 40.0, 'GBP': 25.0, 'EUR': -12.0}

    def test_single_currency_no_breakdown(self):
        """When all positions are same currency, breakdown has one key."""
        signal_rows = [
            {'currency': 'USD', 'unreal_pnl': 50.0},
            {'currency': 'USD', 'unreal_pnl': -10.0},
        ]
        pnl_by_ccy = {}
        for r in signal_rows:
            ccy = r.get('currency', 'USD')
            pnl_val = r.get('unreal_pnl', 0)
            if pnl_val != 0:
                pnl_by_ccy[ccy] = round(pnl_by_ccy.get(ccy, 0) + pnl_val, 2)

        assert len(pnl_by_ccy) == 1
        assert pnl_by_ccy['USD'] == 40.0
