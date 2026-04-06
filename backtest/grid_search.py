"""
backtest/grid_search.py — Grid search over stop% and TP% combinations,
and indicator grid search for deep optimisation.

Indicator settings (Alligator, W%R, RSI, ADX thresholds) stay FIXED at
the values from instruments.json. Only stop% and TP% are optimised.
Signals are generated once; each grid combination only re-runs the simulator.
"""

import itertools
import time as _time
from dataclasses import dataclass, field

from backtest.config import (
    PARAM_GRID, MIN_TRADES_PER_WINDOW,
    INDICATOR_GRID, INDICATOR_FIXED, TOP_N_INDICATOR_COMBOS,
)
from backtest.offline_signals import Signal, generate_signals
from backtest.simulator import simulate_trades, summarise

import pandas as pd


@dataclass
class GridResult:
    """Best parameter combination found by grid search."""
    best_stop_pct: float
    best_tp_pct: float
    best_profit_factor: float
    best_pnl: float
    best_win_rate: float
    best_trade_count: int
    all_results: list[dict]   # Full grid for analysis


def run_grid_search(
    signals: list[Signal],
    df: pd.DataFrame,
    qty: int = 1,
    long_only: bool = True,
    symbol: str = "",
    currency: str = "USD",
    show_progress: bool = False,
) -> GridResult | None:
    """
    Try all (stop_pct, tp_pct) combinations from PARAM_GRID.

    Signals are pre-computed (indicators don't change with stop/TP).
    Each combination runs the simulator and records metrics.
    Ranks by profit_factor. Requires minimum MIN_TRADES_PER_WINDOW trades.

    Returns None if no combination produces enough trades.
    """
    stop_values = PARAM_GRID["trail_stop_pct"]
    tp_values = PARAM_GRID["take_profit_pct"]
    total = len(stop_values) * len(tp_values)

    all_results = []
    best = None
    count = 0

    for stop_pct in stop_values:
        for tp_pct in tp_values:
            count += 1
            if show_progress and count % 16 == 0:
                print(f"    {symbol} grid search: {count}/{total} "
                      f"({count * 100 // total}%)")

            trades = simulate_trades(signals, df, stop_pct, tp_pct, qty, long_only, currency)
            summary = summarise(trades)

            entry = {
                "stop_pct": stop_pct,
                "tp_pct": tp_pct,
                "pnl": summary.total_pnl,
                "profit_factor": summary.profit_factor,
                "win_rate": summary.win_rate,
                "trade_count": summary.trade_count,
                "max_drawdown": summary.max_drawdown,
            }
            all_results.append(entry)

            # Only consider combos with enough trades
            if summary.trade_count < MIN_TRADES_PER_WINDOW:
                continue

            # Rank by profit factor (more stable than raw P&L)
            if best is None or summary.profit_factor > best["profit_factor"]:
                best = entry

    if best is None:
        return None

    return GridResult(
        best_stop_pct=best["stop_pct"],
        best_tp_pct=best["tp_pct"],
        best_profit_factor=best["profit_factor"],
        best_pnl=best["pnl"],
        best_win_rate=best["win_rate"],
        best_trade_count=best["trade_count"],
        all_results=all_results,
    )


# ── Indicator grid search (deep optimisation) ──────────────────


@dataclass
class IndicatorCombo:
    """One indicator parameter combination with its WF result."""
    settings: dict
    wf_efficiency: float = 0.0
    oos_pnl: float = 0.0
    oos_profit_factor: float = 0.0
    oos_win_rate: float = 0.0
    oos_trade_count: int = 0


@dataclass
class OptimiseResult:
    """Result of a full two-phase optimisation."""
    symbol: str
    best_stop_pct: float = 0.0
    best_tp_pct: float = 0.0
    best_indicators: dict = field(default_factory=dict)
    wf_efficiency: float = 0.0
    oos_pnl: float = 0.0
    oos_profit_factor: float = 0.0
    oos_win_rate: float = 0.0
    oos_trade_count: int = 0
    current_oos_pnl: float = 0.0
    improvement_pct: float = 0.0
    combos_tested: int = 0
    duration_seconds: float = 0.0
    top_5: list = field(default_factory=list)


def _generate_indicator_combos(grid: dict = None) -> list[dict]:
    """Generate all indicator combinations from the grid."""
    grid = grid or INDICATOR_GRID
    keys = sorted(grid.keys())
    values = [grid[k] for k in keys]
    combos = []
    for combo in itertools.product(*values):
        settings = dict(zip(keys, combo))
        # Add fixed settings
        settings.update(INDICATOR_FIXED)
        combos.append(settings)
    return combos


def indicator_grid_search(
    symbol: str,
    df: pd.DataFrame,
    current_stop: float,
    current_tp: float,
    base_indicator_settings: dict,
    instrument_config: dict,
    train_months: int = 6,
    test_months: int = 3,
    grid: dict = None,
    top_n: int = TOP_N_INDICATOR_COMBOS,
    progress_callback=None,
) -> list[IndicatorCombo]:
    """
    Phase 1: Test all indicator combinations with fixed stop/TP using
    simple backtest (generate_signals + simulate_trades on full dataset).
    Returns top N indicator combos ranked by profit factor.
    """
    combos = _generate_indicator_combos(grid)
    total = len(combos)
    results = []

    qty = instrument_config.get("qty", 1)
    long_only = instrument_config.get("long_only", True)
    currency = instrument_config.get("currency", "USD")

    for i, settings in enumerate(combos):
        if progress_callback:
            progress_callback(
                phase=1, current=i + 1, total=total,
                detail=f"Phase 1: testing indicator combo {i + 1}/{total}",
            )

        # Merge base settings with this combo's overrides
        merged = {**base_indicator_settings, **settings}

        try:
            signals = generate_signals(df, merged, symbol=symbol)
            trades = simulate_trades(
                signals, df, current_stop, current_tp,
                qty, long_only, currency,
            )
            summary = summarise(trades)
        except Exception:
            continue

        if summary.trade_count < MIN_TRADES_PER_WINDOW:
            continue

        results.append(IndicatorCombo(
            settings=settings,
            oos_pnl=summary.total_pnl,
            oos_profit_factor=summary.profit_factor,
            oos_win_rate=summary.win_rate,
            oos_trade_count=summary.trade_count,
        ))

    # Rank by profit factor, then by P&L as tiebreaker
    results.sort(key=lambda x: (x.oos_profit_factor, x.oos_pnl), reverse=True)
    return results[:top_n]


def full_optimise(
    symbol: str,
    df: pd.DataFrame,
    base_indicator_settings: dict,
    instrument_config: dict,
    train_months: int = 6,
    test_months: int = 3,
    grid: dict = None,
    progress_callback=None,
) -> OptimiseResult | None:
    """
    Two-phase optimisation:
    Phase 1: Find top 5 indicator combos (with current stop/TP)
    Phase 2: Fine-tune stop/TP for each of the top 5
    Returns overall best combination.
    """
    from backtest.walk_forward import run_walk_forward

    start_time = _time.time()
    current_stop = instrument_config.get("trail_stop_pct", 2.0)
    current_tp = instrument_config.get("take_profit_pct", 8.0)

    result = OptimiseResult(symbol=symbol)

    # Phase 1: indicator grid search
    top_combos = indicator_grid_search(
        symbol=symbol, df=df,
        current_stop=current_stop, current_tp=current_tp,
        base_indicator_settings=base_indicator_settings,
        instrument_config=instrument_config,
        train_months=train_months, test_months=test_months,
        grid=grid, progress_callback=progress_callback,
    )

    if not top_combos:
        return None

    indicator_combos_tested = len(_generate_indicator_combos(grid))

    # Phase 2: fine-tune stop/TP for each top combo
    stop_values = PARAM_GRID["trail_stop_pct"]
    tp_values = PARAM_GRID["take_profit_pct"]
    stop_tp_total = len(stop_values) * len(tp_values)
    phase2_total = len(top_combos) * stop_tp_total

    best_overall = None
    phase2_count = 0

    for combo in top_combos:
        merged = {**base_indicator_settings, **combo.settings}

        for stop_pct in stop_values:
            for tp_pct in tp_values:
                phase2_count += 1
                if progress_callback:
                    progress_callback(
                        phase=2, current=phase2_count, total=phase2_total,
                        detail=f"Phase 2: walk-forward testing top combos {phase2_count}/{phase2_total}",
                    )

                try:
                    wf_result = run_walk_forward(
                        symbol=symbol, df=df,
                        indicator_settings=merged,
                        instrument_config={**instrument_config,
                                           "trail_stop_pct": stop_pct,
                                           "take_profit_pct": tp_pct},
                        train_months=train_months,
                        test_months=test_months,
                    )
                except Exception:
                    continue

                if wf_result is None:
                    continue

                entry = {
                    "indicators": combo.settings,
                    "stop_pct": stop_pct,
                    "tp_pct": tp_pct,
                    "wf_efficiency": wf_result.wf_efficiency,
                    "oos_pnl": wf_result.oos_total_pnl,
                    "oos_profit_factor": wf_result.oos_profit_factor,
                    "oos_win_rate": wf_result.oos_win_rate,
                    "oos_trade_count": wf_result.oos_trade_count,
                }

                if (best_overall is None or
                        entry["oos_profit_factor"] > best_overall["oos_profit_factor"]):
                    best_overall = entry

    if best_overall is None:
        return None

    duration = _time.time() - start_time

    result.best_stop_pct = best_overall["stop_pct"]
    result.best_tp_pct = best_overall["tp_pct"]
    result.best_indicators = best_overall["indicators"]
    result.wf_efficiency = best_overall["wf_efficiency"]
    result.oos_pnl = best_overall["oos_pnl"]
    result.oos_profit_factor = best_overall["oos_profit_factor"]
    result.oos_win_rate = best_overall["oos_win_rate"]
    result.oos_trade_count = best_overall["oos_trade_count"]
    result.combos_tested = indicator_combos_tested + phase2_count
    result.duration_seconds = round(duration, 1)
    result.top_5 = [
        {"settings": c.settings, "oos_pnl": c.oos_pnl,
         "oos_profit_factor": c.oos_profit_factor}
        for c in top_combos
    ]

    return result
