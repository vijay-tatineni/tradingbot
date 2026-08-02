"""
backtest/breakout_report.py — Frozen §4 / §5 portfolio metrics + decision tree.

Completes the implementation lock: these are the §4 "frozen" definitions
(Sharpe, drawdown, episode-block bootstrap with transitive overlap) and the §5
panel, plus the mechanical Phase 3b decision tree. Pure functions over a trade
list + a daily marked-to-market equity series — defined entirely by the frozen
v3.2 spec, frozen here BEFORE any OOS data is observed (no researcher degrees of
freedom). Unit-tested on synthetic inputs only.

§4 frozen conventions implemented verbatim:
  - Sharpe: daily MTM portfolio returns, annualized ×√252, rf = 0%.
  - Drawdown: from daily MTM portfolio equity (cash + open positions + all costs).
  - Bootstrap: episodes = trades grouped by portfolio-wide TRANSITIVE overlap of
    holding periods; 90% CI; deterministic seed; 10,000 samples.
"""
import math

import numpy as np
import pandas as pd

ANNUALIZATION = 252
BOOTSTRAP_SAMPLES = 10_000
BOOTSTRAP_SEED = 20260605          # recorded, deterministic (frozen)
BOOTSTRAP_CI = 0.90

# Window calendar (frozen, from Commit A)
WINDOWS = [
    ("W1", "2025-03-01", "2025-05-31"),
    ("W2", "2025-06-01", "2025-08-31"),
    ("W3", "2025-09-01", "2025-11-30"),
    ("W4", "2025-12-01", "2026-02-28"),
]


# ── daily MTM return statistics ───────────────────────────────────────────
def daily_returns(daily_equity, initial_equity):
    """Simple daily returns from a [(date, equity)] series, with the first
    return measured against initial_equity."""
    eq = [e for _, e in daily_equity]
    if not eq:
        return np.array([])
    prev = [initial_equity] + eq[:-1]
    return np.array([(eq[i] - prev[i]) / prev[i] if prev[i] else 0.0
                     for i in range(len(eq))])


def sharpe(daily_equity, initial_equity):
    r = daily_returns(daily_equity, initial_equity)
    if len(r) < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=1) * math.sqrt(ANNUALIZATION))


def sortino(daily_equity, initial_equity):
    r = daily_returns(daily_equity, initial_equity)
    downside = r[r < 0]
    if len(r) < 2 or len(downside) == 0 or downside.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / downside.std(ddof=1) * math.sqrt(ANNUALIZATION))


def annualized_vol(daily_equity, initial_equity):
    r = daily_returns(daily_equity, initial_equity)
    if len(r) < 2:
        return 0.0
    return float(r.std(ddof=1) * math.sqrt(ANNUALIZATION))


def max_drawdown(daily_equity, initial_equity):
    """Max peak-to-trough decline as a fraction of the running peak, from the
    daily MTM equity curve (peak seeded at initial_equity)."""
    peak = initial_equity
    mdd = 0.0
    for _, e in daily_equity:
        peak = max(peak, e)
        if peak > 0:
            mdd = max(mdd, (peak - e) / peak)
    return float(mdd)


def cagr(daily_equity, initial_equity):
    if not daily_equity:
        return 0.0
    final = daily_equity[-1][1]
    d0 = pd.Timestamp(daily_equity[0][0]).normalize()
    d1 = pd.Timestamp(daily_equity[-1][0]).normalize()
    years = max((d1 - d0).days / 365.25, 1e-9)
    if final <= 0 or initial_equity <= 0:
        return -1.0
    return float((final / initial_equity) ** (1 / years) - 1)


def calmar(daily_equity, initial_equity):
    mdd = max_drawdown(daily_equity, initial_equity)
    return float(cagr(daily_equity, initial_equity) / mdd) if mdd > 0 else 0.0


# ── per-window attribution ────────────────────────────────────────────────
def _window_of(date):
    d = pd.Timestamp(date).tz_localize(None) if pd.Timestamp(date).tz else pd.Timestamp(date)
    for name, a, b in WINDOWS:
        if pd.Timestamp(a) <= d <= pd.Timestamp(b):
            return name
    return None


def window_returns(daily_equity, initial_equity):
    """Return {window: pct_return} from the daily MTM equity curve, chaining
    daily returns within each window (§2.5: window return from the daily curve)."""
    r = daily_returns(daily_equity, initial_equity)
    out = {name: 1.0 for name, _, _ in WINDOWS}
    seen = {name: False for name, _, _ in WINDOWS}
    for (date, _), ret in zip(daily_equity, r):
        w = _window_of(date)
        if w is not None:
            out[w] *= (1 + ret)
            seen[w] = True
    return {name: (out[name] - 1.0 if seen[name] else None) for name, _, _ in WINDOWS}


# ── instrument + trade concentration ──────────────────────────────────────
def instrument_net(trades):
    net = {}
    for t in trades:
        net[t.symbol] = net.get(t.symbol, 0.0) + t.pnl_usd
    return net


def instrument_profit_share(trades):
    """Each instrument's share of total profit = net / sum(positive nets).
    Bounded; the §4 gate is max share > 0.30."""
    net = instrument_net(trades)
    denom = sum(v for v in net.values() if v > 0)
    if denom == 0:
        return {s: 0.0 for s in net}, 0.0
    share = {s: (v / denom if v > 0 else v / denom) for s, v in net.items()}
    max_share = max((v / denom) for v in net.values()) if net else 0.0
    return share, max_share


def top_five_trade_contribution(trades):
    """Top-five-TRADE profit share (§4 interpretation band, NOT a gate):
    sum(top 5 net winners) / sum(all positive net P&L)."""
    pos = sorted([t.pnl_usd for t in trades if t.pnl_usd > 0], reverse=True)
    if not pos:
        return 0.0
    return sum(pos[:5]) / sum(pos)


# ── episode-block bootstrap (transitive overlap) ──────────────────────────
def _overlap(a, b):
    return a[0] <= b[1] and b[0] <= a[1]


def episodes(trades):
    """Group trades into episodes by portfolio-wide TRANSITIVE overlap of
    holding periods [entry_date, exit_date]. Returns list of lists of trades."""
    intervals = [(pd.Timestamp(t.entry_date).normalize(),
                  pd.Timestamp(t.exit_date).normalize()) for t in trades]
    n = len(trades)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    for i in range(n):
        for j in range(i + 1, n):
            if _overlap(intervals[i], intervals[j]):
                union(i, j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(trades[i])
    return list(groups.values())


def episode_bootstrap_ci(trades, seed=BOOTSTRAP_SEED, samples=BOOTSTRAP_SAMPLES,
                         ci=BOOTSTRAP_CI):
    """90% CI for total net P&L by resampling EPISODES with replacement.
    Returns (lo, hi, n_episodes). Episode-block preserves within-episode
    correlation (overlapping trades move together)."""
    eps = episodes(trades)
    if not eps:
        return (0.0, 0.0, 0)
    ep_pnl = np.array([sum(t.pnl_usd for t in e) for e in eps])
    rng = np.random.default_rng(seed)
    k = len(ep_pnl)
    idx = rng.integers(0, k, size=(samples, k))
    totals = ep_pnl[idx].sum(axis=1)
    lo = float(np.percentile(totals, (1 - ci) / 2 * 100))
    hi = float(np.percentile(totals, (1 + ci) / 2 * 100))
    return (round(lo, 2), round(hi, 2), k)


# ── mechanical Phase 3b decision tree ─────────────────────────────────────
def evaluate_3b(metrics: dict) -> dict:
    """Apply the frozen §4 gates mechanically. `metrics` keys:
      trade_count, pf_base, sharpe, net_base, net_stress, windows_positive,
      max_instrument_share, top5_removal_adjusted, initial_oos_equity,
      max_drawdown_base.
    Returns {gates: {name: bool}, verdict: str, reasons: [..]}. The verdict is a
    pure function of the flags — computed, not narrated."""
    m = metrics
    gates = {
        "trades>=100": m["trade_count"] >= 100,
        "PF>=1.15": m["pf_base"] >= 1.15,
        "Sharpe>=0.50": m["sharpe"] >= 0.50,
        "net_positive_base": m["net_base"] > 0,
        "net_positive_1.5x": m["net_stress"] > 0,
        ">=3of4_windows_positive": m["windows_positive"] >= 3,
        "no_instrument>30%": m["max_instrument_share"] <= 0.30,
        "top5_removal>=-2%": m["top5_removal_adjusted"] >= -0.02 * m["initial_oos_equity"],
        "max_dd<=15%": m["max_drawdown_base"] <= 0.15,
    }
    hard_dd_fail = m["max_drawdown_base"] > 0.15
    substantive = {k: v for k, v in gates.items() if k != "trades>=100"}

    if hard_dd_fail:
        verdict = "FAIL"
        reasons = ["max drawdown > 15% — hard fail, not offsettable by any other metric"]
    elif not gates["trades>=100"]:
        verdict = "PROVISIONAL"
        reasons = [f"only {m['trade_count']} pooled OOS trades (<100) — provisional by "
                   "pre-registration regardless of PF/return"]
    elif all(substantive.values()):
        verdict = "PASS"
        reasons = ["all frozen gates hold and >=100 trades"]
    else:
        verdict = "FAIL"
        reasons = ["failed gates: " + ", ".join(k for k, v in substantive.items() if not v)]
    return {"gates": gates, "verdict": verdict, "reasons": reasons}
