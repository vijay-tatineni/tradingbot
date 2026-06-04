"""
tests/test_simulator_costs.py — Fix 2: cost and fill realism.

Covers commission, adverse half-spread + slippage on entry AND exit,
gap-through fills, config-driven cost model, and the all-important
zero-cost reproduction of the costless Fix 1 + Fix 1b numbers.

All expected fills below are hand-computed from the adverse-cost arithmetic
(stated in each test), not read back from the code.
"""

import pytest
import pandas as pd

from backtest.simulator import (
    simulate_trades, CostConfig, classify_instrument,
    _BASE_HALF_SPREAD_BPS, _BASE_SLIPPAGE_BPS,
)
from backtest.offline_signals import Signal


def _df(prices):
    rows = []
    for i, (o, h, l, c) in enumerate(prices):
        rows.append({'datetime': f'2024-01-{i+1:02d}', 'open': o, 'high': h,
                     'low': l, 'close': c, 'volume': 1000})
    return pd.DataFrame(rows)


def _sig(direction='BUY', price=100.0, bar_index=0, symbol='TEST'):
    return Signal(datetime=f'2024-01-{bar_index+1:02d}', bar_index=bar_index,
                  direction=direction, price=price, symbol=symbol, indicators={})


def _spread_only(half=50.0, slip=50.0, gap=False):
    """A cost config with ONLY spread+slippage (no commission). adverse = half
    + slip bps. 50+50 = 100 bps = 1.00% -> clean ×1.01 / ×0.99 arithmetic."""
    return CostConfig(commission_per_share=0.0, commission_min=0.0,
                      commission_max_pct=0.0, half_spread_bps=half,
                      slippage_bps=slip, gap_fill_at_open=gap)


# ── (a) Entry fill is adverse: pay the ask on a buy ─────────────────

def test_entry_fill_is_adverse():
    """BUY entry fills ABOVE the raw open by the full adverse move.
      raw open = 100; adverse = 100 bps = 1% -> entry fill = 100 * 1.01 = 101.0
    """
    df = _df([
        (99, 100, 98, 99),       # bar 0: signal
        (100, 100, 100, 100),    # bar 1: entry @ open 100, flat, last bar
    ])
    trades = simulate_trades([_sig('BUY', 99.0, 0)], df, stop_pct=10.0,
                             tp_pct=10.0, qty=1, trailing_mode=False,
                             cost_config=_spread_only())
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_price == 101.0, "buy must fill above raw open (pay the ask)"
    assert t.entry_price > 100.0


def test_entry_fill_adverse_for_short():
    """SELL entry fills BELOW the raw open (hit the bid).
      raw open = 100; adverse 1% -> 100 * 0.99 = 99.0
    """
    df = _df([
        (101, 102, 100, 101),
        (100, 100, 100, 100),    # entry @ 100, flat
    ])
    trades = simulate_trades([_sig('SELL', 101.0, 0)], df, stop_pct=10.0,
                             tp_pct=10.0, qty=1, long_only=False,
                             trailing_mode=False, cost_config=_spread_only())
    assert trades[0].entry_price == 99.0, "short must fill below raw open (hit the bid)"


# ── (b) Exit fill is adverse: hit the bid on a long's sell ──────────

def test_exit_fill_is_adverse():
    """Long TP exit fills BELOW the TP level by the adverse move.
      entry raw 200 -> fill 200*1.01 = 202; tp_pct 5 -> tp = 202*1.05 = 212.1
      exit is a SELL -> 212.1 * 0.99 = 209.979 (below the 212.1 level)
    """
    df = _df([
        (198, 199, 197, 198),    # bar 0: signal
        (200, 201, 199, 200),    # bar 1: entry @ 200 (fill 202); no trigger
        (205, 213, 204, 210),    # bar 2: high 213 >= tp 212.1 -> win
    ])
    trades = simulate_trades([_sig('BUY', 198.0, 0)], df, stop_pct=5.0,
                             tp_pct=5.0, qty=1, trailing_mode=False,
                             cost_config=_spread_only())
    t = trades[0]
    assert t.entry_price == 202.0
    assert t.outcome == 'win'
    assert t.exit_price == pytest.approx(209.979)
    assert t.exit_price < 212.1, "sell fills below the raw TP level"


# ── (c) Commission deducted per trade ──────────────────────────────

def test_commission_deducted_per_trade():
    """IBKR-style per-order minimum charged on BOTH sides, netted off P&L.
      no spread; entry fill 100, TP level 110, exit fill 110
      qty 10: commission/side = max($1.00 min, $0.005*10=$0.05) = $1.00
      total commission = $2.00; gross P&L = (110-100)*10 = $100; net = $98
    """
    cfg = CostConfig(commission_per_share=0.005, commission_min=1.0,
                     commission_max_pct=0.0, half_spread_bps=0.0,
                     slippage_bps=0.0, gap_fill_at_open=False)
    df = _df([
        (98, 99, 97, 98),
        (100, 101, 99, 100),     # entry @ 100
        (105, 111, 104, 110),    # high 111 >= tp 110 -> win
    ])
    trades = simulate_trades([_sig('BUY', 98.0, 0)], df, stop_pct=20.0,
                             tp_pct=10.0, qty=10, trailing_mode=False,
                             cost_config=cfg)
    t = trades[0]
    assert t.commission == 2.0, "min commission $1/side, both sides"
    assert t.pnl == 98.0, "P&L is net of the $2 round-trip commission"


def test_commission_per_share_dominates_for_large_qty():
    """When per-share exceeds the floor, per-share wins.
      qty 400, $0.005/share = $2.00/side > $1.00 min -> $4.00 round trip
    """
    cfg = CostConfig(commission_per_share=0.005, commission_min=1.0,
                     commission_max_pct=0.0, half_spread_bps=0.0,
                     slippage_bps=0.0, gap_fill_at_open=False)
    df = _df([
        (98, 99, 97, 98),
        (100, 101, 99, 100),
        (105, 111, 104, 110),
    ])
    trades = simulate_trades([_sig('BUY', 98.0, 0)], df, stop_pct=20.0,
                             tp_pct=10.0, qty=400, trailing_mode=False,
                             cost_config=cfg)
    assert trades[0].commission == 4.0


# ── (d) Gap-through fills at the bar open, not the level ────────────

def test_gap_through_stop_fills_at_open_not_level():
    """A bar that GAPS DOWN through the stop fills at the open, not the stop.
    The test proves the difference: same data, gap_fill on vs off.
      entry 100, fixed stop = 95; gap bar opens at 90 (below 95), low 88
      gap ON  -> fill at open 90 (worse, honest)
      gap OFF -> fill at the 95 stop level (optimistic)
    """
    df = _df([
        (98, 99, 97, 98),        # bar 0: signal
        (100, 101, 99, 100),     # bar 1: entry @ 100; low 99 > stop 95
        (90, 92, 88, 89),        # bar 2: opens 90 (gapped below stop 95)
    ])
    on = CostConfig(commission_per_share=0.0, commission_min=0.0,
                    commission_max_pct=0.0, half_spread_bps=0.0,
                    slippage_bps=0.0, gap_fill_at_open=True)
    off = CostConfig(commission_per_share=0.0, commission_min=0.0,
                     commission_max_pct=0.0, half_spread_bps=0.0,
                     slippage_bps=0.0, gap_fill_at_open=False)
    t_on = simulate_trades([_sig('BUY', 98.0, 0)], df, stop_pct=5.0,
                           tp_pct=50.0, qty=10, trailing_mode=False,
                           cost_config=on)[0]
    t_off = simulate_trades([_sig('BUY', 98.0, 0)], df, stop_pct=5.0,
                            tp_pct=50.0, qty=10, trailing_mode=False,
                            cost_config=off)[0]
    assert t_on.outcome == 'loss' and t_off.outcome == 'loss'
    assert t_on.exit_price == 90.0, "gap-through fills at the gapped open"
    assert t_off.exit_price == 95.0, "without gap logic, fills at the stop level"
    assert t_on.exit_price != t_off.exit_price, "gap fill must differ from level fill"
    assert t_on.pnl < t_off.pnl, "honest gap fill is worse than the optimistic level"
    # Hand-checked: (90-100)*10 = -100 vs (95-100)*10 = -50
    assert t_on.pnl == -100.0 and t_off.pnl == -50.0


# ── (e) Cost config read from config, defaults to base ─────────────

def test_classify_instrument():
    assert classify_instrument("USD", "STK") == "us_large_cap"
    assert classify_instrument("GBP", "STK") == "lse"
    assert classify_instrument("USD", "CFD") == "cfd"
    assert classify_instrument("EUR", "STK") == "default"


def test_cost_config_defaults_to_base():
    """from_settings with no cost_model block returns the base class defaults."""
    cfg = CostConfig.from_settings({}, "us_large_cap", preset="base")
    assert cfg.half_spread_bps == _BASE_HALF_SPREAD_BPS["us_large_cap"] == 1.5
    assert cfg.slippage_bps == _BASE_SLIPPAGE_BPS == 2.0
    assert cfg.commission_min == 1.0          # IBKR US Fixed minimum
    assert cfg.gap_fill_at_open is True
    # Wider classes
    assert CostConfig.for_class("lse").half_spread_bps == 5.0
    assert CostConfig.for_class("cfd").half_spread_bps == 10.0
    # Pessimistic preset doubles spread + slippage
    pess = CostConfig.for_class("us_large_cap", preset="pessimistic")
    assert pess.half_spread_bps == 3.0 and pess.slippage_bps == 4.0


def test_cost_config_reads_overrides_from_settings():
    """A cost_model block in settings overrides individual fields; the rest
    fall back to base."""
    settings = {"cost_model": {"half_spread_bps": 7.5, "commission_min": 2.5}}
    cfg = CostConfig.from_settings(settings, "us_large_cap", preset="base")
    assert cfg.half_spread_bps == 7.5      # overridden
    assert cfg.commission_min == 2.5       # overridden
    assert cfg.slippage_bps == 2.0         # base fallback
    assert cfg.commission_per_share == 0.005


# ── (f) Zero-cost reproduces Fix 1 + Fix 1b exactly ────────────────

def test_zero_cost_reproduces_costless_numbers():
    """cost_config=CostConfig.zero() must be bit-identical to cost_config=None
    (the costless Fix 1 + Fix 1b path), even on a scenario with a gap-through
    and a trailing ratchet — proving costs are additive and isolatable."""
    df = _df([
        (100, 101, 99, 100),     # bar 0: signal
        (100, 101, 99, 100),     # bar 1: entry @ 100, flat
        (100, 105, 100, 104),    # bar 2: close 104 -> trailing ratchets stop to 98.8
        (95, 96, 90, 92),        # bar 3: gaps to open 95, low 90 -> stop @ 98.8
    ])
    kwargs = dict(stop_pct=5.0, tp_pct=50.0, qty=10, trailing_mode=True)
    none = simulate_trades([_sig('BUY', 100.0, 0)], df, cost_config=None, **kwargs)
    zero = simulate_trades([_sig('BUY', 100.0, 0)], df,
                           cost_config=CostConfig.zero(), **kwargs)
    assert len(none) == len(zero) == 1
    a, b = none[0], zero[0]
    assert (a.entry_price, a.exit_price, a.pnl, a.commission,
            a.outcome, a.holding_bars) == \
           (b.entry_price, b.exit_price, b.pnl, b.commission,
            b.outcome, b.holding_bars)
    # Costless ratcheted stop at 98.8 (NOT the gapped open 95) and no commission.
    assert a.commission == 0.0
    assert a.exit_price == pytest.approx(98.8)

    # And a BASE config on the same data DOES differ (gap fill -> 95, + costs),
    # confirming zero() genuinely switches the realism off.
    base = simulate_trades([_sig('BUY', 100.0, 0)], df,
                           cost_config=CostConfig.for_class("us_large_cap"),
                           **kwargs)[0]
    assert base.exit_price != a.exit_price
