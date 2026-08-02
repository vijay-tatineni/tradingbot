"""
backtest/walk_forward_phase2.py — Phase 2 nested portfolio-level walk-forward.

Implements the FROZEN pre-registration in experiments/phase2_walkforward.md.
Read that file first; this module must not deviate from it.

Design (Option A — LOCKED):
  - Parameters are NOT searched. Each instrument's live instruments.json config
    (stop, TP, timeframe) is frozen; we walk-forward the instrument-SELECTION
    decision only. Zero parameter degrees of freedom.
  - Window structure: 12-month train / 3-month test, rolling by 3 months over the
    ~24-month history -> 4 OOS windows (months 12-24).
  - Selection (train data only): independent per-instrument run on the train
    sub-window; include a name iff train after-cost PF >= 1.20 AND >= 20 train
    trades. Threshold, not top-k / max-PF.
  - Test (test data only): portfolio event loop (5-cap, one-per-instrument, honest
    costs/sizing) on ONLY the frozen selection.
  - Aggregation: pool ONLY the OOS test-window trades.

Warmup faithfulness: signals are generated ONCE on each instrument's FULL history
(full indicator lookback, exactly as live would have), then a window simply
RESTRICTS which bars trade. Slicing never recomputes indicators on a truncated
series, so no warmup artefact is introduced.
"""

from dataclasses import dataclass, replace

import pandas as pd
from dateutil.relativedelta import relativedelta

from backtest.offline_signals import Signal
from backtest.portfolio_sim import (
    InstrumentSpec, run_portfolio, PortfolioConfig, fx_to_usd,
)
from backtest.simulator import simulate_trades, summarise

# Frozen selection thresholds (experiments/phase2_walkforward.md §2 Stage 2).
PF_BAR = 1.20
MIN_TRAIN_TRADES = 20
MAX_OPEN = 5
TARGET_NOTIONAL = 1000.0

# Frozen window structure (§1).
TRAIN_MONTHS = 12
TEST_MONTHS = 3
STEP_MONTHS = 3
N_WINDOWS = 4

# Coverage tolerance (implementation detail of §4 "full data coverage"). Real bars
# carry intraday timestamps, instrument starts span a few days (03-21..03-25), and
# the feed ends a few days before the round 24-month mark — so an exact
# first_bar<=train_start / last_bar>=test_end test spuriously excludes nearly
# everyone. A few-days grace at each window edge restores the intended meaning
# (an instrument present throughout the window) while still excluding a genuine
# multi-week gap like NVTS's ~32-day late start. NOT a design change: thresholds,
# windows, and selection logic are untouched.
COVERAGE_TOL_DAYS = 10


@dataclass
class Window:
    idx: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp     # == test_start (half-open [start, end))
    test_start: pd.Timestamp
    test_end: pd.Timestamp

    def label(self) -> str:
        return (f"W{self.idx}: train [{self.train_start.date()} .. "
                f"{self.train_end.date()})  test [{self.test_start.date()} .. "
                f"{self.test_end.date()})")


@dataclass
class SelectionRow:
    symbol: str
    eligible: bool
    train_trades: int
    train_pf: float
    selected: bool
    reason: str


def make_windows(t0: pd.Timestamp, n: int = N_WINDOWS) -> list:
    """Calendar windows: 12mo train / 3mo test, rolling 3mo. T0 anchors W1's
    train start; the test windows tile the second 12 months."""
    wins = []
    for k in range(n):
        tr_s = t0 + relativedelta(months=k * STEP_MONTHS)
        tr_e = tr_s + relativedelta(months=TRAIN_MONTHS)
        te_e = tr_e + relativedelta(months=TEST_MONTHS)
        wins.append(Window(k + 1, tr_s, tr_e, tr_e, te_e))
    return wins


def _dt(spec: InstrumentSpec) -> pd.Series:
    return pd.to_datetime(spec.df["datetime"])


def is_eligible(spec: InstrumentSpec, w: Window) -> bool:
    """An instrument is a candidate in a window only if its data fully covers
    BOTH the train and test sub-windows (§4), within COVERAGE_TOL_DAYS at each
    edge. Handles the NVTS offset (excluded until genuinely covered)."""
    dt = _dt(spec)
    tol = pd.Timedelta(days=COVERAGE_TOL_DAYS)
    return dt.iloc[0] <= w.train_start + tol and dt.iloc[-1] >= w.test_end - tol


def slice_spec(spec: InstrumentSpec, start: pd.Timestamp,
               end: pd.Timestamp) -> InstrumentSpec:
    """Restrict a full-history spec to bars in [start, end). Signals (generated
    on full history) are kept where they fall in range and their bar_index is
    remapped to the sliced df's local coordinate. Returns None if the slice is
    empty. Frozen params (stop/TP/qty/long_only/currency/cost) are unchanged."""
    dt = _dt(spec)
    mask = (dt >= start) & (dt < end)
    idx = mask.to_numpy().nonzero()[0]
    if len(idx) == 0:
        return None
    i0, i1 = int(idx[0]), int(idx[-1]) + 1          # contiguous bar range
    sub_df = spec.df.iloc[i0:i1].reset_index(drop=True)
    sub_signals = [
        replace(s, bar_index=s.bar_index - i0)
        for s in spec.signals if i0 <= s.bar_index < i1
    ]
    return replace(spec, df=sub_df, signals=sub_signals)


def select_in_window(full_specs: list, w: Window) -> list:
    """Stage-2 selection on TRAIN data only: independent per-instrument run over
    the train sub-window, include iff PF >= PF_BAR and trades >= MIN_TRAIN_TRADES.
    Returns a list[SelectionRow] (every candidate, with its train read)."""
    rows = []
    for spec in full_specs:
        if not is_eligible(spec, w):
            rows.append(SelectionRow(spec.symbol, False, 0, float("nan"),
                                     False, "no data coverage this window"))
            continue
        sub = slice_spec(spec, w.train_start, w.train_end)
        if sub is None or not sub.signals:
            rows.append(SelectionRow(spec.symbol, True, 0, float("nan"),
                                     False, "no signals in train window"))
            continue
        trades = simulate_trades(
            sub.signals, sub.df, sub.stop_pct, sub.tp_pct, qty=sub.qty,
            long_only=sub.long_only, currency=sub.currency,
            target_notional=TARGET_NOTIONAL, trailing_mode=True,
            entry_on="next_open", cost_config=sub.cost_config,
        )
        summ = summarise(trades)
        pf = summ.profit_factor
        n = summ.trade_count
        passed = n >= MIN_TRAIN_TRADES and pf >= PF_BAR
        if passed:
            reason = "selected"
        elif n < MIN_TRAIN_TRADES:
            reason = f"thin ({n} < {MIN_TRAIN_TRADES} train trades)"
        else:
            reason = f"PF {pf:.2f} < {PF_BAR:.2f}"
        rows.append(SelectionRow(spec.symbol, True, n, pf, passed, reason))
    return rows


def _scale_cost(cc, mult: float):
    """Scale ONLY slippage + half-spread by mult; commission (a real IBKR
    schedule, not a stress knob) is left untouched (§5 cost-stress)."""
    return replace(cc, half_spread_bps=cc.half_spread_bps * mult,
                   slippage_bps=cc.slippage_bps * mult)


def run_test_window(full_specs: list, selected: list, w: Window,
                    cost_mult: float = 1.0):
    """Run the portfolio loop on ONLY the frozen selection over the TEST window.
    cost_mult scales slippage+spread for the cost-stress sensitivity (selection
    is unchanged — it was frozen at base cost). Returns (PortfolioResult or
    None, list_of_windowed_specs)."""
    by_sym = {s.symbol: s for s in full_specs}
    test_specs = []
    for sym in selected:
        sub = slice_spec(by_sym[sym], w.test_start, w.test_end)
        if sub is not None and len(sub.df):
            if cost_mult != 1.0:
                sub = replace(sub, cost_config=_scale_cost(sub.cost_config, cost_mult))
            test_specs.append(sub)
    if not test_specs:
        return None, []
    res = run_portfolio(test_specs, PortfolioConfig(MAX_OPEN, TARGET_NOTIONAL))
    return res, test_specs


def infer_t0(full_specs: list) -> pd.Timestamp:
    """T0 = earliest data start (the calendar anchor), floored to midnight. Offset
    instruments (NVTS) simply fail per-window coverage until they are covered."""
    return min(_dt(s).iloc[0] for s in full_specs).normalize()
