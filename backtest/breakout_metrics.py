"""
backtest/breakout_metrics.py — Frozen §14 Commit-B metric definitions.

Pure functions over a list of closed trades (each with .symbol and .pnl_usd, net
of commission/spread/slippage/FX, and INCLUDING END_OF_TEST_LIQUIDATION exits).
Defined and frozen at the implementation lock (Commit B) so two correct
implementations agree on the Phase 3b numbers.

OUTCOME METRICS — by pre-registration these are NOT computed in Phase 3a
(development period). They are exercised only by synthetic unit tests until 3b.
"""


def profit_factor(trades) -> float:
    """sum(positive net P&L) / |sum(negative net P&L)|, over ALL closed trades
    incl. END_OF_TEST_LIQUIDATION. inf if there are no losing trades."""
    pos = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
    neg = abs(sum(t.pnl_usd for t in trades if t.pnl_usd < 0))
    if neg == 0:
        return float("inf") if pos > 0 else 0.0
    return pos / neg


def instrument_concentration(trades) -> dict:
    """Per-instrument contribution = max(0, net P&L for instrument) ÷
    sum over instruments of max(0, net P&L). Bounded in [0,1], interpretable even
    when some instruments lose (losers contribute 0). Empty dict if no positive
    instrument exists."""
    net = {}
    for t in trades:
        net[t.symbol] = net.get(t.symbol, 0.0) + t.pnl_usd
    pos_net = {s: (v if v > 0 else 0.0) for s, v in net.items()}
    denom = sum(pos_net.values())
    if denom == 0:
        return {s: 0.0 for s in net}
    return {s: pos_net[s] / denom for s in net}


def remove_top_five(trades):
    """ARITHMETIC top-five removal (§14): from the ORIGINAL completed run, rank
    closed trades by net P&L and remove the five largest net winners by
    subtraction. Does NOT rerun the portfolio (a rerun would change slot
    availability/sizing). Returns (original_total, adjusted_total, removed_trades).
    """
    total = sum(t.pnl_usd for t in trades)
    ranked = sorted(trades, key=lambda t: t.pnl_usd, reverse=True)
    removed = [t for t in ranked[:5] if t.pnl_usd > 0]      # only actual winners
    adjusted = total - sum(t.pnl_usd for t in removed)
    return round(total, 2), round(adjusted, 2), removed
