"""Frozen §14 metric definitions — synthetic trade lists ONLY (no dev/OOS data,
so no outcome leak). Verifies PF, instrument concentration, top-five removal."""
from dataclasses import dataclass

from backtest.breakout_metrics import (
    profit_factor, instrument_concentration, remove_top_five,
)


@dataclass
class T:
    symbol: str
    pnl_usd: float
    exit_reason: str = "intra_stop"


def test_profit_factor_includes_all_trades():
    trades = [T("A", 100), T("B", -40), T("C", 50), T("D", -10),
              T("E", 20, "END_OF_TEST_LIQUIDATION")]
    # pos = 170, neg = 50 -> 3.4 ; END_OF_TEST_LIQUIDATION counted.
    assert profit_factor(trades) == 170 / 50


def test_profit_factor_no_losses_is_inf():
    assert profit_factor([T("A", 10), T("B", 5)]) == float("inf")


def test_profit_factor_no_trades_is_zero():
    assert profit_factor([]) == 0.0


def test_instrument_concentration_ratio_and_losers_zero():
    # A net +150, B net -30 (loser -> 0), C net +50. denom = 200.
    trades = [T("A", 100), T("A", 50), T("B", -30), T("C", 50)]
    c = instrument_concentration(trades)
    assert c["A"] == 150 / 200
    assert c["C"] == 50 / 200
    assert c["B"] == 0.0
    assert abs(sum(v for v in c.values()) - 1.0) < 1e-9


def test_top_five_removal_is_arithmetic_no_rerun():
    trades = [T("A", 100), T("B", 90), T("C", 80), T("D", 70),
              T("E", 60), T("F", 5), T("G", -200)]
    total, adjusted, removed = remove_top_five(trades)
    assert total == 100 + 90 + 80 + 70 + 60 + 5 - 200      # 205
    assert {t.pnl_usd for t in removed} == {100, 90, 80, 70, 60}  # 5 best winners
    assert adjusted == 205 - (100 + 90 + 80 + 70 + 60)     # -195


def test_top_five_removal_only_removes_winners():
    # Fewer than five winners -> remove only the actual winners.
    trades = [T("A", 50), T("B", 10), T("C", -5), T("D", -20)]
    total, adjusted, removed = remove_top_five(trades)
    assert {t.pnl_usd for t in removed} == {50, 10}
    assert adjusted == total - 60
