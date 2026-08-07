"""Survivors-only portfolio-loop run (Phase 1b follow-up).

Runs the same single-clock portfolio event loop as portfolio_loop_run.py, but
restricted to the selected survivor sets — to remove the cap-contamination
confound (5-6 instruments -> cap rarely binds -> clean per-instrument PFs) and
read the real focused-portfolio return on deployable capital.

The full-14 contaminated portfolio PFs are recomputed live here (not hardcoded)
so the contamination comparison is self-consistent with this run.

  Set A (strict, PF >= 1.20): SGLN, SSLN, ANET, PLTR, MSFT
  Set B (strict + SCCO):      above + SCCO

Reproduce: PYTHONPATH=/root/trading python3 backtest/results/survivors_loop_run.py
"""
import json
from backtest.database import get_connection
from backtest.portfolio_sim import build_specs, run_portfolio, PortfolioConfig, fx_to_usd

# Committed preliminary per-instrument baseline (preliminary_read_2026-06-04.md):
# sym -> (prelim_PF, prelim_trades)
PRELIM = {
    "SGLN": (9.51, 464), "SSLN": (1.93, 138), "ANET": (1.58, 59),
    "SCCO": (1.33, 94), "PLTR": (1.28, 340), "MSFT": (1.06, 186),
}

SET_A = ["SGLN", "SSLN", "ANET", "PLTR", "MSFT"]
SET_B = SET_A + ["SCCO"]
MAX_OPEN, TARGET = 5, 1000.0

# Walk-forward projection assumption (Phase 2 framing): 6mo train / 3mo test
# rolling over ~24mo span. Test windows tile months 6-24 -> ~6 windows; the
# first 6mo is train-only so ~75% of the timeline is ever OOS-tested.
WF_WINDOWS = 6
WF_OOS_FRACTION = 0.75


def pf_s(v):
    if v == float("inf") or v >= 100:
        return ">99"
    return f"{v:.2f}"


def cs(c):
    return "£" if c == "GBP" else ("€" if c == "EUR" else "$")


def load_specs(symbols):
    data = json.load(open("/root/trading/instruments.json"))
    settings = data.get("settings", {})
    conn = get_connection()
    specs = build_specs(data["layer1_active"], settings, conn, symbols=symbols)
    conn.close()
    return specs


# ── 1. Full-14 contaminated reference (recomputed live) ────────────────────
full_specs = load_specs(None)
full_res = run_portfolio(full_specs, PortfolioConfig(MAX_OPEN, TARGET))
FULL14 = {}  # sym -> (port_pf, port_trd, blk_cap)
for s in full_specs:
    summ = full_res.per_symbol_summary[s.symbol]
    FULL14[s.symbol] = (summ.profit_factor, summ.trade_count,
                        full_res.blocked_by_cap[s.symbol])


def run_set(name, symbols):
    specs = load_specs(symbols)
    res = run_portfolio(specs, PortfolioConfig(MAX_OPEN, TARGET))
    cur = {s.symbol: s.currency for s in specs}

    print("\n" + "=" * 124)
    print(f"  SURVIVORS-ONLY PORTFOLIO LOOP — {name}  ({len(symbols)} instruments, "
          f"{MAX_OPEN}-cap, ${TARGET:.0f} notional, one-per-instrument)")
    print("=" * 124)

    # Per-instrument table: survivors PF vs full-14 contaminated PF.
    hdr = (f"{'Sym':<6}{'Cur':<4}{'PrelimPF':>9}{'Full14PF':>9}{'SurvPF':>8}"
           f"{'PrelimTrd':>10}{'Full14Trd':>10}{'SurvTrd':>8}"
           f"{'blkOpen':>8}{'blkCap':>7}{'Full14Cap':>10}{'Win%':>6}{'NetP&L':>12}{'Comm':>9}")
    print(hdr)
    print("-" * 124)
    rows = []
    for s in specs:
        sym = s.symbol
        summ = res.per_symbol_summary[sym]
        trs = res.per_symbol_trades[sym]
        comm = sum(t.commission for t in trs)
        rows.append({
            "sym": sym, "cur": s.currency, "pf": summ.profit_factor,
            "trd": summ.trade_count, "blk_open": res.blocked_by_open[sym],
            "blk_cap": res.blocked_by_cap[sym], "win": summ.win_rate * 100,
            "net": summ.total_pnl, "comm": comm,
        })
    rows.sort(key=lambda r: (r["pf"] if r["pf"] != float("inf") else 1e9), reverse=True)
    for r in rows:
        sym = r["sym"]
        c = cs(r["cur"])
        f14_pf, f14_trd, f14_cap = FULL14[sym]
        prelim_pf, prelim_trd = PRELIM[sym]
        print(f"{sym:<6}{r['cur']:<4}{pf_s(prelim_pf):>9}{pf_s(f14_pf):>9}{pf_s(r['pf']):>8}"
              f"{prelim_trd:>10}{f14_trd:>10}{r['trd']:>8}"
              f"{r['blk_open']:>8}{r['blk_cap']:>7}{f14_cap:>10}"
              f"{r['win']:>5.0f}%{c+format(r['net'],',.0f'):>12}{c+format(r['comm'],',.0f'):>9}")
    print("-" * 124)

    # Portfolio-level
    deployable = MAX_OPEN * TARGET
    ret_pct = res.total_pnl_usd / deployable * 100
    dd_pct = res.max_drawdown_usd / deployable * 100
    tot_cap = sum(res.blocked_by_cap.values())
    tot_open = sum(res.blocked_by_open.values())
    at_cap = res.concurrency_hist.get(MAX_OPEN, 0)
    avg_conc = sum(n * c for n, c in res.concurrency_hist.items()) / res.n_timestamps
    print(f"  Total trades:                 {len(res.trades)}")
    print(f"  Combined P&L (USD, static FX): ${res.total_pnl_usd:,.2f}")
    print(f"  Deployable capital ({MAX_OPEN}x${TARGET:.0f}): ${deployable:,.0f}")
    print(f"  Return on deployable capital:  {ret_pct:,.1f}%  (~2yr; {ret_pct/2:,.1f}%/yr)")
    print(f"  Realized-trade max drawdown:   ${res.max_drawdown_usd:,.2f} ({dd_pct:.1f}% of deployable)")
    print(f"  Peak concurrent positions:     {res.peak_concurrent} / {MAX_OPEN}")
    print(f"  Avg concurrency:               {avg_conc:.2f} / {MAX_OPEN}")
    print(f"  blocked_by_open={tot_open}   blocked_by_cap={tot_cap}  "
          f"(full-14 blk_cap for these names: {sum(FULL14[s][2] for s in symbols)})")
    print(f"  Cap-saturation: at {MAX_OPEN}/{MAX_OPEN} for {at_cap}/{res.n_timestamps} "
          f"timestamps = {at_cap/res.n_timestamps*100:.1f}%  (full-14 was 29.3%)")
    print(f"  Concurrency distribution:")
    for n in sorted(res.concurrency_hist):
        c = res.concurrency_hist[n]
        bar = "#" * int(50 * c / res.n_timestamps)
        tag = "  <-- AT CAP" if n == MAX_OPEN else ""
        print(f"    {n}: {c:6,} ({c/res.n_timestamps*100:5.1f}%) {bar}{tag}")

    # Sample-size / OOS projection
    print(f"\n  ── Phase 2 sample-size projection (6mo train / 3mo test, "
          f"~{WF_WINDOWS} OOS windows over ~2yr) ──")
    print(f"    {'Sym':<6}{'SurvTrd':>8}{'OOS total*':>11}{'per-window':>12}   flag")
    for r in sorted(rows, key=lambda x: x["trd"]):
        oos_total = r["trd"] * WF_OOS_FRACTION
        per_win = oos_total / WF_WINDOWS
        flag = "TOO THIN — no per-window PF" if per_win < 5 else (
            "thin" if per_win < 10 else "ok")
        print(f"    {r['sym']:<6}{r['trd']:>8}{oos_total:>11.0f}{per_win:>12.1f}   {flag}")
    print(f"    *OOS total = SurvTrd x {WF_OOS_FRACTION:.2f} (first 6mo is train-only).")
    return res, rows


run_set("SET A (strict, PF>=1.20)", SET_A)
run_set("SET B (strict + SCCO)", SET_B)
