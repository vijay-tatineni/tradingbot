"""
tests/test_backtest_comparison.py
Test the comparison between backtest (IS) and walk-forward (OOS).
"""

import pytest


# ── Helper functions matching the tests.html logic ─────────────

def calc_degradation(is_pnl, oos_pnl):
    """Calculate degradation matching the fixed JS logic."""
    if abs(is_pnl) == 0:
        return 0
    return ((oos_pnl - is_pnl) / abs(is_pnl)) * 100


def calc_reality_discount(is_total, oos_total):
    """Reality discount = (1 - oos_total / is_total) * 100."""
    if is_total == 0:
        return 0
    return (1 - oos_total / is_total) * 100


def action_badge(enabled, verdict, params_match):
    """Determine action badge for comparison table."""
    if enabled and verdict == 'no_edge':
        return 'Disable'
    if not enabled and verdict == 'robust':
        return 'Enable'
    if enabled and verdict == 'robust' and not params_match:
        return 'Update'
    if enabled and verdict == 'robust' and params_match:
        return 'OK'
    return 'OK'


# ── Totals tests ───────────────────────────────────────────────

def test_comparison_totals_calculation():
    """Total IS P&L and OOS P&L must sum all instruments correctly,
    including negative values."""
    results = [
        {'is_pnl': 90000, 'oos_pnl': 33791},
        {'is_pnl': 277000, 'oos_pnl': 108000},
        {'is_pnl': 40, 'oos_pnl': -506},
        {'is_pnl': 500, 'oos_pnl': -19846},
    ]
    tot_is = sum(r['is_pnl'] for r in results)
    tot_oos = sum(r['oos_pnl'] for r in results)

    assert tot_is == 367540
    assert tot_oos == 121439
    # Verify negatives were subtracted, not added
    assert tot_oos != 33791 + 108000 + 506 + 19846


# ── Reality discount tests ─────────────────────────────────────

def test_reality_discount_calculation():
    """Reality discount = (1 - oos_total / is_total) * 100.
    IS $367K, OOS $122K -> ~67% discount."""
    discount = calc_reality_discount(367000, 122000)
    assert round(discount) == 67, f"Expected ~67%, got {discount:.0f}%"


def test_reality_discount_with_negatives():
    """If OOS total is negative but IS is positive,
    reality discount should be > 100%."""
    discount = calc_reality_discount(100000, -5000)
    assert discount > 100, f"Expected >100%, got {discount:.0f}%"
    # (1 - (-5000/100000)) * 100 = 105%
    assert discount == 105.0


# ── Degradation tests ──────────────────────────────────────────

def test_degradation_with_negatives():
    """Degradation handles all sign combinations."""
    # Positive IS, negative OOS
    deg = calc_degradation(40, -506)
    assert round(deg) == -1365

    # Negative IS, negative OOS (improved)
    deg = calc_degradation(-277, -248)
    assert deg > 0  # OOS is less negative = improvement

    # Both positive
    deg = calc_degradation(90000, 33000)
    assert round(deg) == -63


# ── Params match tests ─────────────────────────────────────────

def test_params_match_detection():
    """Current 4.0%/12.0% vs WF 5.0%/4.0% -> params_match = false.
    Current 1.2%/4.0% vs WF 1.5%/3.0% -> params_match = false.
    Current 2.5%/8.0% vs WF 2.5%/8.0% -> params_match = true."""
    def params_match(cur_stop, cur_tp, wf_stop, wf_tp):
        return cur_stop == wf_stop and cur_tp == wf_tp

    assert params_match(4.0, 12.0, 5.0, 4.0) is False
    assert params_match(1.2, 4.0, 1.5, 3.0) is False
    assert params_match(2.5, 8.0, 2.5, 8.0) is True


# ── Action badge tests ─────────────────────────────────────────

def test_action_badge_logic():
    """Enabled + no_edge -> 'Disable' badge.
    Disabled + robust -> 'Enable' badge.
    Enabled + robust + params mismatch -> 'Update' badge.
    Enabled + robust + params match -> 'OK' badge."""
    assert action_badge(True, 'no_edge', True) == 'Disable'
    assert action_badge(False, 'robust', True) == 'Enable'
    assert action_badge(True, 'robust', False) == 'Update'
    assert action_badge(True, 'robust', True) == 'OK'
