"""
backtest/breakout_run_3b.py — Phase 3b: four-window chronological OOS portfolio
evaluation over the UNTOUCHED OOS period (2025-03-01 .. 2026-02-28).

Full frozen 14-instrument universe, ONE global rule, NO selection, NO tuning,
NO 3a-derived shortlist. Indicators are computed on full history (warm-up) then
the run is sliced to the OOS window so trades fire only on OOS signals and the
portfolio starts in cash at OOS start (§2.5 window-boundary mechanics).

Uses the locked engine (breakout_strategy/sim/metrics/report) unchanged.
"""
import sqlite3

import pandas as pd

from backtest.database import load_bars
from backtest.breakout_strategy import compute_indicators
from backtest.breakout_sim import (
    BreakoutInstrument, run_breakout_portfolio, classify_and_cost,
    DEFAULT_INITIAL_CAPITAL_USD,
)
from backtest.simulator import CostConfig, classify_instrument
from backtest import breakout_metrics as M
from backtest import breakout_report as R

OOS_START = pd.Timestamp("2025-03-01", tz="UTC")
OOS_END = pd.Timestamp("2026-02-28", tz="UTC")


def load_universe(path="instruments.json"):
    import json
    d = json.load(open(path))
    out, oi = [], 0
    for inst in d["layer1_active"]:
        if not inst.get("enabled"):
            continue
        out.append((oi, inst))
        oi += 1
    return out


def _cost_for(currency, sec_type, mode):
    """Base cost model, or a 1.5x stress on spread+slippage ONLY (commission is
    contractual, unscaled). Built explicitly here — NOT via simulator's locked
    'pessimistic' preset, which is 2.0x, not the frozen 1.5x stress gate."""
    base = CostConfig.for_class(classify_instrument(currency, sec_type), "base")
    if mode == "base":
        return base
    if mode == "stress15":
        return CostConfig(
            commission_per_share=base.commission_per_share,
            commission_min=base.commission_min,
            commission_max_pct=base.commission_max_pct,
            half_spread_bps=base.half_spread_bps * 1.5,
            slippage_bps=base.slippage_bps * 1.5,
            gap_fill_at_open=base.gap_fill_at_open,
        )
    raise ValueError(mode)


def build_oos_instruments(conn, mode="base"):
    insts = []
    for order_idx, inst in load_universe():
        sym = inst["symbol"]
        ind = compute_indicators(load_bars(conn, sym, "daily"))   # warm-up on full history
        oos = ind[(ind["datetime"] >= OOS_START) & (ind["datetime"] <= OOS_END)].reset_index(drop=True)
        assert oos["datetime"].min() >= OOS_START and oos["datetime"].max() <= OOS_END
        currency = inst.get("currency", "USD")
        sec_type = inst.get("sec_type", "STK")
        cost = _cost_for(currency, sec_type, mode)
        insts.append(BreakoutInstrument(symbol=sym, order_idx=order_idx, df=oos,
                                        currency=currency, sec_type=sec_type, cost_config=cost))
    return insts


def daily_equity_series(res):
    """Collapse the per-event-group equity curve to one point per calendar date
    (the last group of each date = end-of-date MTM)."""
    by_date = {}
    for ts, eq in res.equity_curve_usd:
        d = str(pd.Timestamp(ts).date())
        by_date[d] = eq                      # last write per date wins (US session after EU)
    return [(d, by_date[d]) for d in sorted(by_date)]


def run(conn, initial=DEFAULT_INITIAL_CAPITAL_USD):
    base_insts = build_oos_instruments(conn, "base")
    res = run_breakout_portfolio(base_insts, initial_capital_usd=initial, liquidate_at_end=True)
    stress_insts = build_oos_instruments(conn, "stress15")        # frozen 1.5x cost stress
    res_s = run_breakout_portfolio(stress_insts, initial_capital_usd=initial, liquidate_at_end=True)

    trades = res.trades
    de = daily_equity_series(res)
    de_s = daily_equity_series(res_s)

    net_base = round(sum(t.pnl_usd for t in trades), 2)
    net_stress = round(sum(t.pnl_usd for t in res_s.trades), 2)
    pf_base = M.profit_factor(trades)
    sh = R.sharpe(de, initial)
    mdd_base = R.max_drawdown(de, initial)
    mdd_stress = R.max_drawdown(de_s, initial)
    wr = R.window_returns(de, initial)
    windows_pos = sum(1 for v in wr.values() if v is not None and v > 0)
    share, max_share = R.instrument_profit_share(trades)
    top5_share = R.top_five_trade_contribution(trades)
    _orig, top5_adj, removed = M.remove_top_five(trades)
    boot = R.episode_bootstrap_ci(trades)
    inst_net = R.instrument_net(trades)

    metrics = dict(
        trade_count=len(trades), pf_base=pf_base, sharpe=sh, net_base=net_base,
        net_stress=net_stress, windows_positive=windows_pos,
        max_instrument_share=max_share, top5_removal_adjusted=top5_adj,
        initial_oos_equity=initial, max_drawdown_base=mdd_base,
    )
    decision = R.evaluate_3b(metrics)

    return dict(
        res=res, res_s=res_s, trades=trades, daily_equity=de,
        net_base=net_base, net_stress=net_stress, pf_base=pf_base, sharpe=sh,
        sortino=R.sortino(de, initial), vol=R.annualized_vol(de, initial),
        cagr=R.cagr(de, initial), calmar=R.calmar(de, initial),
        mdd_base=mdd_base, mdd_stress=mdd_stress, window_returns=wr,
        windows_pos=windows_pos, inst_net=inst_net, inst_share=share,
        max_share=max_share, top5_share=top5_share, top5_adj=top5_adj,
        removed=removed, bootstrap=boot, metrics=metrics, decision=decision,
        initial=initial,
    )


def _path_coverage(res):
    reasons = {}
    for t in res.trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    n_cap = sum(1 for b in res.blocked if b.reason == "cap")
    return reasons, n_cap, len(res.contention_events)


def format_report(out):
    L, p = [], None
    L = []
    def p(s=""): L.append(s)
    d = out
    tc = len(d["trades"])
    provisional = tc < 100
    reasons, n_cap, n_cont = _path_coverage(d["res"])

    p("=" * 80)
    p("PHASE 3b — FOUR-WINDOW CHRONOLOGICAL OOS PORTFOLIO EVALUATION")
    p("Full frozen 14 universe · one global rule · no selection · no tuning · OOS 2025-03-01..2026-02-28")
    p("=" * 80)
    p("")
    p(">>> TOTAL POOLED OOS TRADE COUNT: %d <<<" % tc)
    if provisional:
        p(">>> HEADLINE: PROVISIONAL by pre-registration — under 100 pooled OOS trades. <<<")
        p(">>> A good-looking PF/return on a thin sample does NOT read as a pass. <<<")
    else:
        p(">>> Sample size >= 100: gates evaluated on a full-power sample. <<<")
    if provisional:
        p("")
        p("    Note: on a sub-100 sample the windows-positive, Sharpe, and bootstrap")
        p("    gates degrade MECHANICALLY (flat windows score 0; mostly-zero daily")
        p("    returns; few episodes => wide CI). A spuriously high PF/Sharpe cannot")
        p("    manufacture a PASS — PROVISIONAL governs. The frozen rule stands.")

    p("")
    p("--- Pre-registration interpretation (recorded, not a silent choice) ---")
    p("  OOS-slicing: indicators computed on FULL history (warm-up), run sliced to")
    p("  the OOS window. Pre-OOS bars are used ONLY for warm-up (§2.5): a signal on")
    p("  the last pre-OOS bar does not fill into OOS. Portfolio starts in cash at OOS")
    p("  start. 1.5x stress scales spread+slippage x1.5 (commission unscaled).")

    p("")
    p("--- Code-path coverage in the REAL OOS run (3b is the first integration of these) ---")
    for path in ["gap_stop", "intra_stop", "trend_break", "END_OF_TEST_LIQUIDATION"]:
        n = reasons.get(path, 0)
        status = "FIRED (%d)" % n if n else "did NOT fire — still unit-test-only"
        p(f"  {path:24} {status}")
    p(f"  {'5-cap block':24} {'FIRED (%d)' % n_cap if n_cap else 'did NOT fire — still unit-test-only'}")
    p(f"  {'same-ts contention':24} {'FIRED (%d)' % n_cont if n_cont else 'did NOT fire — still unit-test-only'}")
    p("  (Any path that did not fire is data-dependent here, not a bug: see notes below.)")

    p("")
    p("--- FROZEN GATES (verdict is a pure function of these flags) ---")
    g = d["decision"]["gates"]
    vals = {
        "trades>=100": f"{tc}",
        "PF>=1.15": f"{d['pf_base']:.3f}",
        "Sharpe>=0.50": f"{d['sharpe']:.3f}",
        "net_positive_base": f"{d['net_base']:,.2f}",
        "net_positive_1.5x": f"{d['net_stress']:,.2f}",
        ">=3of4_windows_positive": f"{d['windows_pos']}/4",
        "no_instrument>30%": f"max {d['max_share']*100:.1f}%",
        "top5_removal>=-2%": f"{d['top5_adj']:,.2f} (limit {-0.02*d['initial']:,.0f})",
        "max_dd<=15%": f"{d['mdd_base']*100:.2f}%",
    }
    for k in g:
        p(f"  [{'PASS' if g[k] else 'FAIL'}] {k:26} value={vals[k]}")

    p("")
    p("--- §5 PORTFOLIO METRIC PANEL (base costs) ---")
    p(f"  Net P&L (USD)        : {d['net_base']:,.2f}    (1.5x stress: {d['net_stress']:,.2f})")
    p(f"  Profit factor        : {d['pf_base']:.3f}")
    p(f"  Sharpe / Sortino     : {d['sharpe']:.3f} / {d['sortino']:.3f}")
    p(f"  CAGR / ann.vol       : {d['cagr']*100:.2f}% / {d['vol']*100:.2f}%")
    p(f"  Max drawdown         : base {d['mdd_base']*100:.2f}%  (1.5x stress {d['mdd_stress']*100:.2f}%)")
    p(f"  Calmar               : {d['calmar']:.3f}")
    p(f"  Worst rolling 12m    : OOS span is exactly 12m -> equals full-period return")

    p("")
    p("--- PER-WINDOW (daily MTM attribution) ---")
    for name, a, b in R.WINDOWS:
        v = d["window_returns"][name]
        p(f"  {name} {a}..{b}: {'n/a' if v is None else f'{v*100:+.2f}%'}")
    p(f"  Windows positive: {d['windows_pos']}/4")

    p("")
    p("--- PER-INSTRUMENT net P&L + profit share ---")
    p(f"  {'SYM':6} {'NET_USD':>12} {'SHARE':>8}")
    for sym in sorted(d["inst_net"], key=lambda s: d["inst_net"][s], reverse=True):
        p(f"  {sym:6} {d['inst_net'][sym]:>12,.2f} {d['inst_share'].get(sym,0)*100:>7.1f}%")

    p("")
    p("--- CONCENTRATION (interpretation, not a gate) ---")
    t5 = d["top5_share"] * 100
    band = "acceptable (<=50%)" if t5 <= 50 else ("yellow flag (50-70%)" if t5 <= 70 else "serious concern (>70%)")
    p(f"  Top-5-trade profit contribution: {t5:.1f}% -> {band}")
    p(f"  Max single-instrument profit share: {d['max_share']*100:.1f}% (gate: <=30%)")

    p("")
    p("--- TOP-5 REMOVAL (arithmetic, no rerun) ---")
    p(f"  Net after removing 5 best trades: {d['top5_adj']:,.2f}  "
      f"(pass if >= {-0.02*d['initial']:,.0f} = -2% of initial OOS equity)")

    p("")
    p("--- EPISODE-BLOCK BOOTSTRAP (transitive overlap, 90% CI, seed %d, 10k samples) ---" % R.BOOTSTRAP_SEED)
    lo, hi, ne = d["bootstrap"]
    p(f"  Episodes: {ne}   90%% CI for total net P&L: [{lo:,.2f}, {hi:,.2f}]")

    p("")
    p("--- END_OF_TEST_LIQUIDATION exits ---")
    eot = [t for t in d["trades"] if t.exit_reason == "END_OF_TEST_LIQUIDATION"]
    p(f"  {len(eot)} position(s) liquidated at OOS endpoint 2026-02-28: "
      f"{', '.join(t.symbol for t in eot) if eot else '(none — all positions closed before endpoint)'}")

    p("")
    p("=" * 80)
    p("MECHANICAL VERDICT (derived from the gate flags above, not from narrative):")
    p(f"  VERDICT: {d['decision']['verdict']}")
    for r in d["decision"]["reasons"]:
        p(f"  - {r}")
    if provisional and d["decision"]["verdict"] != "PROVISIONAL":
        p(f"  - reconciliation: sample is also sub-100 ({tc} trades), so even setting the")
        p(f"    above aside the result could not read better than PROVISIONAL.")
    p("=" * 80)
    return "\n".join(L)


if __name__ == "__main__":
    conn = sqlite3.connect("backtest.db")
    out = run(conn)
    print(format_report(out))
