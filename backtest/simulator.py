"""
backtest/simulator.py — Simulate trades from signals against OHLCV data.

For each signal, scans forward through bars to determine if the trade
hits stop-loss, take-profit, or remains open at end of data.
No slippage, no commissions — this is parameter validation, not execution sim.
"""

from dataclasses import dataclass

import pandas as pd

from backtest.offline_signals import Signal


@dataclass
class TradeResult:
    """Outcome of a single simulated trade."""
    symbol: str
    direction: str        # "BUY" or "SELL"
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    pnl: float
    pnl_pct: float
    holding_bars: int
    outcome: str          # "win" (hit TP), "loss" (hit SL), "open" (end of data)
    stop_pct: float
    tp_pct: float


@dataclass
class SimulationSummary:
    """Aggregate statistics across all trades."""
    total_pnl: float
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: float          # win_count / trade_count
    profit_factor: float     # gross_profit / gross_loss (inf if no losses)
    max_drawdown: float
    avg_holding_bars: float
    avg_win_pnl: float
    avg_loss_pnl: float


def simulate_trades(
    signals: list[Signal],
    df: pd.DataFrame,
    stop_pct: float,
    tp_pct: float,
    qty: int = 1,
    long_only: bool = True,
    currency: str = "USD",
) -> list[TradeResult]:
    """
    Simulate each signal as a trade with fixed stop% and TP%.

    For BUY signals:
        stop_price = entry * (1 - stop_pct/100)
        tp_price   = entry * (1 + tp_pct/100)
        Scan forward: low <= stop → LOSS; high >= tp → WIN
        Both in same bar → conservative: LOSS

    For SELL signals (only if long_only=False):
        stop_price = entry * (1 + stop_pct/100)
        tp_price   = entry * (1 - tp_pct/100)
        Loss if high >= stop; win if low <= tp

    GBP instruments: LSE stocks are quoted in pence. P&L is calculated in
    pence then divided by 100 to convert to pounds, matching the live bot's
    logic in bot/portfolio.py and bot/layer3_silver.py.
    """
    # GBP pence→pounds divisor (LSE quotes in pence, P&L needs pounds)
    pence_divisor = 100.0 if currency == "GBP" else 1.0

    trades = []

    for sig in signals:
        if sig.direction == "SELL" and long_only:
            continue

        entry_price = sig.price
        entry_idx = sig.bar_index

        if sig.direction == "BUY":
            stop_price = entry_price * (1 - stop_pct / 100)
            tp_price = entry_price * (1 + tp_pct / 100)
        else:  # SELL
            stop_price = entry_price * (1 + stop_pct / 100)
            tp_price = entry_price * (1 - tp_pct / 100)

        outcome = "open"
        exit_price = entry_price
        exit_date = str(df.iloc[-1]["datetime"])
        holding_bars = len(df) - entry_idx - 1

        # Scan forward from the bar AFTER the signal
        for j in range(entry_idx + 1, len(df)):
            bar = df.iloc[j]

            if sig.direction == "BUY":
                hit_stop = bar["low"] <= stop_price
                hit_tp = bar["high"] >= tp_price
            else:  # SELL
                hit_stop = bar["high"] >= stop_price
                hit_tp = bar["low"] <= tp_price

            if hit_stop and hit_tp:
                # Both hit in same bar — assume LOSS (conservative)
                outcome = "loss"
                exit_price = stop_price
                exit_date = str(bar["datetime"])
                holding_bars = j - entry_idx
                break
            elif hit_stop:
                outcome = "loss"
                exit_price = stop_price
                exit_date = str(bar["datetime"])
                holding_bars = j - entry_idx
                break
            elif hit_tp:
                outcome = "win"
                exit_price = tp_price
                exit_date = str(bar["datetime"])
                holding_bars = j - entry_idx
                break

        if outcome == "open":
            exit_price = float(df.iloc[-1]["close"])
            exit_date = str(df.iloc[-1]["datetime"])
            holding_bars = len(df) - entry_idx - 1

        # Calculate P&L
        # Raw P&L in price units (pence for GBP, dollars for USD)
        if sig.direction == "BUY":
            raw_pnl = (exit_price - entry_price) * qty
        else:
            raw_pnl = (entry_price - exit_price) * qty
        # Convert pence → pounds for GBP instruments
        pnl = raw_pnl / pence_divisor
        pnl_pct = ((exit_price - entry_price) / entry_price * 100
                    if sig.direction == "BUY"
                    else (entry_price - exit_price) / entry_price * 100)

        trades.append(TradeResult(
            symbol=sig.symbol,
            direction=sig.direction,
            entry_date=sig.datetime,
            entry_price=entry_price,
            exit_date=exit_date,
            exit_price=round(exit_price, 4),
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 2),
            holding_bars=holding_bars,
            outcome=outcome,
            stop_pct=stop_pct,
            tp_pct=tp_pct,
        ))

    return trades


def summarise(trades: list[TradeResult]) -> SimulationSummary:
    """Calculate aggregate statistics from a list of trade results."""
    if not trades:
        return SimulationSummary(
            total_pnl=0, trade_count=0, win_count=0, loss_count=0,
            win_rate=0, profit_factor=0, max_drawdown=0,
            avg_holding_bars=0, avg_win_pnl=0, avg_loss_pnl=0,
        )

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]
    total_pnl = sum(t.pnl for t in trades)
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))

    # Max drawdown from cumulative P&L curve
    cum_pnl = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cum_pnl += t.pnl
        if cum_pnl > peak:
            peak = cum_pnl
        dd = peak - cum_pnl
        if dd > max_dd:
            max_dd = dd

    return SimulationSummary(
        total_pnl=round(total_pnl, 2),
        trade_count=len(trades),
        win_count=len(wins),
        loss_count=len(losses),
        win_rate=round(len(wins) / len(trades), 4) if trades else 0,
        profit_factor=round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf"),
        max_drawdown=round(max_dd, 2),
        avg_holding_bars=round(sum(t.holding_bars for t in trades) / len(trades), 1),
        avg_win_pnl=round(gross_profit / len(wins), 2) if wins else 0,
        avg_loss_pnl=round(gross_loss / len(losses), 2) if losses else 0,
    )
