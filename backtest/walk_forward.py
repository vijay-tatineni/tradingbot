"""
backtest/walk_forward.py — Sliding-window walk-forward optimisation loop.

Slides training+test windows across the full dataset:
    Step 1: Train [Month 1-6]   Test [Month 7-9]
    Step 2: Train [Month 4-9]   Test [Month 10-12]
    ...
Steps forward by test_months each iteration.

For each step: grid-search on training window, evaluate on test window
with the best params. Stitches all OOS results for a final verdict.
"""

from collections import Counter
from dataclasses import dataclass, field
from dateutil.relativedelta import relativedelta

import pandas as pd

from backtest.config import (
    WF_ROBUST_THRESHOLD, WF_MARGINAL_THRESHOLD, MIN_TRADES_PER_WINDOW,
)
from backtest.offline_signals import generate_signals
from backtest.grid_search import run_grid_search
from backtest.simulator import simulate_trades, summarise


@dataclass
class WFStep:
    """Results from one train+test window."""
    step_num: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    best_stop_pct: float
    best_tp_pct: float
    is_pnl: float
    is_profit_factor: float
    is_trade_count: int
    oos_pnl: float
    oos_profit_factor: float
    oos_win_rate: float
    oos_trade_count: int


@dataclass
class WalkForwardResult:
    """Aggregated walk-forward results for one instrument+timeframe."""
    symbol: str
    timeframe: str
    steps: list[WFStep] = field(default_factory=list)
    is_total_pnl: float = 0.0
    is_avg_profit_factor: float = 0.0
    oos_total_pnl: float = 0.0
    oos_profit_factor: float = 0.0
    oos_win_rate: float = 0.0
    oos_trade_count: int = 0
    wf_efficiency: float = 0.0
    best_stop_pct: float = 0.0
    best_tp_pct: float = 0.0
    param_stability: str = "unstable"
    verdict: str = "no_edge"


def run_walk_forward(
    symbol: str,
    df: pd.DataFrame,
    indicator_settings: dict,
    instrument_config: dict,
    train_months: int = 6,
    test_months: int = 3,
) -> WalkForwardResult | None:
    """
    Run walk-forward optimisation on a single instrument.

    Window mechanics:
        - Training window: train_months of data
        - Test window: next test_months immediately after
        - Step forward by test_months and repeat
        - Stop when not enough data for another train+test

    Returns WalkForwardResult or None if insufficient data.
    """
    if df.empty or "datetime" not in df.columns:
        return None

    qty = instrument_config.get("qty", 1)
    long_only = instrument_config.get("long_only", True)
    timeframe = instrument_config.get("timeframe", "daily")
    currency = instrument_config.get("currency", "USD")

    result = WalkForwardResult(symbol=symbol, timeframe=timeframe)

    data_start = df["datetime"].iloc[0]
    data_end = df["datetime"].iloc[-1]

    # Build window boundaries
    steps = []
    step_num = 0
    train_start = data_start

    while True:
        train_end = train_start + relativedelta(months=train_months)
        test_start = train_end
        test_end = test_start + relativedelta(months=test_months)

        if test_end > data_end:
            break

        step_num += 1
        steps.append((step_num, train_start, train_end, test_start, test_end))

        # Step forward by test_months
        train_start = train_start + relativedelta(months=test_months)

    if not steps:
        print(f"  {symbol}: not enough data for walk-forward "
              f"({train_months}+{test_months} months needed)")
        return None

    print(f"\n  {symbol} ({timeframe}): {len(steps)} walk-forward steps")

    all_oos_trades = []
    is_pfs = []

    for step_num, t_start, t_end, ts_start, ts_end in steps:
        t_start_str = t_start.strftime("%b %Y")
        t_end_str = (t_end - relativedelta(days=1)).strftime("%b %Y")
        ts_start_str = ts_start.strftime("%b %Y")
        ts_end_str = (ts_end - relativedelta(days=1)).strftime("%b %Y")

        # Include ALL data from beginning up to train_end for indicator warmup.
        # The MA200 needs 200 prior bars, so we can't just slice to the train window.
        # We pass the full history up to the window end, and use start_from to
        # only record signals within the target window.

        # Training: full history up to train_end, signals only from train_start
        train_slice = df[df["datetime"] < t_end].reset_index(drop=True)
        train_start_idx = int((train_slice["datetime"] >= t_start).idxmax()) if (train_slice["datetime"] >= t_start).any() else 0
        train_end_idx = len(train_slice) - 1

        # Test: full history up to test_end, signals only from test_start
        test_slice = df[df["datetime"] < ts_end].reset_index(drop=True)
        test_start_idx = int((test_slice["datetime"] >= ts_start).idxmax()) if (test_slice["datetime"] >= ts_start).any() else 0

        if train_end_idx - train_start_idx < 20:
            print(f"    Step {step_num}/{len(steps)}: "
                  f"Train {t_start_str}-{t_end_str} — insufficient bars, skipping")
            continue

        # Generate signals on training window (with full history for warmup)
        train_signals = generate_signals(
            train_slice, indicator_settings, symbol, start_from=train_start_idx,
        )

        # Grid search: simulator only uses bars within the train window
        # Signals have bar_index relative to train_slice, and the simulator
        # scans forward from each signal's bar_index within train_slice
        grid = run_grid_search(
            train_signals, train_slice, qty=qty, long_only=long_only,
            symbol=symbol, currency=currency,
        )

        if grid is None:
            print(f"    Step {step_num}/{len(steps)}: "
                  f"Train {t_start_str}-{t_end_str} — no valid param combos "
                  f"({len(train_signals)} signals)")
            continue

        # Generate signals on test window (with full history for warmup)
        test_signals = generate_signals(
            test_slice, indicator_settings, symbol, start_from=test_start_idx,
        )

        # Simulate on test window with best params from training
        oos_trades = simulate_trades(
            test_signals, test_slice,
            stop_pct=grid.best_stop_pct,
            tp_pct=grid.best_tp_pct,
            qty=qty,
            long_only=long_only,
            currency=currency,
        )
        oos_summary = summarise(oos_trades)

        step_result = WFStep(
            step_num=step_num,
            train_start=str(t_start.date()),
            train_end=str(t_end.date()),
            test_start=str(ts_start.date()),
            test_end=str(ts_end.date()),
            best_stop_pct=grid.best_stop_pct,
            best_tp_pct=grid.best_tp_pct,
            is_pnl=grid.best_pnl,
            is_profit_factor=grid.best_profit_factor,
            is_trade_count=grid.best_trade_count,
            oos_pnl=oos_summary.total_pnl,
            oos_profit_factor=oos_summary.profit_factor,
            oos_win_rate=oos_summary.win_rate,
            oos_trade_count=oos_summary.trade_count,
        )
        result.steps.append(step_result)
        all_oos_trades.extend(oos_trades)
        is_pfs.append(grid.best_profit_factor)

        # Status line
        check = "+" if oos_summary.total_pnl >= 0 else "-"
        is_pf_str = f"{grid.best_profit_factor:.2f}" if grid.best_profit_factor < 100 else ">99"
        oos_pf_str = f"{oos_summary.profit_factor:.2f}" if oos_summary.profit_factor < 100 else ">99"
        print(f"    Step {step_num}/{len(steps)}: "
              f"Train {t_start_str}-{t_end_str} -> "
              f"Best: {grid.best_stop_pct}%/{grid.best_tp_pct}% "
              f"(PF {is_pf_str}) -> "
              f"Test {ts_start_str}-{ts_end_str}: "
              f"PF {oos_pf_str} "
              f"P&L ${oos_summary.total_pnl:+.0f} [{check}]")

    if not result.steps:
        return None

    # Aggregate OOS results
    oos_full = summarise(all_oos_trades)
    result.oos_total_pnl = oos_full.total_pnl
    result.oos_profit_factor = oos_full.profit_factor
    result.oos_win_rate = oos_full.win_rate
    result.oos_trade_count = oos_full.trade_count

    # Aggregate IS results
    result.is_total_pnl = sum(s.is_pnl for s in result.steps)
    # Cap infinite profit factors at 10.0 for averaging (inf means no losses)
    capped_pfs = [min(pf, 10.0) for pf in is_pfs]
    avg_is_pf = sum(capped_pfs) / len(capped_pfs) if capped_pfs else 0
    result.is_avg_profit_factor = round(avg_is_pf, 2)

    # Walk-forward efficiency: OOS PF / avg IS PF
    oos_pf = min(oos_full.profit_factor, 10.0)  # cap inf
    if avg_is_pf > 0:
        result.wf_efficiency = round(oos_pf / avg_is_pf, 2)
    else:
        result.wf_efficiency = 0.0

    # Most common best params across windows
    stop_counts = Counter(s.best_stop_pct for s in result.steps)
    tp_counts = Counter(s.best_tp_pct for s in result.steps)
    result.best_stop_pct = stop_counts.most_common(1)[0][0]
    result.best_tp_pct = tp_counts.most_common(1)[0][0]

    # Parameter stability assessment
    result.param_stability = _assess_stability(result.steps)

    # Verdict
    result.verdict = _assign_verdict(result)

    return result


def _assess_stability(steps: list[WFStep]) -> str:
    """
    Assess how stable the optimal parameters are across windows.

    stable:   same (or very similar) params in all windows
    drifting: params change gradually
    unstable: params jump around — sign of overfitting
    """
    if len(steps) <= 1:
        return "stable"

    stops = [s.best_stop_pct for s in steps]
    tps = [s.best_tp_pct for s in steps]

    unique_stops = len(set(stops))
    unique_tps = len(set(tps))
    n = len(steps)

    # Check for consecutive changes (drift detection)
    stop_changes = sum(1 for i in range(1, n) if stops[i] != stops[i - 1])
    tp_changes = sum(1 for i in range(1, n) if tps[i] != tps[i - 1])

    # If most windows pick the same params → stable
    if unique_stops <= 2 and unique_tps <= 2:
        return "stable"

    # If params change every window → unstable
    if stop_changes >= n * 0.7 or tp_changes >= n * 0.7:
        return "unstable"

    return "drifting"


def _assign_verdict(result: WalkForwardResult) -> str:
    """
    Assign a robustness verdict based on WF efficiency and OOS P&L.

    robust:   WF efficiency > 0.5
    marginal: WF efficiency 0.3 - 0.5
    overfit:  WF efficiency < 0.3
    no_edge:  OOS P&L negative
    """
    if result.oos_total_pnl < 0:
        return "no_edge"
    if result.wf_efficiency > WF_ROBUST_THRESHOLD:
        return "robust"
    if result.wf_efficiency >= WF_MARGINAL_THRESHOLD:
        return "marginal"
    return "overfit"
