"""No-leverage cash constraint (§2.5). Five 20%-notional positions plus
commissions cannot create leverage; the cash cap binds and cash stays >= 0."""
from backtest.breakout_sim import run_breakout_portfolio, MAX_OPEN_POSITIONS
from backtest.simulator import CostConfig
from tests.breakout_helpers import make_inst, bar


def _comm_only():
    # Commission but zero spread/slippage so entry fills land exactly on the open.
    return CostConfig(commission_per_share=0.005, commission_min=1.0,
                      commission_max_pct=1.0, half_spread_bps=0.0,
                      slippage_bps=0.0, gap_fill_at_open=True)


def _entrant(sym, idx):
    # tiny ATR -> risk/heat caps are huge; the 20% notional cap (and then cash)
    # binds. price 100, equity 100k -> notional cap = 200 shares.
    return make_inst(sym, idx, [
        bar("2025-01-02", 99, 100, 98, 99, atr14=0.1, entry_signal=True),
        bar("2025-01-03", 100, 101, 99.9, 100, atr14=0.1),   # low 99.9 > stop 99.8
    ], cost=_comm_only())


def test_five_positions_no_leverage_cash_binds():
    insts = [_entrant(f"S{i}", i) for i in range(6)]   # 6 compete, cap is 5
    res = run_breakout_portfolio(insts, initial_capital_usd=100_000.0,
                                 liquidate_at_end=False)

    assert len(res.entries) == MAX_OPEN_POSITIONS           # exactly 5 admitted
    assert any(b.reason == "cap" for b in res.blocked)      # 6th blocked by cap

    # No leverage: total deployed notional never exceeds starting capital.
    total_notional = sum(e.qty * e.entry_fill for e in res.entries)  # USD (pdiv=fx=1)
    assert total_notional <= 100_000.0

    # The last admitted position is constrained by cash (earlier ones ate it).
    assert res.entries[-1].binding == "cash"
    assert res.entries[-1].qty < res.entries[0].qty         # cash-squeezed below notional


def test_skip_when_quantity_below_one():
    # Equity so small that even one share's notional exceeds the 20% cap -> skip.
    inst = make_inst("X", 0, [
        bar("2025-01-02", 99, 100, 98, 99, atr14=0.1, entry_signal=True),
        bar("2025-01-03", 1000, 1001, 999, 1000, atr14=0.1),
    ], cost=CostConfig.zero())
    res = run_breakout_portfolio([inst], initial_capital_usd=1000.0,
                                 liquidate_at_end=False)
    # 20% of 1000 = 200 < price 1000 -> qty_notional 0 -> no entry.
    assert len(res.entries) == 0
