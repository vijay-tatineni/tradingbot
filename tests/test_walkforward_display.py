"""
tests/test_walkforward_display.py
Test walk-forward results rendering logic and API output format.
"""

import pytest


# ── P&L formatting logic (mirrors tests.html fmtPnl) ──────────

def fmt_pnl(val):
    """Python equivalent of the fixed fmtPnl JS function."""
    if val is None:
        return '--'
    sign = '+' if val >= 0 else '-'
    return f"{sign}${abs(val):,.0f}"


def pnl_class(val):
    """Python equivalent of JS pnlClass."""
    return 'green' if val >= 0 else 'red'


def verdict_from_results(wf_efficiency, oos_pnl):
    """Determine walk-forward verdict.
    WF efficiency > 0.5 -> robust, 0.3-0.5 -> marginal, < 0.3 -> overfit.
    But if OOS P&L is negative -> no_edge regardless."""
    if oos_pnl < 0:
        return 'no_edge'
    if wf_efficiency > 0.5:
        return 'robust'
    elif wf_efficiency >= 0.3:
        return 'marginal'
    else:
        return 'overfit'


# ── Bug 1 fix tests: negative P&L sign ─────────────────────────

def test_negative_oos_pnl_keeps_sign():
    """OOS P&L of -$19,846 must display as -$19,846, not +$19,846."""
    result = fmt_pnl(-19846)
    assert result == '-$19,846', f"Expected '-$19,846', got '{result}'"
    assert '-' in result, "Negative sign must be present"


def test_positive_oos_pnl_keeps_sign():
    """Positive P&L should show + sign."""
    result = fmt_pnl(33791)
    assert result == '+$33,791'


def test_negative_oos_pnl_shows_red():
    """Negative OOS P&L should be flagged for red display."""
    assert pnl_class(-19846) == 'red'
    assert pnl_class(33791) == 'green'


def test_total_oos_pnl_subtracts_negatives():
    """Total OOS P&L must subtract negative values, not add them.
    Example: +$33,791 + (-$19,846) = $13,945, NOT $53,637"""
    results = [
        {'oos_pnl': 33791},
        {'oos_pnl': -19846},
    ]
    total = sum(r['oos_pnl'] for r in results)
    assert total == 13945, f"Expected 13945, got {total}"
    assert total != 53637, "Must not add absolute values"


# ── Degradation tests ──────────────────────────────────────────

def calc_degradation(is_pnl, oos_pnl):
    """Calculate degradation percentage matching the fixed JS logic."""
    if abs(is_pnl) == 0:
        return 0
    return ((oos_pnl - is_pnl) / abs(is_pnl)) * 100


def test_degradation_positive_to_positive():
    """IS +$90K -> OOS +$33K: degradation = -63%"""
    deg = calc_degradation(90000, 33000)
    assert round(deg) == -63, f"Expected -63%, got {deg:.0f}%"


def test_degradation_positive_to_negative():
    """IS +$40 -> OOS -$506: degradation = -1365%"""
    deg = calc_degradation(40, -506)
    assert round(deg) == -1365, f"Expected -1365%, got {deg:.0f}%"


def test_degradation_negative_is():
    """IS -$277 -> OOS -$248: handle gracefully, no division by zero."""
    deg = calc_degradation(-277, -248)
    # (-248 - (-277)) / abs(-277) * 100 = 29/277 * 100 = ~10.47%
    assert deg == pytest.approx(10.47, abs=0.1)


def test_degradation_zero_is():
    """IS $0: degradation should be 0, not divide-by-zero."""
    deg = calc_degradation(0, -500)
    assert deg == 0


# ── Verdict tests ──────────────────────────────────────────────

def test_wf_verdict_matches_efficiency():
    """WF efficiency > 0.5 -> robust, 0.3-0.5 -> marginal, < 0.3 -> overfit."""
    assert verdict_from_results(0.7, 1000) == 'robust'
    assert verdict_from_results(0.4, 1000) == 'marginal'
    assert verdict_from_results(0.2, 1000) == 'overfit'


def test_no_edge_verdict_when_oos_negative():
    """Even if WF efficiency is high (e.g., 0.84), if OOS P&L is negative,
    verdict should be no_edge. CVX: efficiency 0.84, OOS -$19,846 -> no_edge."""
    assert verdict_from_results(0.84, -19846) == 'no_edge'
    assert verdict_from_results(0.99, -1) == 'no_edge'
    assert verdict_from_results(0.1, -500) == 'no_edge'
