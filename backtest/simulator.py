"""
backtest/simulator.py — Simulate trades from signals against OHLCV data.

For each signal, scans forward through bars to determine if the trade
hits stop-loss, take-profit, or remains open at end of data.

Cost/fill realism (Fix 2) is OPT-IN via the cost_config argument:
commission, half-spread, slippage, and gap-through fills. With
cost_config=None (the default) the simulator is frictionless and
reproduces the costless Fix 1 + Fix 1b behaviour bit-for-bit.
"""

from dataclasses import dataclass

import pandas as pd

from backtest.offline_signals import Signal
from bot.currency import is_pence_instrument, convert_pnl_to_base
from bot.sizing import calculate_qty


# ── Cost / fill-realism model (Fix 2) ──────────────────────────────
#
# Base (realistic) per-instrument-class HALF-spread, in basis points
# (1 bp = 0.01%). A round trip pays the half-spread twice (adverse on
# both entry and exit). Tighter for liquid US large-caps, wider for LSE
# names, widest for CFDs.
_BASE_HALF_SPREAD_BPS = {
    "us_large_cap": 1.5,   # liquid US mega/large-cap stocks
    "lse":          5.0,   # London-listed (pence-quoted) names
    "cfd":         10.0,   # CFDs — widest, dealer spread
    "default":      3.0,   # anything unclassified
}
# Base adverse slippage applied on top of the half-spread, in bps. Market
# orders don't fill at the exact touch.
_BASE_SLIPPAGE_BPS = 2.0
# Sensitivity presets scale spread + slippage (NOT commission, which is
# contractual). "base" = realistic; run optimistic/pessimistic for the
# cost-sensitivity table without code changes.
_PRESET_MULT = {"optimistic": 0.5, "base": 1.0, "pessimistic": 2.0}


@dataclass
class CostConfig:
    """Transaction-cost assumptions for one instrument.

    Commission defaults model IBKR's **US Stocks Fixed** schedule
    (USD 0.005/share, USD 1.00 minimum per order, capped at 1% of trade
    value). Source: IBKR commissions schedule (long-standing fixed tier);
    re-verify at https://www.interactivebrokers.com/en/pricing/commissions-home.php
    For non-USD instruments this same schedule is applied as a conservative
    approximation — override via from_settings() / for_class() if needed.

    Spread + slippage are applied ADVERSELY to every fill: you pay the ask
    on buys and hit the bid on sells, plus slippage in the same direction.
    """
    commission_per_share: float = 0.005
    commission_min: float = 1.0          # per order (per side)
    commission_max_pct: float = 1.0      # percent of trade notional (0 = no cap)
    half_spread_bps: float = 1.5
    slippage_bps: float = 2.0
    gap_fill_at_open: bool = True        # fill gap-throughs at the bar open, not the level

    @property
    def adverse_bps(self) -> float:
        """Total adverse move applied to each fill (half-spread + slippage)."""
        return self.half_spread_bps + self.slippage_bps

    @classmethod
    def zero(cls) -> "CostConfig":
        """Exactly frictionless: no commission, no spread/slippage, no gap
        fills. Reproduces the costless Fix 1 + Fix 1b numbers."""
        return cls(commission_per_share=0.0, commission_min=0.0,
                   commission_max_pct=0.0, half_spread_bps=0.0,
                   slippage_bps=0.0, gap_fill_at_open=False)

    @classmethod
    def for_class(cls, instrument_class: str = "default",
                  preset: str = "base") -> "CostConfig":
        """Build a realistic CostConfig for an instrument class + sensitivity
        preset (optimistic / base / pessimistic)."""
        half = _BASE_HALF_SPREAD_BPS.get(instrument_class,
                                         _BASE_HALF_SPREAD_BPS["default"])
        mult = _PRESET_MULT.get(preset, 1.0)
        return cls(half_spread_bps=half * mult,
                   slippage_bps=_BASE_SLIPPAGE_BPS * mult)

    @classmethod
    def from_settings(cls, settings: dict, instrument_class: str = "default",
                      preset: str = "base") -> "CostConfig":
        """Read a cost model from a settings dict's optional "cost_model"
        block, falling back to the class/preset defaults for any field not
        overridden. Lets the cost-sensitivity table be driven from config."""
        base = cls.for_class(instrument_class, preset)
        cm = (settings or {}).get("cost_model", {}) or {}
        return cls(
            commission_per_share=cm.get("commission_per_share", base.commission_per_share),
            commission_min=cm.get("commission_min", base.commission_min),
            commission_max_pct=cm.get("commission_max_pct", base.commission_max_pct),
            half_spread_bps=cm.get("half_spread_bps", base.half_spread_bps),
            slippage_bps=cm.get("slippage_bps", base.slippage_bps),
            gap_fill_at_open=cm.get("gap_fill_at_open", base.gap_fill_at_open),
        )


def classify_instrument(currency: str = "USD", sec_type: str = "STK") -> str:
    """Map an instrument to a cost class for default spread assumptions."""
    if sec_type and sec_type.upper() == "CFD":
        return "cfd"
    if currency == "GBP":
        return "lse"
    if currency == "USD":
        return "us_large_cap"
    return "default"


def _adverse_fill(price: float, is_buy: bool, cfg: "CostConfig") -> float:
    """Apply half-spread + slippage adversely. Buys fill higher, sells lower."""
    if cfg is None:
        return price
    f = cfg.adverse_bps / 10000.0
    return price * (1 + f) if is_buy else price * (1 - f)


def _exit_level(outcome: str, direction: str, stop_price: float,
                tp_price: float, bar_open: float, cfg: "CostConfig") -> float:
    """Raw (pre-spread) price the exit fills at, before adverse costs.

    With gap_fill_at_open, a bar that opens already past the stop/TP fills at
    the OPEN (the realistic fill) instead of the level — removing the
    optimistic 'always filled exactly at the level' assumption.
    """
    gap = cfg is not None and cfg.gap_fill_at_open
    if outcome == "loss":          # stop hit
        if direction == "BUY":     # long stop: gap DOWN through stop -> worse
            return min(stop_price, bar_open) if gap else stop_price
        return max(stop_price, bar_open) if gap else stop_price  # short stop: gap UP
    else:                          # win / TP hit
        if direction == "BUY":     # long TP: gap UP through TP -> fill at open
            return max(tp_price, bar_open) if gap else tp_price
        return min(tp_price, bar_open) if gap else tp_price       # short TP: gap DOWN


def _commission(price: float, qty: float, cfg: "CostConfig",
                pence_divisor: float) -> float:
    """Per-order commission in the instrument's BASE currency (pounds for
    pence-quoted GBP names, dollars otherwise)."""
    if cfg is None:
        return 0.0
    comm = max(cfg.commission_min, cfg.commission_per_share * qty)
    if cfg.commission_max_pct > 0:
        notional_base = abs(price) * qty / pence_divisor
        comm = min(comm, cfg.commission_max_pct / 100.0 * notional_base)
    return comm


@dataclass
class TradeResult:
    """Outcome of a single simulated trade."""
    symbol: str
    direction: str        # "BUY" or "SELL"
    entry_date: str       # bar we actually FILLED on (next_open: bar i+1)
    entry_price: float    # fill price (next_open: bar i+1's open)
    exit_date: str
    exit_price: float
    pnl: float
    pnl_pct: float
    holding_bars: int
    outcome: str          # "win" (hit TP), "loss" (hit SL), "open" (end of data)
    stop_pct: float
    tp_pct: float
    # Total commission charged (entry + exit) in base currency. 0 when
    # cost_config is None. pnl is already NET of this.
    commission: float = 0.0
    # Reference-only: the bar/price the signal was COMPUTED at (bar i's close).
    # Kept for traceability; NOT used as the fill. Defaults so older call
    # sites and result readers stay valid.
    signal_date: str = None
    signal_price: float = None


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


class OpenPosition:
    """A single in-flight trade, driven one bar at a time.

    Holds the one and only copy of the honest per-trade mechanics:
      Fix 1   next-bar-open entry + entry-bar-inclusive scan
      Fix 1b  check-then-ratchet trailing stop (no intra-bar lookahead)
      Fix 2   adverse fills (half-spread + slippage) + gap-through + commission
      Fix 3   target_notional sizing
    Both simulate_trades() (per-instrument) and the portfolio event loop drive
    THIS class, so the exit/fill/cost logic exists once and can't drift.

    Lifecycle (per-instrument loop):
        pos = OpenPosition.open_from_signal(sig, df, ...)   # None if unfillable
        for j in range(pos.scan_start, len(df)):
            if pos.step(j) is not None:                     # exit triggered
                break
        trade = pos.finalize()                              # -> TradeResult

    A portfolio scheduler instead calls step(j) only on the bars it chooses (in
    global timestamp order, while the position is open) and finalize() when
    step() returns an outcome or the instrument's data ends.
    """

    def __init__(self, sig, df, stop_pct, tp_pct, qty, currency,
                 target_notional, trailing_mode, cost_config,
                 entry_idx, scan_start, raw_entry):
        self.sig = sig
        self.df = df
        self.stop_pct = stop_pct
        self.tp_pct = tp_pct
        self.qty = qty
        self.currency = currency
        self.target_notional = target_notional
        self.trailing_mode = trailing_mode
        self.cost_config = cost_config
        self.entry_idx = entry_idx
        self.scan_start = scan_start

        self.is_buy = sig.direction == "BUY"
        # Adverse entry fill: a buy pays up, a sell sells down (half-spread +
        # slippage). With cost_config=None this is exactly raw_entry. Stops/TP
        # are set off the ACTUAL fill, matching the live bot.
        self.entry_price = _adverse_fill(raw_entry, self.is_buy, cost_config)
        if self.is_buy:
            self.stop_price = self.entry_price * (1 - stop_pct / 100)
            self.tp_price = self.entry_price * (1 + tp_pct / 100)
        else:  # SELL
            self.stop_price = self.entry_price * (1 + stop_pct / 100)
            self.tp_price = self.entry_price * (1 - tp_pct / 100)
        self.peak_price = self.entry_price

        self.outcome = "open"
        self.exit_level = self.entry_price     # raw (pre-cost) exit; set on trigger
        self.exit_bar_open = self.entry_price  # bar open at exit, for gap fills
        self.exit_date = str(df.iloc[-1]["datetime"])
        self.holding_bars = len(df) - entry_idx - 1

    @classmethod
    def open_from_signal(cls, sig, df, stop_pct, tp_pct, qty=1,
                         long_only=True, currency="USD", target_notional=None,
                         trailing_mode=True, entry_on="next_open",
                         cost_config=None):
        """Resolve the entry fill per entry_on and build an OpenPosition, or
        return None if the signal can't be taken.

        entry_on="next_open" (honest): signal known at bar i's close; first
        tradeable price is bar i+1's open. Fill there; the stop/TP go live
        immediately, so the exit scan is entry-bar-inclusive (scan_start =
        entry bar). A signal on the last bar has no next bar -> None.
        entry_on="signal_close" (legacy lookahead): fill at the signal bar's own
        close; that bar is already complete, so the scan starts the NEXT bar.
        """
        if sig.direction == "SELL" and long_only:
            return None
        signal_idx = sig.bar_index
        if entry_on == "next_open":
            fill_idx = signal_idx + 1
            if fill_idx >= len(df):
                return None
            raw_entry = float(df.iloc[fill_idx]["open"])
            entry_idx = fill_idx
            scan_start = entry_idx            # entry-bar-inclusive
        elif entry_on == "signal_close":
            raw_entry = sig.price
            entry_idx = signal_idx
            scan_start = entry_idx + 1
        else:
            raise ValueError(f"unknown entry_on mode: {entry_on!r}")
        return cls(sig, df, stop_pct, tp_pct, qty, currency, target_notional,
                   trailing_mode, cost_config, entry_idx, scan_start, raw_entry)

    def step(self, j: int):
        """Process bar j against the live stop/TP, then ratchet from its close.

        Returns the outcome ("loss"/"win") if this bar triggers an exit, else
        None. HONEST ORDER (no intra-bar lookahead): the stop entering this bar
        is whatever the PREVIOUS bar's close set (or the initial stop, incl. the
        entry bar). Test this bar's low/high against that pre-existing stop
        FIRST; only after surviving do we ratchet from this bar's close, which
        can only affect the NEXT bar.
        """
        bar = self.df.iloc[j]
        if self.sig.direction == "BUY":
            hit_stop = bar["low"] <= self.stop_price
            hit_tp = bar["high"] >= self.tp_price
        else:  # SELL
            hit_stop = bar["high"] >= self.stop_price
            hit_tp = bar["low"] <= self.tp_price

        # Stop-first: a bar that hits both stop and TP is booked as a LOSS
        # (conservative). hit_stop alone or hit_stop+hit_tp -> loss.
        if hit_stop:
            self.outcome = "loss"
            self.exit_level = self.stop_price
            self.exit_bar_open = float(bar["open"])
            self.exit_date = str(bar["datetime"])
            self.holding_bars = j - self.entry_idx
            return self.outcome
        elif hit_tp:
            self.outcome = "win"
            self.exit_level = self.tp_price
            self.exit_bar_open = float(bar["open"])
            self.exit_date = str(bar["datetime"])
            self.holding_bars = j - self.entry_idx
            return self.outcome

        # Survived — ratchet the stop from this bar's close (live bar-close
        # trailing logic); applies from the next bar onward.
        if self.trailing_mode:
            close = bar["close"]
            if self.sig.direction == "BUY":
                if close > self.peak_price:
                    self.peak_price = close
                    new_stop = self.peak_price * (1 - self.stop_pct / 100)
                    self.stop_price = max(self.stop_price, new_stop)
            else:  # SELL (short)
                if close < self.peak_price:
                    self.peak_price = close
                    new_stop = self.peak_price * (1 + self.stop_pct / 100)
                    self.stop_price = min(self.stop_price, new_stop)
        return None

    def finalize(self) -> "TradeResult":
        """Close the position and produce the TradeResult. Call after step()
        returns an outcome, or at end of data (outcome stays 'open' -> mark out
        at the last close, an adverse liquidation with no stop/TP gap logic)."""
        # GBP pence→pounds divisor (LSE quotes in pence, P&L needs pounds)
        pence_divisor = 100.0 if is_pence_instrument(self.currency) else 1.0

        if self.outcome == "open":
            self.exit_level = float(self.df.iloc[-1]["close"])
            self.exit_bar_open = float(self.df.iloc[-1]["open"])
            self.exit_date = str(self.df.iloc[-1]["datetime"])
            self.holding_bars = len(self.df) - self.entry_idx - 1
            raw_exit = self.exit_level
        else:
            # Gap-through: a bar that opened past the level fills at the open.
            raw_exit = _exit_level(self.outcome, self.sig.direction,
                                   self.stop_price, self.tp_price,
                                   self.exit_bar_open, self.cost_config)

        # Adverse exit fill: closing a long is a SELL (fills lower); closing a
        # short is a BUY (fills higher). None cost_config -> exactly raw_exit.
        exit_price = _adverse_fill(raw_exit, is_buy=not self.is_buy,
                                   cfg=self.cost_config)

        # If target_notional is set, compute qty from the actual entry fill.
        trade_qty = self.qty
        if self.target_notional is not None:
            inst_stub = {'qty': self.qty, 'currency': self.currency}
            trade_qty = calculate_qty(inst_stub, self.entry_price,
                                      self.target_notional)
        # Commission charged per side, in base currency, then netted off P&L.
        commission = (_commission(self.entry_price, trade_qty, self.cost_config, pence_divisor)
                      + _commission(exit_price, trade_qty, self.cost_config, pence_divisor))
        # Raw P&L in price units (pence for GBP, dollars for USD)
        if self.is_buy:
            raw_pnl = (exit_price - self.entry_price) * trade_qty
        else:
            raw_pnl = (self.entry_price - exit_price) * trade_qty
        # Convert pence → pounds for GBP instruments, then net out commission.
        pnl = raw_pnl / pence_divisor - commission
        pnl_pct = ((exit_price - self.entry_price) / self.entry_price * 100
                   if self.is_buy
                   else (self.entry_price - exit_price) / self.entry_price * 100)

        return TradeResult(
            symbol=self.sig.symbol,
            direction=self.sig.direction,
            entry_date=str(self.df.iloc[self.entry_idx]["datetime"]),
            entry_price=round(self.entry_price, 4),
            exit_date=self.exit_date,
            exit_price=round(exit_price, 4),
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 2),
            holding_bars=self.holding_bars,
            outcome=self.outcome,
            stop_pct=self.stop_pct,
            tp_pct=self.tp_pct,
            commission=round(commission, 4),
            signal_date=self.sig.datetime,
            signal_price=self.sig.price,
        )


def simulate_trades(
    signals: list[Signal],
    df: pd.DataFrame,
    stop_pct: float,
    tp_pct: float,
    qty: int = 1,
    long_only: bool = True,
    currency: str = "USD",
    target_notional: float = None,
    trailing_mode: bool = True,
    entry_on: str = "next_open",
    cost_config: "CostConfig" = None,
) -> list[TradeResult]:
    """
    Simulate each signal as a trade with stop% and TP%.

    If trailing_mode=True (default):
      - Peak price updates on each bar close
      - Stop ratchets up (for longs) / down (for shorts) but never reverses
      - Matches the live bot's bar-close trailing stop logic

    If trailing_mode=False:
      - Fixed stop = entry × (1 ± stop_pct/100)
      - Never changes after entry (original behavior)

    entry_on controls the fill price:
      - "next_open" (default, honest): the signal is computed from bar i's
        close (when the bar completes and the signal is first known), but the
        fill happens at bar i+1's OPEN — the first price actually tradeable
        after the signal. A signal on the last bar has no next bar and is
        skipped (the trade can't be taken). This removes the same-bar lookahead.
      - "signal_close" (legacy, LOOKAHEAD): fills at the signal bar's own close
        (sig.price). Retained only for A/B comparison; not realistic.

    cost_config controls transaction-cost realism (Fix 2):
      - None (default): frictionless. entry/exit fill exactly at the open /
        stop / TP level, no commission. Reproduces Fix 1 + Fix 1b exactly.
      - a CostConfig: applies half-spread + slippage adversely to every fill
        (pay the ask on buys, hit the bid on sells), charges commission per
        side, and (if gap_fill_at_open) fills gap-throughs at the bar open
        rather than the stop/TP level. entry_price / exit_price on the result
        are the cost-adjusted FILLS; pnl is NET of commission.

    GBP instruments: LSE stocks are quoted in pence. P&L is calculated in
    pence then divided by 100 to convert to pounds, matching the live bot's
    logic in bot/portfolio.py and bot/layer3_silver.py.
    """
    trades = []
    for sig in signals:
        # Resolve the entry fill (Fix 1 next-open, or legacy signal_close) and
        # build the position. None => signal can't be taken (long_only SELL, or
        # a next_open signal on the last bar with no next bar to fill at).
        pos = OpenPosition.open_from_signal(
            sig, df, stop_pct, tp_pct, qty=qty, long_only=long_only,
            currency=currency, target_notional=target_notional,
            trailing_mode=trailing_mode, entry_on=entry_on,
            cost_config=cost_config,
        )
        if pos is None:
            continue
        # Drive the position one bar at a time until it exits or data ends.
        for j in range(pos.scan_start, len(df)):
            if pos.step(j) is not None:
                break
        trades.append(pos.finalize())

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
