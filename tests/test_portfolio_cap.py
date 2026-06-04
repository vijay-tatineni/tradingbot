"""
Synthetic, deterministic proof that the portfolio loop's two drop-rules fire:
  1. cap-blocking: with a 5-cap and 6 instruments all signalling at once, the
     6th (last in instruments.json order) is dropped and counted blocked_by_cap.
  2. exits-before-entries within a tie: a slot freed at timestamp T is reused by
     a later instrument at the same T.

No DB, no real data — hand-built bars so the outcome is fully determined.
"""
import pandas as pd

from backtest.offline_signals import Signal
from backtest.portfolio_sim import (
    InstrumentSpec, PortfolioConfig, run_portfolio,
)
from backtest.simulator import CostConfig


def _flat_df(timestamps, price=100.0):
    """Bars that never move — a position opened here never hits stop/TP, so it
    stays open and holds its slot until end of data (unless we force an exit)."""
    return pd.DataFrame({
        "datetime": pd.to_datetime(timestamps),
        "open": [price] * len(timestamps),
        "high": [price] * len(timestamps),
        "low": [price] * len(timestamps),
        "close": [price] * len(timestamps),
    })


def _spec(symbol, order_idx, df, signal_bars, stop=5.0, tp=10.0):
    sigs = [Signal(datetime=str(df["datetime"].iloc[b]), bar_index=b,
                   price=float(df["close"].iloc[b]), symbol=symbol,
                   direction="BUY", indicators={})
            for b in signal_bars]
    return InstrumentSpec(
        symbol=symbol, order_idx=order_idx, df=df, signals=sigs,
        stop_pct=stop, tp_pct=tp, qty=1, long_only=True, currency="USD",
        timeframe="daily", cost_config=CostConfig.zero(),
    )


def test_cap_blocks_sixth_simultaneous_signal():
    # 6 instruments, identical timestamps, all signal on bar 0 (fill at bar 1).
    # Flat bars => none ever exits => only 5 slots, the 6th is dropped.
    ts = ["2024-01-01", "2024-01-02", "2024-01-03"]
    specs = [_spec(f"S{k}", k, _flat_df(ts), signal_bars=[0]) for k in range(6)]
    res = run_portfolio(specs, PortfolioConfig(max_open_positions=5,
                                               target_notional=1000.0))

    # Exactly 5 positions opened, 1 blocked_by_cap, 0 blocked_by_open.
    assert res.peak_concurrent == 5, res.peak_concurrent
    total_cap = sum(res.blocked_by_cap.values())
    total_open = sum(res.blocked_by_open.values())
    assert total_cap == 1, res.blocked_by_cap
    assert total_open == 0, res.blocked_by_open
    # The dropped one is the LAST in instruments.json order (S5) — tie-break.
    assert res.blocked_by_cap["S5"] == 1, res.blocked_by_cap
    assert res.blocked_by_cap["S0"] == 0
    # 5 instruments took a (still-open, marked-out) trade; S5 took none.
    assert len(res.per_symbol_trades["S5"]) == 0
    assert all(len(res.per_symbol_trades[f"S{k}"]) == 1 for k in range(5))


def test_freed_slot_reused_same_timestamp():
    # 5 instruments hold flat-forever positions (fill bar 1, never exit).
    # A 6th (S5) signals on bar 0 -> fill bar 1: blocked_by_cap at T1 (full).
    # One holder (S0) is given bars that DROP through its stop on bar 2, freeing
    # a slot at T2. S5 signals AGAIN on bar 1 -> fill bar 2 (same T2 as S0's
    # exit). Exits-before-entries => S5 takes the freed slot at T2.
    ts = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]

    # S0: flat at 100 until bar 2 where it craters (low 50 < stop), exits.
    s0_df = pd.DataFrame({
        "datetime": pd.to_datetime(ts),
        "open":  [100, 100, 100, 100],
        "high":  [100, 100, 100, 100],
        "low":   [100, 100,  50,  50],   # bar 2 low 50 -> 5% stop (95) hit
        "close": [100, 100,  50,  50],
    })
    holders = [_spec("S0", 0, s0_df, signal_bars=[0])]
    for k in range(1, 5):
        holders.append(_spec(f"S{k}", k, _flat_df(ts), signal_bars=[0]))

    # S5 signals on bar 0 (fill 1, blocked: full) AND bar 1 (fill 2, free slot).
    s5 = _spec("S5", 5, _flat_df(ts), signal_bars=[0, 1])

    res = run_portfolio(holders + [s5],
                        PortfolioConfig(max_open_positions=5, target_notional=1000.0))

    # S5 was blocked once (T1, cap full) then entered at T2 (slot freed by S0).
    assert res.blocked_by_cap["S5"] == 1, res.blocked_by_cap
    assert len(res.per_symbol_trades["S5"]) == 1, res.per_symbol_trades["S5"]
    s5_entry = res.per_symbol_trades["S5"][0].entry_date[:10]
    assert s5_entry == "2024-01-03", s5_entry   # T2 — the freed-slot bar
    # S0 exited at T2 as a loss (stop hit).
    assert res.per_symbol_trades["S0"][0].outcome == "loss"
