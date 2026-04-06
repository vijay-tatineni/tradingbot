"""
backtest/simple_backtest.py — Simple backtest mode.

Runs the bot's signal engine with fixed stop%/TP% over the full dataset,
shows every individual trade, and produces summary stats + equity charts.

Reuses offline_signals.generate_signals() and simulator.simulate_trades()
— no new signal or simulation logic.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtest.offline_signals import generate_signals
from backtest.simulator import simulate_trades, summarise, TradeResult, SimulationSummary

RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class BacktestResult:
    """Full backtest output for one instrument."""
    symbol: str
    timeframe: str
    currency: str
    stop_pct: float
    tp_pct: float
    qty: int
    bar_count: int
    date_start: str
    date_end: str
    trades: list[TradeResult]
    summary: SimulationSummary
    enabled: bool


def run_simple_backtest(
    symbol: str,
    df: pd.DataFrame,
    stop_pct: float,
    tp_pct: float,
    indicator_settings: dict,
    instrument_config: dict,
) -> BacktestResult | None:
    """
    Run the bot's signal engine with fixed params over the full dataset.
    Returns BacktestResult with individual trades and summary stats.
    """
    if df.empty:
        return None

    qty = instrument_config.get("qty", 1)
    long_only = instrument_config.get("long_only", True)
    currency = instrument_config.get("currency", "USD")
    timeframe = instrument_config.get("timeframe", "daily")
    enabled = instrument_config.get("enabled", True)

    print(f"  Generating signals...", end=" ", flush=True)
    signals = generate_signals(df, indicator_settings, symbol)
    print(f"{len(signals)} signals")

    print(f"  Simulating trades (stop={stop_pct}%, TP={tp_pct}%)...", end=" ", flush=True)
    trades = simulate_trades(
        signals, df,
        stop_pct=stop_pct,
        tp_pct=tp_pct,
        qty=qty,
        long_only=long_only,
        currency=currency,
    )
    summary = summarise(trades)
    print(f"{summary.trade_count} trades")

    return BacktestResult(
        symbol=symbol,
        timeframe=timeframe,
        currency=currency,
        stop_pct=stop_pct,
        tp_pct=tp_pct,
        qty=qty,
        bar_count=len(df),
        date_start=str(df["datetime"].iloc[0].date()),
        date_end=str(df["datetime"].iloc[-1].date()),
        trades=trades,
        summary=summary,
        enabled=enabled,
    )


# ── Currency-aware formatting helpers ──────────────────────────────

def _cur(currency: str) -> str:
    """Currency symbol for display."""
    return "\u00a3" if currency == "GBP" else "$"


def _fmt_pnl(val: float, currency: str) -> str:
    c = _cur(currency)
    if val >= 0:
        return f"{c}{val:,.2f}"
    return f"-{c}{abs(val):,.2f}"


def _fmt_pnl_int(val: float, currency: str) -> str:
    c = _cur(currency)
    if val >= 0:
        return f"{c}{val:,.0f}"
    return f"-{c}{abs(val):,.0f}"


def _fmt_price(val: float, currency: str) -> str:
    if currency == "GBP":
        return f"{val:.2f}p"
    return f"${val:.2f}"


def _fmt_result(outcome: str) -> str:
    if outcome == "win":
        return "TP"
    elif outcome == "loss":
        return "SL"
    return "OPEN"


# ── Single-instrument trade list ───────────────────────────────────

def format_trade_list(result: BacktestResult) -> str:
    """Format the detailed trade-by-trade report for one instrument."""
    lines = []
    sep = "=" * 75
    cur = result.currency

    lines.append(sep)
    lines.append(f"  Backtest Report -- {result.symbol} ({result.timeframe}) "
                 f"-- Stop: {result.stop_pct}% / TP: {result.tp_pct}%")
    lines.append(f"  Period: {result.date_start} to {result.date_end} "
                 f"({result.bar_count} bars) | Qty: {result.qty}")
    lines.append(sep)
    lines.append("")

    # Trade table
    header = (f" {'#':>3}  | {'Direction':<9} | {'Entry Date':<20} | "
              f"{'Entry':>10} | {'Exit Date':<20} | {'Exit':>10} | "
              f"{'P&L':>10} | {'Bars':>4} | Result")
    divider = (f"{'':->5}+{'':->11}+{'':->22}+{'':->12}+"
               f"{'':->22}+{'':->12}+{'':->12}+{'':->6}+{'':->8}")
    lines.append(header)
    lines.append(divider)

    for i, t in enumerate(result.trades, 1):
        # Truncate datetime to readable format
        entry_dt = t.entry_date[:19] if len(t.entry_date) > 19 else t.entry_date
        exit_dt = t.exit_date[:19] if len(t.exit_date) > 19 else t.exit_date

        line = (f" {i:>3}  | {t.direction:<9} | {entry_dt:<20} | "
                f"{_fmt_price(t.entry_price, cur):>10} | {exit_dt:<20} | "
                f"{_fmt_price(t.exit_price, cur):>10} | "
                f"{_fmt_pnl(t.pnl, cur):>10} | {t.holding_bars:>4} | "
                f"{_fmt_result(t.outcome)}")
        lines.append(line)

    # Summary
    s = result.summary
    lines.append("")
    lines.append(sep)
    lines.append(f"  Summary -- {result.symbol}")
    lines.append(sep)
    lines.append(f"  Total trades:    {s.trade_count}")
    lines.append(f"  Wins / Losses:   {s.win_count} / {s.loss_count}")
    lines.append(f"  Win rate:        {s.win_rate * 100:.1f}%")
    lines.append(f"  Total P&L:       {_fmt_pnl(s.total_pnl, cur)}")
    pf_str = f"{s.profit_factor:.2f}" if s.profit_factor < 100 else ">99"
    lines.append(f"  Profit factor:   {pf_str}")
    lines.append(f"  Max drawdown:    {_fmt_pnl(-s.max_drawdown, cur)}")
    lines.append(f"  Avg win:         {_fmt_pnl(s.avg_win_pnl, cur)}")
    lines.append(f"  Avg loss:        {_fmt_pnl(-s.avg_loss_pnl, cur)}")
    lines.append(f"  Avg holding:     {s.avg_holding_bars:.0f} bars")

    if result.trades:
        best = max(result.trades, key=lambda t: t.pnl)
        worst = min(result.trades, key=lambda t: t.pnl)
        lines.append(f"  Best trade:      {_fmt_pnl(best.pnl, cur)} "
                     f"({best.entry_date[:10]}, held {best.holding_bars} bars)")
        lines.append(f"  Worst trade:     {_fmt_pnl(worst.pnl, cur)} "
                     f"({worst.entry_date[:10]}, held {worst.holding_bars} bars)")

    return "\n".join(lines)


# ── Multi-instrument summary table ─────────────────────────────────

def format_summary_table(results: list[BacktestResult], params_source: str) -> str:
    """Format the multi-instrument summary table."""
    lines = []
    sep = "=" * 75

    # Sort by P&L descending
    results = sorted(results, key=lambda r: r.summary.total_pnl, reverse=True)

    lines.append(sep)
    lines.append(f"  Backtest Summary -- All Instruments")
    if results:
        lines.append(f"  Period: {results[0].date_start} to {results[0].date_end} "
                     f"| {params_source}")
    lines.append(sep)
    lines.append("")

    header = (f"{'Symbol':<8}| {'TF':<6}| {'Stop%':>5} | {'TP%':>5} | "
              f"{'Trades':>6} | {'Win%':>5} | {'P&L':>10} | "
              f"{'PF':>6} | {'Max DD':>10} | Status")
    divider = (f"{'':->8}+{'':->7}+{'':->7}+{'':->7}+"
               f"{'':->8}+{'':->7}+{'':->12}+"
               f"{'':->8}+{'':->12}+{'':->10}")
    lines.append(header)
    lines.append(divider)

    total_pnl_usd = 0.0
    for r in results:
        s = r.summary
        pf_str = f"{s.profit_factor:.2f}" if s.profit_factor < 100 else ">99"
        wr_str = f"{s.win_rate * 100:.0f}%" if s.trade_count > 0 else "--"
        status = "Enabled" if r.enabled else "Disabled"

        line = (f"{r.symbol:<8}| {r.timeframe:<6}| {r.stop_pct:>5.1f} | "
                f"{r.tp_pct:>5.1f} | {s.trade_count:>6} | {wr_str:>5} | "
                f"{_fmt_pnl_int(s.total_pnl, r.currency):>10} | "
                f"{pf_str:>6} | {_fmt_pnl_int(-s.max_drawdown, r.currency):>10} | "
                f"{status}")
        lines.append(line)
        total_pnl_usd += s.total_pnl  # approximate, mixed currencies

    lines.append("")
    lines.append(f"  Total P&L: ${total_pnl_usd:,.0f} (across all instruments, "
                 f"mixed currencies approximate)")

    return "\n".join(lines)


# ── Report generation ──────────────────────────────────────────────

def generate_backtest_report(
    results: list[BacktestResult],
    profile: str,
    single_symbol: bool = False,
) -> str:
    """
    Print reports to console, save text file, generate charts.
    Returns path to saved text report.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M")

    report_parts = []

    # Print individual trade lists
    for r in results:
        trade_text = format_trade_list(r)
        print(f"\n{trade_text}")
        report_parts.append(trade_text)

    # Multi-instrument summary
    if len(results) > 1:
        params_source = "Using current instruments.json params"
        summary_text = format_summary_table(results, params_source)
        print(f"\n{summary_text}")
        report_parts.append("")
        report_parts.append(summary_text)

    full_report = "\n".join(report_parts)

    # Save text report
    report_path = RESULTS_DIR / f"bt_report_{timestamp}.txt"
    report_path.write_text(full_report)
    print(f"\nReport saved: {report_path}")

    # Generate charts
    try:
        _generate_backtest_charts(results, timestamp, single_symbol)
    except Exception as e:
        print(f"Chart generation failed: {e}")

    return str(report_path)


def _generate_backtest_charts(
    results: list[BacktestResult],
    timestamp: str,
    single_symbol: bool,
) -> None:
    """Generate equity curve charts for backtest results."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    valid = [r for r in results if r.trades]
    if not valid:
        return

    if single_symbol or len(valid) == 1:
        # Single instrument: one chart with trade markers
        r = valid[0]
        fig, ax = plt.subplots(1, 1, figsize=(14, 6))
        fig.suptitle(f"Backtest: {r.symbol} ({r.timeframe}) -- "
                     f"Stop {r.stop_pct}% / TP {r.tp_pct}%",
                     fontsize=13, fontweight="bold")

        cum_pnl = []
        dates = []
        colors = []
        running = 0.0
        for t in r.trades:
            running += t.pnl
            cum_pnl.append(running)
            dates.append(t.exit_date[:10])
            colors.append("#2ecc71" if t.pnl > 0 else "#e74c3c")

        ax.plot(range(len(cum_pnl)), cum_pnl, color="#3498db", linewidth=1.5)
        ax.scatter(range(len(cum_pnl)), cum_pnl, c=colors, s=30, zorder=5)
        ax.axhline(y=0, color="gray", linestyle="--", alpha=0.5)
        ax.set_xlabel("Trade #")
        c = _cur(r.currency)
        ax.set_ylabel(f"Cumulative P&L ({c})")
        ax.set_title(f"{r.summary.trade_count} trades | "
                     f"P&L: {_fmt_pnl(r.summary.total_pnl, r.currency)} | "
                     f"PF: {r.summary.profit_factor:.2f} | "
                     f"Win: {r.summary.win_rate * 100:.0f}%")
        ax.grid(True, alpha=0.3)

    else:
        # Multi-instrument: subplots + combined
        n = len(valid) + 1  # +1 for combined
        cols = 3
        rows = (n + cols - 1) // cols
        fig, axes = plt.subplots(rows, cols, figsize=(16, 4 * rows))
        fig.suptitle(f"Backtest Equity Curves -- {timestamp}",
                     fontsize=14, fontweight="bold")
        axes_flat = axes.flatten() if hasattr(axes, "flatten") else [axes]

        for idx, r in enumerate(valid):
            ax = axes_flat[idx]
            cum_pnl = []
            running = 0.0
            for t in r.trades:
                running += t.pnl
                cum_pnl.append(running)
            colors_line = ["#2ecc71" if t.pnl > 0 else "#e74c3c" for t in r.trades]
            ax.plot(range(len(cum_pnl)), cum_pnl, color="#3498db", linewidth=1)
            ax.scatter(range(len(cum_pnl)), cum_pnl, c=colors_line, s=15, zorder=5)
            ax.axhline(y=0, color="gray", linestyle="--", alpha=0.5)
            c = _cur(r.currency)
            pf_str = f"{r.summary.profit_factor:.1f}" if r.summary.profit_factor < 100 else ">99"
            ax.set_title(f"{r.symbol} | {_fmt_pnl_int(r.summary.total_pnl, r.currency)} "
                         f"PF {pf_str}", fontsize=9)
            ax.tick_params(labelsize=7)
            ax.grid(True, alpha=0.3)

        # Combined equity curve in last subplot
        ax_combined = axes_flat[len(valid)]
        for r in valid:
            cum_pnl = []
            running = 0.0
            for t in r.trades:
                running += t.pnl
                cum_pnl.append(running)
            ax_combined.plot(range(len(cum_pnl)), cum_pnl, label=r.symbol, linewidth=1)
        ax_combined.axhline(y=0, color="gray", linestyle="--", alpha=0.5)
        ax_combined.set_title("All Instruments", fontsize=9)
        ax_combined.legend(fontsize=6, ncol=3)
        ax_combined.grid(True, alpha=0.3)

        # Hide unused subplots
        for idx in range(len(valid) + 1, len(axes_flat)):
            axes_flat[idx].set_visible(False)

    plt.tight_layout()
    chart_path = RESULTS_DIR / f"bt_chart_{timestamp}.png"
    plt.savefig(str(chart_path), dpi=150)
    plt.close()
    print(f"Charts saved: {chart_path}")
