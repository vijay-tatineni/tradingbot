"""
backtest/report.py — Console output, text report, and matplotlib charts.

Displays walk-forward results in a formatted table, saves a text report,
generates equity curve and efficiency charts, and persists results to
backtest.db for run-over-run comparison.
"""

import os
from datetime import datetime
from pathlib import Path

from backtest.database import get_connection, store_wf_result
from backtest.walk_forward import WalkForwardResult

RESULTS_DIR = Path(__file__).parent / "results"


def _verdict_icon(verdict: str) -> str:
    icons = {
        "robust": "Robust",
        "marginal": "Marginal",
        "overfit": "Overfit",
        "no_edge": "No edge",
    }
    return icons.get(verdict, verdict)


def _fmt_pnl(val: float) -> str:
    if val >= 0:
        return f"${val:,.0f}"
    return f"-${abs(val):,.0f}"


def generate_report(
    results: list[WalkForwardResult],
    profile: str,
    train_months: int,
    test_months: int,
    total_instruments: int,
    enabled_count: int,
    fresh_download: bool,
    instruments: list[dict] | None = None,
) -> str:
    """
    Generate console output, text report, and charts.
    Returns the path to the saved text report.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M")

    # Sort by OOS P&L descending
    results.sort(key=lambda r: r.oos_total_pnl, reverse=True)

    # Build the report text
    lines = []
    sep = "=" * 75

    lines.append(sep)
    lines.append(f"  Walk-Forward Report -- {now.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"  Profile: {profile} | Train: {train_months} months | "
                 f"Test: {test_months} months")
    disabled = total_instruments - enabled_count
    lines.append(f"  Instruments: {total_instruments} "
                 f"({enabled_count} enabled, {disabled} disabled)")
    lines.append(f"  Data: {'fresh download from IBKR' if fresh_download else 'cached'}")
    lines.append(sep)
    lines.append("")

    # Table header
    header = (f"{'Symbol':<8}| {'TF':<6}| {'IS P&L':>10} | {'OOS P&L':>10} | "
              f"{'WF Ratio':>8} | {'Stop%':>5} | {'TP%':>5} | "
              f"{'Trades':>6} | Verdict")
    divider = (f"{'':->8}+{'':->7}+{'':->12}+{'':->12}+"
               f"{'':->10}+{'':->7}+{'':->7}+{'':->8}+{'':->12}")
    lines.append(header)
    lines.append(divider)

    for r in results:
        wf_str = f"{r.wf_efficiency:.2f}" if r.wf_efficiency > 0 else "N/A"
        stop_str = f"{r.best_stop_pct:.1f}" if r.best_stop_pct > 0 else "--"
        tp_str = f"{r.best_tp_pct:.1f}" if r.best_tp_pct > 0 else "--"

        line = (f"{r.symbol:<8}| {r.timeframe:<6}| {_fmt_pnl(r.is_total_pnl):>10} | "
                f"{_fmt_pnl(r.oos_total_pnl):>10} | {wf_str:>8} | "
                f"{stop_str:>5} | {tp_str:>5} | "
                f"{r.oos_trade_count:>6} | {_verdict_icon(r.verdict)}")
        lines.append(line)

    # Summary counts
    verdicts = {"robust": 0, "marginal": 0, "overfit": 0, "no_edge": 0}
    for r in results:
        verdicts[r.verdict] = verdicts.get(r.verdict, 0) + 1

    lines.append("")
    lines.append(sep)
    lines.append("  Summary")
    lines.append(sep)
    lines.append(f"  Robust:    {verdicts['robust']:>2} instruments -- "
                 f"safe for live trading")
    lines.append(f"  Marginal:  {verdicts['marginal']:>2} instruments -- "
                 f"monitor closely, consider tighter sizing")
    lines.append(f"  Overfit:   {verdicts['overfit']:>2} instruments -- "
                 f"do NOT trade live, params are unreliable")
    lines.append(f"  No edge:   {verdicts['no_edge']:>2} instruments -- "
                 f"no consistent signal, disable")

    # Parameter recommendations vs current config
    if instruments:
        lines.append("")
        lines.append("  Parameter recommendations vs current instruments.json:")
        inst_map = {i["symbol"]: i for i in instruments}
        for r in results:
            if r.symbol in inst_map:
                inst = inst_map[r.symbol]
                curr_stop = inst.get("trail_stop_pct", 0)
                curr_tp = inst.get("take_profit_pct", 0)
                if curr_stop > 0 and curr_tp > 0:
                    if (abs(curr_stop - r.best_stop_pct) < 0.01
                            and abs(curr_tp - r.best_tp_pct) < 0.01):
                        lines.append(f"  {r.symbol}: current {curr_stop}%/{curr_tp}% "
                                     f"matches WF optimal")
                    elif r.verdict in ("robust", "marginal"):
                        lines.append(
                            f"  {r.symbol}: current {curr_stop}%/{curr_tp}% -- "
                            f"WF suggests {r.best_stop_pct}%/{r.best_tp_pct}%, "
                            f"consider updating"
                        )
                    else:
                        lines.append(
                            f"  {r.symbol}: current {curr_stop}%/{curr_tp}% -- "
                            f"{r.verdict}, WF results unreliable"
                        )

    report_text = "\n".join(lines)

    # Print to console
    print(f"\n{report_text}")

    # Save text report
    report_path = RESULTS_DIR / f"wf_report_{timestamp}.txt"
    report_path.write_text(report_text)
    print(f"\nReport saved: {report_path}")

    # Generate charts
    try:
        _generate_charts(results, timestamp)
    except Exception as e:
        print(f"Chart generation failed (matplotlib may not be installed): {e}")

    # Persist to database
    _persist_results(results, now, train_months, test_months)

    return str(report_path)


def _generate_charts(results: list[WalkForwardResult], timestamp: str) -> None:
    """Generate matplotlib charts: equity curves, WF efficiency bars, param heatmap."""
    import matplotlib
    matplotlib.use("Agg")  # Non-interactive backend
    import matplotlib.pyplot as plt
    import numpy as np

    # Filter to results that have steps
    valid = [r for r in results if r.steps]
    if not valid:
        return

    fig, axes = plt.subplots(3, 1, figsize=(14, 18))
    fig.suptitle(f"Walk-Forward Analysis — {timestamp}", fontsize=14, fontweight="bold")

    # Chart 1: OOS equity curves (top 10 by OOS P&L)
    ax1 = axes[0]
    top_n = sorted(valid, key=lambda r: r.oos_total_pnl, reverse=True)[:10]
    for r in top_n:
        cum_pnl = []
        running = 0.0
        for s in r.steps:
            running += s.oos_pnl
            cum_pnl.append(running)
        ax1.plot(range(1, len(cum_pnl) + 1), cum_pnl,
                 marker="o", label=f"{r.symbol} ({r.timeframe})")
    ax1.axhline(y=0, color="gray", linestyle="--", alpha=0.5)
    ax1.set_xlabel("Walk-Forward Step")
    ax1.set_ylabel("Cumulative OOS P&L ($)")
    ax1.set_title("Out-of-Sample Equity Curves (Top 10)")
    ax1.legend(fontsize=8, ncol=2)
    ax1.grid(True, alpha=0.3)

    # Chart 2: WF efficiency bar chart
    ax2 = axes[1]
    sorted_results = sorted(valid, key=lambda r: r.wf_efficiency, reverse=True)
    names = [f"{r.symbol}" for r in sorted_results]
    efficiencies = [r.wf_efficiency for r in sorted_results]
    colors = []
    for e in efficiencies:
        if e > 0.5:
            colors.append("#2ecc71")  # green
        elif e >= 0.3:
            colors.append("#f39c12")  # amber
        else:
            colors.append("#e74c3c")  # red
    bars = ax2.bar(names, efficiencies, color=colors)
    ax2.axhline(y=0.5, color="green", linestyle="--", alpha=0.7, label="Robust (0.5)")
    ax2.axhline(y=0.3, color="orange", linestyle="--", alpha=0.7, label="Marginal (0.3)")
    ax2.set_ylabel("WF Efficiency (OOS PF / IS PF)")
    ax2.set_title("Walk-Forward Efficiency by Instrument")
    ax2.legend(fontsize=8)
    ax2.tick_params(axis="x", rotation=45)
    ax2.grid(True, alpha=0.3, axis="y")

    # Chart 3: Parameter stability heatmap
    ax3 = axes[2]
    # Build matrix: rows = instruments, columns = steps, values = stop_pct*100 + tp_pct
    max_steps = max(len(r.steps) for r in valid)
    n_inst = min(len(valid), 15)
    matrix = np.full((n_inst, max_steps), np.nan)
    y_labels = []
    for i, r in enumerate(valid[:n_inst]):
        y_labels.append(r.symbol)
        for j, s in enumerate(r.steps):
            # Encode both params into one value for visual distinction
            matrix[i, j] = s.best_stop_pct * 10 + s.best_tp_pct
    im = ax3.imshow(matrix, aspect="auto", cmap="viridis", interpolation="nearest")
    ax3.set_yticks(range(n_inst))
    ax3.set_yticklabels(y_labels, fontsize=8)
    ax3.set_xlabel("Walk-Forward Step")
    ax3.set_title("Parameter Stability (colour = stop%*10 + TP%)")
    fig.colorbar(im, ax=ax3, shrink=0.6)

    # Annotate cells with actual param values
    for i in range(n_inst):
        for j in range(len(valid[i].steps)):
            s = valid[i].steps[j]
            ax3.text(j, i, f"{s.best_stop_pct}/{s.best_tp_pct}",
                     ha="center", va="center", fontsize=6, color="white")

    plt.tight_layout()
    chart_path = RESULTS_DIR / f"wf_charts_{timestamp}.png"
    plt.savefig(str(chart_path), dpi=150)
    plt.close()
    print(f"Charts saved: {chart_path}")


def _persist_results(results: list[WalkForwardResult], run_date: datetime,
                     train_months: int, test_months: int) -> None:
    """Write all results to wf_results table in backtest.db."""
    conn = get_connection()
    date_str = run_date.strftime("%Y-%m-%d %H:%M")

    for r in results:
        store_wf_result(conn, {
            "run_date": date_str,
            "symbol": r.symbol,
            "timeframe": r.timeframe,
            "is_pnl": r.is_total_pnl,
            "is_profit_factor": r.is_avg_profit_factor,
            "is_win_rate": 0,
            "is_trade_count": sum(s.is_trade_count for s in r.steps),
            "oos_pnl": r.oos_total_pnl,
            "oos_profit_factor": r.oos_profit_factor,
            "oos_win_rate": r.oos_win_rate,
            "oos_trade_count": r.oos_trade_count,
            "wf_efficiency": r.wf_efficiency,
            "best_stop_pct": r.best_stop_pct,
            "best_tp_pct": r.best_tp_pct,
            "param_stability": r.param_stability,
            "verdict": r.verdict,
            "train_months": train_months,
            "test_months": test_months,
        })

    conn.close()
    print(f"Results persisted to backtest.db ({len(results)} rows)")
