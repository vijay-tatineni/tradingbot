"""
backtest/breakout_sim.py — Frozen breakout portfolio event loop (§2.5 / §14).

Drives the FROZEN breakout mechanics over the full 14-instrument universe with a
single UTC clock. Reuses the existing honest cost primitives (CostConfig,
_adverse_fill, _commission, classify_instrument, fx_to_usd) but implements the
breakout-specific position mechanics here because they are ATR-absolute, have a
monotonic ratchet, no take-profit, and an SMA50 trend-break exit — none of which
the percentage-based simulator.OpenPosition can express. simulator.py and
portfolio_sim.py are left untouched.

Frozen mechanics implemented:
  - Entry next session open + adverse costs; size by 0.50% risk with 20% notional
    cap, 5-position cap, 2.50% aggregate-heat cap, and a no-leverage cash cap
    (§2 / §2.5 sequential sizing).
  - Initial stop = entry_fill - 2.0 * signal_bar ATR14 (fixed at entry).
  - Monotonic ratchet: candidate = highest_completed_close - 3.0 * current ATR14;
    active_stop = max(prev, initial, candidate); effective NEXT session; never falls.
  - Stop fills: gap (open <= stop -> open) then intra-session (low <= stop -> stop),
    both at adverse costs. Trend-break exit at next open - adverse costs.
  - Mixed-market UTC ordering (§14): each instrument's daily session occurs at its
    exchange session-open time in UTC; European exits free slots/cash before US
    entries the same date. Frozen universe order tie-breaks ONLY among instruments
    sharing the exact same event timestamp.

Outcome-blindness: this module computes P&L internally (equity must evolve to size
positions) but the Phase 3a reporter (breakout_run.py) surfaces only mechanics and
frequency — never PF, net P&L, winners, or rankings (§12).
"""

from dataclasses import dataclass, field

import pandas as pd

from backtest.simulator import (
    CostConfig, _adverse_fill, _commission, classify_instrument,
)
from backtest.portfolio_sim import fx_to_usd
from bot.currency import is_pence_instrument

# Frozen risk configuration (§2)
RISK_PER_TRADE = 0.005          # 0.50% of MTM equity
MAX_NOTIONAL_PCT = 0.20         # 20% of equity per instrument
MAX_OPEN_POSITIONS = 5
MAX_PORTFOLIO_HEAT = 0.025      # 2.50% aggregate initial-stop risk
INITIAL_STOP_ATR_MULT = 2.0
TRAIL_ATR_MULT = 3.0

# Frozen initial capital (USD). Not a strategy rule — a scale-only implementation
# parameter, chosen large enough that integer-share rounding and the $1 commission
# floor are not dominant across 14 instruments incl. pence-quoted LSE names.
# Recorded at Commit B. No canonical live account-equity figure exists in settings.
DEFAULT_INITIAL_CAPITAL_USD = 100_000.0

# Representative exchange session-open times in UTC, used ONLY for event ordering
# (§14). The ordering invariant that matters is Europe-before-US on the same date;
# exact per-exchange calendars + DST are deferred and documented at Commit B.
_SESSION_OPEN_UTC = {
    "lse": ("GBP", 8, 0),       # London  08:00 UTC
    "euronext": ("EUR", 8, 0),  # Paris/Amsterdam ~08:00 UTC
    "us": ("USD", 14, 30),      # NYSE/NASDAQ 09:30 ET = 14:30 UTC (EST convention)
}


def classify_and_cost(currency: str, sec_type: str = "STK", preset: str = "base"):
    """Build the base-preset CostConfig for an instrument (reuses the existing
    honest cost model in simulator.py)."""
    return CostConfig.for_class(classify_instrument(currency, sec_type), preset)


def session_open_offset(currency: str, sec_type: str = "STK"):
    """(hours, minutes) past UTC midnight for this instrument's session open."""
    if currency == "GBP":
        return (8, 0)
    if currency == "EUR":
        return (8, 0)
    return (14, 30)             # USD / default: US session


@dataclass
class BreakoutInstrument:
    """One instrument ready for the breakout loop. df must already carry the
    frozen indicator/signal columns from compute_indicators()."""
    symbol: str
    order_idx: int
    df: pd.DataFrame
    currency: str
    sec_type: str
    cost_config: CostConfig

    @property
    def pence_div(self) -> float:
        return 100.0 if is_pence_instrument(self.currency) else 1.0

    @property
    def fx(self) -> float:
        return fx_to_usd(self.currency)


@dataclass
class BreakoutPosition:
    """In-flight long breakout position. Holds the one copy of the ATR stop +
    monotonic ratchet mechanics so the loop and the tests drive identical code."""
    symbol: str
    inst: BreakoutInstrument
    entry_idx: int
    entry_date: str
    entry_fill: float           # local quote units
    qty: int
    signal_bar_atr: float
    initial_stop: float
    active_stop: float
    stop_distance_usd: float    # frozen at entry, for heat accounting
    highest_close: float = float("-inf")
    ratchet_history: list = field(default_factory=list)

    @classmethod
    def open(cls, inst, entry_idx, entry_fill, qty, signal_bar_atr,
             stop_distance_usd):
        initial_stop = entry_fill - INITIAL_STOP_ATR_MULT * signal_bar_atr
        return cls(
            symbol=inst.symbol, inst=inst, entry_idx=entry_idx,
            entry_date=str(inst.df.iloc[entry_idx]["datetime"]),
            entry_fill=entry_fill, qty=qty, signal_bar_atr=signal_bar_atr,
            initial_stop=initial_stop, active_stop=initial_stop,
            stop_distance_usd=stop_distance_usd,
        )

    def gap_stop_fill(self, bar):
        """If this session opens at/through the active stop, the fill is the
        OPEN (adverse), not the stop level. Returns fill price or None."""
        if bar["open"] <= self.active_stop:
            return _adverse_fill(float(bar["open"]), is_buy=False,
                                 cfg=self.inst.cost_config)
        return None

    def intrasession_stop_fill(self, bar):
        """If the session low reaches the active stop (and it did not gap), the
        fill is the stop level (adverse). Returns fill price or None."""
        if bar["low"] <= self.active_stop:
            return _adverse_fill(float(self.active_stop), is_buy=False,
                                 cfg=self.inst.cost_config)
        return None

    def ratchet_at_close(self, bar):
        """Update highest completed close + monotonic ratchet from this bar's
        close. The new active_stop is effective NEXT session. Never falls."""
        close = float(bar["close"])
        atr = float(bar["atr14"])
        if close > self.highest_close:
            self.highest_close = close
        candidate = self.highest_close - TRAIL_ATR_MULT * atr
        prev = self.active_stop
        self.active_stop = max(prev, self.initial_stop, candidate)
        self.ratchet_history.append(self.active_stop)
        return self.active_stop


@dataclass
class BreakoutTrade:
    symbol: str
    entry_date: str
    entry_fill: float
    qty: int
    exit_date: str
    exit_fill: float
    exit_reason: str            # gap_stop | intra_stop | trend_break | END_OF_TEST_LIQUIDATION
    holding_bars: int
    initial_stop: float
    pnl_usd: float


@dataclass
class EntryTrace:
    """Per-entry sizing breakdown (a mechanic, reportable in 3a)."""
    date: str
    symbol: str
    entry_fill: float
    qty: int
    qty_risk: int
    qty_notional: int
    qty_heat: int
    qty_cash: int
    binding: str
    equity_usd: float
    initial_stop: float


@dataclass
class BlockedEntry:
    date: str
    symbol: str
    reason: str                 # cap | heat_zero | cash_zero | size_zero


@dataclass
class BreakoutPortfolioResult:
    trades: list
    entries: list               # EntryTrace
    blocked: list               # BlockedEntry
    equity_curve_usd: list      # (date, equity_usd) at each event group close
    contention_events: list     # timestamps where >1 candidate competed for slots
    peak_concurrent: int
    concurrency_hist: dict
    n_event_groups: int
    initial_capital_usd: float
    final_equity_usd: float


def _floor_int(x: float) -> int:
    import math
    return int(math.floor(x)) if x == x and x not in (float("inf"), float("-inf")) else 0


def run_breakout_portfolio(instruments: list,
                           initial_capital_usd: float = DEFAULT_INITIAL_CAPITAL_USD,
                           liquidate_at_end: bool = True
                           ) -> BreakoutPortfolioResult:
    """Single-UTC-clock breakout portfolio loop over the given instruments.

    Each instrument's df must already carry compute_indicators() columns and be
    sliced to the desired date range BEFORE calling (the caller enforces the
    OOS-blind boundary). order_idx is the frozen universe tie-break."""
    insts = sorted(instruments, key=lambda x: x.order_idx)
    by_symbol = {x.symbol: x for x in insts}

    # Build the merged UTC event clock: one event per (instrument, bar).
    events = []  # (event_ts, order_idx, symbol, local_bar_j)
    for x in insts:
        h, m = session_open_offset(x.currency, x.sec_type)
        for j in range(len(x.df)):
            d = pd.Timestamp(x.df.iloc[j]["datetime"]).normalize()
            event_ts = d + pd.Timedelta(hours=h, minutes=m)
            events.append((event_ts, x.order_idx, x.symbol, j))
    events.sort(key=lambda e: (e[0], e[1]))

    cash_usd = float(initial_capital_usd)
    positions = {}              # symbol -> BreakoutPosition
    pending_entry = {x.symbol: False for x in insts}        # entry_signal[j-1]
    pending_trend_break = {x.symbol: False for x in insts}  # trend_break[j-1]
    last_close_local = {x.symbol: None for x in insts}      # for MTM marks

    trades, entries, blocked, equity_curve = [], [], [], []
    contention_events = []
    peak_concurrent = 0
    concurrency_hist = {}
    n_groups = 0

    def mtm_equity_usd():
        eq = cash_usd
        for sym, pos in positions.items():
            x = by_symbol[sym]
            mark = last_close_local[sym]
            if mark is None:
                mark = pos.entry_fill
            eq += pos.qty * mark / x.pence_div * x.fx
        return eq

    def close_position(sym, exit_fill_local, exit_date, reason, holding_bars):
        nonlocal cash_usd
        pos = positions.pop(sym)
        x = by_symbol[sym]
        pdiv, fx = x.pence_div, x.fx
        comm_local = _commission(exit_fill_local, pos.qty, x.cost_config, pdiv)
        proceeds_usd = pos.qty * exit_fill_local / pdiv * fx
        cash_usd += proceeds_usd - comm_local * fx
        entry_comm_local = _commission(pos.entry_fill, pos.qty, x.cost_config, pdiv)
        raw_pnl_local = (exit_fill_local - pos.entry_fill) * pos.qty
        pnl_local = raw_pnl_local / pdiv - (comm_local + entry_comm_local)
        trades.append(BreakoutTrade(
            symbol=sym, entry_date=pos.entry_date, entry_fill=round(pos.entry_fill, 4),
            qty=pos.qty, exit_date=str(exit_date), exit_fill=round(exit_fill_local, 4),
            exit_reason=reason, holding_bars=holding_bars,
            initial_stop=round(pos.initial_stop, 4), pnl_usd=round(pnl_local * fx, 2),
        ))

    i, n = 0, len(events)
    while i < n:
        ts = events[i][0]
        group = []
        while i < n and events[i][0] == ts:
            group.append(events[i])
            i += 1
        group.sort(key=lambda e: e[1])     # frozen universe order within the tie

        open_at_start = set(positions.keys())

        # ── Pass A: open exits (gap stop, then scheduled trend-break) ──
        for (_t, _oi, sym, j) in group:
            if sym not in positions:
                continue
            x = by_symbol[sym]
            bar = x.df.iloc[j]
            pos = positions[sym]
            gfill = pos.gap_stop_fill(bar)
            if gfill is not None:
                close_position(sym, gfill, bar["datetime"], "gap_stop", j - pos.entry_idx)
            elif pending_trend_break.get(sym):
                tb_fill = _adverse_fill(float(bar["open"]), is_buy=False,
                                        cfg=x.cost_config)
                close_position(sym, tb_fill, bar["datetime"], "trend_break", j - pos.entry_idx)

        # ── Pass B: entries (flat instruments with pending entry), universe order ──
        # equity snapshot is post-exit for THIS event group (§14).
        equity_snapshot = mtm_equity_usd()
        candidates = [(sym, j) for (_t, _oi, sym, j) in group
                      if sym not in open_at_start and sym not in positions
                      and pending_entry.get(sym)]
        if len(candidates) > 1:
            contention_events.append((str(ts), [c[0] for c in candidates]))
        for (sym, j) in candidates:
            x = by_symbol[sym]
            bar = x.df.iloc[j]
            if len(positions) >= MAX_OPEN_POSITIONS:
                blocked.append(BlockedEntry(str(bar["datetime"]), sym, "cap"))
                continue
            signal_bar_atr = float(x.df.iloc[j - 1]["atr14"]) if j >= 1 else float("nan")
            if not (signal_bar_atr > 0):
                blocked.append(BlockedEntry(str(bar["datetime"]), sym, "size_zero"))
                continue
            entry_fill = _adverse_fill(float(bar["open"]), is_buy=True, cfg=x.cost_config)
            pdiv, fx = x.pence_div, x.fx
            stop_distance_local = INITIAL_STOP_ATR_MULT * signal_bar_atr
            stop_distance_usd = stop_distance_local / pdiv * fx
            price_usd = entry_fill / pdiv * fx
            # risk, notional, heat caps off the post-exit equity snapshot
            qty_risk = _floor_int(equity_snapshot * RISK_PER_TRADE / stop_distance_usd)
            qty_notional = _floor_int(equity_snapshot * MAX_NOTIONAL_PCT / price_usd)
            current_heat = sum(p.qty * p.stop_distance_usd for p in positions.values())
            remaining_heat = max(0.0, MAX_PORTFOLIO_HEAT * equity_snapshot - current_heat)
            qty_heat = _floor_int(remaining_heat / stop_distance_usd)
            qty_pre = min(qty_risk, qty_notional, qty_heat)
            if qty_pre < 1:
                reason = "heat_zero" if qty_heat < min(qty_risk, qty_notional) else "size_zero"
                blocked.append(BlockedEntry(str(bar["datetime"]), sym, reason))
                continue
            # no-leverage cash cap (sequential: cash already reduced by prior entries)
            est_comm_usd = _commission(entry_fill, qty_pre, x.cost_config, pdiv) * fx
            qty_cash = _floor_int((cash_usd - est_comm_usd) / price_usd)
            qty = min(qty_pre, qty_cash)
            if qty < 1:
                blocked.append(BlockedEntry(str(bar["datetime"]), sym, "cash_zero"))
                continue
            binding = min(
                [("risk", qty_risk), ("notional", qty_notional),
                 ("heat", qty_heat), ("cash", qty_cash)], key=lambda kv: kv[1])[0]
            # admit
            comm_usd = _commission(entry_fill, qty, x.cost_config, pdiv) * fx
            cash_usd -= qty * price_usd + comm_usd
            pos = BreakoutPosition.open(x, j, entry_fill, qty, signal_bar_atr,
                                        stop_distance_usd)
            positions[sym] = pos
            peak_concurrent = max(peak_concurrent, len(positions))
            entries.append(EntryTrace(
                date=str(bar["datetime"]), symbol=sym, entry_fill=round(entry_fill, 4),
                qty=qty, qty_risk=qty_risk, qty_notional=qty_notional,
                qty_heat=qty_heat, qty_cash=qty_cash, binding=binding,
                equity_usd=round(equity_snapshot, 2), initial_stop=round(pos.initial_stop, 4)))

        # ── Pass C: during-session intra stops (incl. positions entered today) ──
        for (_t, _oi, sym, j) in group:
            if sym not in positions:
                continue
            x = by_symbol[sym]
            bar = x.df.iloc[j]
            pos = positions[sym]
            ifill = pos.intrasession_stop_fill(bar)
            if ifill is not None:
                close_position(sym, ifill, bar["datetime"], "intra_stop", j - pos.entry_idx)

        # ── Pass D: close — mark, ratchet (next session), set pending flags ──
        for (_t, _oi, sym, j) in group:
            x = by_symbol[sym]
            bar = x.df.iloc[j]
            last_close_local[sym] = float(bar["close"])
            if sym in positions:
                positions[sym].ratchet_at_close(bar)
                pending_trend_break[sym] = bool(bar["trend_break"])
                pending_entry[sym] = False           # held -> ignore entry signals
            else:
                pending_trend_break[sym] = False
                pending_entry[sym] = bool(bar["entry_signal"])

        n_groups += 1
        held = len(positions)
        concurrency_hist[held] = concurrency_hist.get(held, 0) + 1
        equity_curve.append((str(ts), round(mtm_equity_usd(), 2)))

    # End of data: synthetic liquidation at the last completed close (adverse).
    if liquidate_at_end:
        for sym in list(positions.keys()):
            x = by_symbol[sym]
            pos = positions[sym]
            last_bar = x.df.iloc[-1]
            raw = float(last_bar["close"])
            exit_fill = _adverse_fill(raw, is_buy=False, cfg=x.cost_config)
            close_position(sym, exit_fill, last_bar["datetime"],
                           "END_OF_TEST_LIQUIDATION", len(x.df) - 1 - pos.entry_idx)

    return BreakoutPortfolioResult(
        trades=trades, entries=entries, blocked=blocked,
        equity_curve_usd=equity_curve, contention_events=contention_events,
        peak_concurrent=peak_concurrent, concurrency_hist=concurrency_hist,
        n_event_groups=n_groups, initial_capital_usd=float(initial_capital_usd),
        final_equity_usd=round(cash_usd, 2),
    )
