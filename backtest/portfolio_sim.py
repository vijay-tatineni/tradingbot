"""
backtest/portfolio_sim.py — Portfolio event loop (Phase 1b).

A single clock across the whole universe, replacing the per-symbol independent
simulation. It does NOT re-implement fills: it drives the honest per-trade
mechanics in backtest.simulator.OpenPosition (Fix 1 next-open, Fix 1b
check-then-ratchet, Fix 2 adverse fills + gap-through + commission, Fix 3
target_notional sizing). This module is purely the portfolio-aware scheduler.

Modelled live constraints (verified against bot/layer1.py + bot/order_validator.py):
  - max_open_positions cap (default 5). When full, a new entry is DROPPED
    (not queued) and counted blocked_by_cap — matches layer1._can_enter.
  - one position per instrument: a signal that fires while that instrument is
    already open is DROPPED and counted blocked_by_open — matches the
    pos!=0 / pos==0 branch split in _process_instrument.

DELIBERATELY NOT modelled (scoped out per the Phase 1b plan; every one of these
would only REDUCE entries, so this loop is a conservative UPPER BOUND on
turnover/commission vs true live):
  - daily/weekly loss-limit circuit breakers
  - max_entries_per_cycle
  - reentry cooldown / recovery
There is no buying-power gate in live beyond the position count + per-order
notional cap (order_validator), so none is modelled here either.

Tie-break when multiple signals contend for scarce slots at the same timestamp:
instruments.json array order (faithful to live's active_instruments iteration in
layer1.py:89). NOT signal-strength ranked — live has no such priority.
"""

from dataclasses import dataclass, field

import pandas as pd

from backtest.offline_signals import Signal
from backtest.simulator import (
    OpenPosition, TradeResult, SimulationSummary, summarise,
    CostConfig, classify_instrument,
)

# FX fallbacks mirror bot/portfolio.py:_get_fx_rate (the live path uses IBKR's
# live ExchangeRate tag with these as fallback). A backtest has no IBKR, so the
# fallbacks apply. Mirrored (not imported) because PortfolioManager needs a live
# IBKR handle; keeping the live bot untouched. STATIC-RATE SIMPLIFICATION — the
# go/no-go (per-instrument PF within portfolio) is FX-invariant; FX only scales
# the combined equity curve.
_FX_TO_USD = {"USD": 1.0, "GBP": 1.27, "EUR": 1.08, "CHF": 1.12, "JPY": 0.0067}


def fx_to_usd(currency: str) -> float:
    return _FX_TO_USD.get(currency, 1.0)


@dataclass
class PortfolioConfig:
    max_open_positions: int = 5
    target_notional: float = 1000.0


@dataclass
class InstrumentSpec:
    """Everything the loop needs to run one instrument's honest trades."""
    symbol: str
    order_idx: int            # instruments.json position — the tie-break key
    df: pd.DataFrame
    signals: list             # Signal list (already strategy-filtered upstream)
    stop_pct: float
    tp_pct: float
    qty: int
    long_only: bool
    currency: str
    timeframe: str
    cost_config: CostConfig


@dataclass
class BlockedSignal:
    symbol: str
    timestamp: str
    fill_bar: int
    reason: str               # "cap" or "open"


@dataclass
class PortfolioResult:
    trades: list              # list[TradeResult], in exit-timestamp order
    blocked: list             # list[BlockedSignal]
    per_symbol_trades: dict    # symbol -> list[TradeResult]
    per_symbol_summary: dict   # symbol -> SimulationSummary
    blocked_by_cap: dict       # symbol -> int
    blocked_by_open: dict      # symbol -> int
    equity_curve_usd: list     # list[(timestamp, cum_pnl_usd)]
    max_drawdown_usd: float
    total_pnl_usd: float
    peak_concurrent: int       # max positions ever held at once


def _signal_fill_map(spec: InstrumentSpec) -> dict:
    """Map fill_bar_index -> Signal. A signal known at bar s's close fills at
    bar s+1's open (Fix 1). SELL signals on long_only instruments are dropped
    here (strategy filter, not a portfolio block) to match simulate_trades."""
    fill = {}
    n = len(spec.df)
    for sig in spec.signals:
        if sig.direction == "SELL" and spec.long_only:
            continue
        fb = sig.bar_index + 1
        if fb < n:                      # signal on last bar can't fill
            fill[fb] = sig
    return fill


def run_portfolio(specs: list, cfg: PortfolioConfig) -> PortfolioResult:
    """Drive the single-clock portfolio event loop over the given instruments.

    specs must already be sorted by order_idx (instruments.json order)."""
    specs = sorted(specs, key=lambda s: s.order_idx)
    by_symbol = {s.symbol: s for s in specs}
    fill_maps = {s.symbol: _signal_fill_map(s) for s in specs}

    # Single merged clock: one event per (instrument, bar), sorted by timestamp
    # then instruments.json order. Each event = "this instrument's bar at T".
    events = []  # (timestamp, order_idx, symbol, local_bar_index)
    for s in specs:
        dts = s.df["datetime"]
        for j in range(len(s.df)):
            events.append((dts.iloc[j], s.order_idx, s.symbol, j))
    events.sort(key=lambda e: (e[0], e[1]))

    open_positions = {}       # symbol -> OpenPosition (currently held)
    completed = []            # (exit_timestamp, TradeResult)
    blocked = []
    blocked_by_cap = {s.symbol: 0 for s in specs}
    blocked_by_open = {s.symbol: 0 for s in specs}
    peak_concurrent = 0

    def _finalize(symbol):
        pos = open_positions.pop(symbol)
        tr = pos.finalize()
        completed.append((tr.exit_date, tr))
        return tr

    # Walk the clock one timestamp-group at a time so we can enforce
    # exits-before-entries within a tie (a slot freed at T is available to a
    # later instrument at the same T).
    i = 0
    n_events = len(events)
    while i < n_events:
        t = events[i][0]
        group = []
        while i < n_events and events[i][0] == t:
            group.append(events[i])
            i += 1

        # snapshot which symbols are open at the START of this timestamp — that
        # is the state that decides exit-manage vs entry-consider (live: a
        # position closed this cycle does not re-enter same cycle).
        open_at_start = set(open_positions.keys())

        # ── Pass 1: exits ──  step every open position against its bar here.
        for (_t, _oi, sym, j) in group:
            if sym in open_at_start and sym in open_positions:
                pos = open_positions[sym]
                if pos.step(j) is not None:     # exit triggered on this bar
                    _finalize(sym)
            # a signal that would fill at this bar while we were already open
            # is blocked_by_open (the entry can't happen — slot occupied).
            if sym in open_at_start and j in fill_maps[sym]:
                blocked_by_open[sym] += 1
                blocked.append(BlockedSignal(sym, str(t), j, "open"))

        # ── Pass 2: entries ──  flat instruments with a signal filling here,
        # in instruments.json order. Cap checked against the LIVE open count so
        # a slot freed in pass 1 (or by a same-bar exit earlier in pass 2) is
        # visible — matches live checking _open_count at order time.
        for (_t, _oi, sym, j) in sorted(group, key=lambda e: e[1]):
            if sym in open_at_start:
                continue                         # was open at start → not an entry bar
            if sym in open_positions:
                continue                         # already entered earlier this T (shouldn't happen: 1 event/bar)
            sig = fill_maps[sym].get(j)
            if sig is None:
                continue                         # no signal fills here
            if len(open_positions) >= cfg.max_open_positions:
                blocked_by_cap[sym] += 1
                blocked.append(BlockedSignal(sym, str(t), j, "cap"))
                continue
            spec = by_symbol[sym]
            pos = OpenPosition.open_from_signal(
                sig, spec.df, spec.stop_pct, spec.tp_pct, qty=spec.qty,
                long_only=spec.long_only, currency=spec.currency,
                target_notional=cfg.target_notional, trailing_mode=True,
                entry_on="next_open", cost_config=spec.cost_config,
            )
            if pos is None:
                continue
            open_positions[sym] = pos
            peak_concurrent = max(peak_concurrent, len(open_positions))
            # entry-bar-inclusive: the entry bar's own high/low can trigger
            # (Fix 1). If it exits same bar, finalize now and free the slot.
            if pos.step(j) is not None:
                _finalize(sym)

    # End of data: mark out any still-open positions at their last bar.
    for sym in list(open_positions.keys()):
        _finalize(sym)

    # Order completed trades by exit timestamp for a single global equity curve.
    completed.sort(key=lambda x: x[0])
    trades = [tr for (_ts, tr) in completed]

    per_symbol_trades = {s.symbol: [] for s in specs}
    for tr in trades:
        per_symbol_trades[tr.symbol].append(tr)
    per_symbol_summary = {sym: summarise(trs)
                          for sym, trs in per_symbol_trades.items()}

    # Combined USD equity curve + drawdown, globally ordered by exit timestamp.
    equity = []
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for ts, tr in completed:
        cum += tr.pnl * fx_to_usd(by_symbol[tr.symbol].currency)
        equity.append((ts, round(cum, 2)))
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    return PortfolioResult(
        trades=trades,
        blocked=blocked,
        per_symbol_trades=per_symbol_trades,
        per_symbol_summary=per_symbol_summary,
        blocked_by_cap=blocked_by_cap,
        blocked_by_open=blocked_by_open,
        equity_curve_usd=equity,
        max_drawdown_usd=round(max_dd, 2),
        total_pnl_usd=round(cum, 2),
        peak_concurrent=peak_concurrent,
    )


# ── Convenience: build specs from instruments.json + backtest.db ───────────

def build_specs(instruments: list, settings: dict, conn,
                symbols: list = None) -> list:
    """Load data + signals and build InstrumentSpecs for the enabled (or a
    requested subset of) instruments, preserving instruments.json order."""
    from backtest.database import load_bars
    from backtest.run import _resolve_indicator_settings
    from backtest.offline_signals import generate_signals

    specs = []
    order_idx = 0
    for inst in instruments:
        if not inst.get("enabled"):
            continue
        sym = inst["symbol"]
        this_order = order_idx
        order_idx += 1
        if symbols is not None and sym not in symbols:
            continue
        tf = inst.get("timeframe", "daily")
        df = load_bars(conn, sym, tf)
        if df.empty and tf != "daily":
            df = load_bars(conn, sym, "daily")
            tf = "daily"
        if df.empty:
            continue
        ind = _resolve_indicator_settings(settings, inst)
        signals = generate_signals(df, ind, sym)
        currency = inst.get("currency", "USD")
        inst_class = classify_instrument(currency, inst.get("sec_type", "STK"))
        cost_config = CostConfig.from_settings(ind, inst_class, preset="base")
        specs.append(InstrumentSpec(
            symbol=sym, order_idx=this_order, df=df, signals=signals,
            stop_pct=inst.get("trail_stop_pct", 2.0),
            tp_pct=inst.get("take_profit_pct", 8.0),
            qty=inst.get("qty", 1), long_only=inst.get("long_only", True),
            currency=currency, timeframe=tf, cost_config=cost_config,
        ))
    return specs
