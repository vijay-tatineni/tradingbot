"""Frozen §4/§5 metric + decision-tree definitions — synthetic inputs ONLY
(no dev/OOS data, no outcome leak)."""
import math
from dataclasses import dataclass

import numpy as np

from backtest.breakout_report import (
    sharpe, max_drawdown, window_returns, episodes, episode_bootstrap_ci,
    evaluate_3b, instrument_profit_share, top_five_trade_contribution,
)


@dataclass
class T:
    symbol: str
    pnl_usd: float
    entry_date: str
    exit_date: str


def _equity_from_returns(initial, rets):
    eq, cur = [], initial
    # fabricate dates Mar 2025 (W1) for window tests
    for i, r in enumerate(rets):
        cur = cur * (1 + r)
        eq.append((f"2025-03-{i+3:02d}", cur))
    return eq


def test_sharpe_matches_independent_calc():
    rets = [0.01, -0.005, 0.02, 0.0, 0.01, -0.002]
    eq = _equity_from_returns(100.0, rets)
    r = np.array(rets)
    expected = r.mean() / r.std(ddof=1) * math.sqrt(252)
    assert abs(sharpe(eq, 100.0) - expected) < 1e-9


def test_max_drawdown_from_equity_curve():
    eq = [("2025-03-03", 110), ("2025-03-04", 90), ("2025-03-05", 95),
          ("2025-03-06", 120), ("2025-03-07", 60)]
    assert abs(max_drawdown(eq, 100.0) - 0.5) < 1e-12     # (120-60)/120


def test_window_attribution_assigns_by_date():
    eq = [("2025-03-15", 101.0), ("2025-07-15", 102.0), ("2025-10-15", 103.0),
          ("2026-01-15", 104.0)]
    wr = window_returns(eq, 100.0)
    assert wr["W1"] is not None and wr["W2"] is not None
    assert wr["W3"] is not None and wr["W4"] is not None


def test_episodes_transitive_overlap():
    # A-B overlap, B-C overlap (chain), A-C do NOT -> still one episode of 3.
    trades = [
        T("A", 10, "2025-03-01", "2025-03-10"),
        T("B", -5, "2025-03-05", "2025-03-15"),
        T("C", 7, "2025-03-14", "2025-03-20"),
        T("D", 3, "2025-04-01", "2025-04-05"),   # disjoint
    ]
    eps = episodes(trades)
    sizes = sorted(len(e) for e in eps)
    assert sizes == [1, 3]


def test_bootstrap_is_deterministic_and_brackets():
    trades = [T(f"S{i}", (-1) ** i * (i + 1) * 10.0,
                f"2025-03-{i+1:02d}", f"2025-03-{i+2:02d}") for i in range(8)]
    a = episode_bootstrap_ci(trades, seed=20260605)
    b = episode_bootstrap_ci(trades, seed=20260605)
    assert a == b                                  # deterministic
    lo, hi, n = a
    assert lo <= hi and n >= 1


def test_instrument_share_and_top5_band():
    trades = [T("A", 100, "2025-03-01", "2025-03-02"),
              T("B", 50, "2025-03-01", "2025-03-02"),
              T("C", -20, "2025-03-01", "2025-03-02")]
    share, mx = instrument_profit_share(trades)
    assert abs(mx - 100 / 150) < 1e-9              # A dominates
    assert top_five_trade_contribution(trades) == 1.0  # only 2 winners


BASE = dict(trade_count=120, pf_base=1.30, sharpe=0.8, net_base=5000,
            net_stress=2000, windows_positive=4, max_instrument_share=0.2,
            top5_removal_adjusted=-100, initial_oos_equity=100000,
            max_drawdown_base=0.08)


def test_decision_pass():
    assert evaluate_3b(dict(BASE))["verdict"] == "PASS"


def test_decision_provisional_under_100_trades():
    m = dict(BASE, trade_count=40)
    assert evaluate_3b(m)["verdict"] == "PROVISIONAL"


def test_decision_hard_dd_fail_overrides():
    m = dict(BASE, max_drawdown_base=0.20)
    out = evaluate_3b(m)
    assert out["verdict"] == "FAIL"
    assert "drawdown" in out["reasons"][0]


def test_decision_fail_lists_gates():
    m = dict(BASE, pf_base=1.0, sharpe=0.1)
    out = evaluate_3b(m)
    assert out["verdict"] == "FAIL"
    assert "PF>=1.15" in out["reasons"][0]
