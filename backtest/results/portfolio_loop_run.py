"""Full 14-instrument portfolio-loop run with baseline comparison."""
import json
from backtest.database import get_connection
from backtest.portfolio_sim import build_specs, run_portfolio, PortfolioConfig, fx_to_usd

# Committed preliminary baseline (backtest/results/preliminary_read_2026-06-04.md)
# sym -> (prelim_PF, prelim_trades, win%)
BASE = {
    "SGLN": (9.51, 464, 78), "SSLN": (1.93, 138, 67), "ANET": (1.58, 59, 42),
    "SCCO": (1.33, 94, 69), "PLTR": (1.28, 340, 36), "MSFT": (1.06, 186, 45),
    "BARC": (0.98, 470, 54), "TSM": (0.83, 283, 28), "AAPL": (0.78, 245, 33),
    "AVGO": (0.73, 242, 29), "ANTO": (0.59, 312, 28), "SU": (0.54, 264, 27),
    "NBIS": (0.47, 94, 11), "NVTS": (0.44, 142, 16),
}
WINNERS = {"SGLN", "SSLN", "ANET", "SCCO", "PLTR"}
MAX_OPEN, TARGET = 5, 1000.0

data = json.load(open("/root/trading/instruments.json"))
settings = data.get("settings", {})
conn = get_connection()
specs = build_specs(data["layer1_active"], settings, conn, symbols=None)  # all 14
conn.close()

res = run_portfolio(specs, PortfolioConfig(max_open_positions=MAX_OPEN, target_notional=TARGET))

cur = {s.symbol: s.currency for s in specs}
rows = []
for s in specs:
    sym = s.symbol
    summ = res.per_symbol_summary[sym]
    trs = res.per_symbol_trades[sym]
    comm = sum(t.commission for t in trs)
    pf = summ.profit_factor
    rows.append({
        "sym": sym, "cur": s.currency,
        "prelim_pf": BASE[sym][0], "port_pf": pf,
        "prelim_trd": BASE[sym][1], "port_trd": summ.trade_count,
        "blk_open": res.blocked_by_open[sym], "blk_cap": res.blocked_by_cap[sym],
        "win": summ.win_rate * 100, "net": summ.total_pnl, "comm": comm,
        "winner": sym in WINNERS,
    })

# Sort by portfolio PF descending (inf -> top)
rows.sort(key=lambda r: (r["port_pf"] if r["port_pf"] != float("inf") else 1e9), reverse=True)


def pf_s(v):
    return ">99" if v == float("inf") or v >= 100 else f"{v:.2f}"


def cs(c):
    return "£" if c == "GBP" else "$"


print("\n" + "=" * 132)
print("  FULL-UNIVERSE PORTFOLIO LOOP (Phase 1b) — 14 instruments, single clock, "
      f"{MAX_OPEN}-cap, ${TARGET:.0f} notional, one-per-instrument")
print("  Honest mechanics (Fixes 1+1b+2+3) via shared OpenPosition stepper. "
      "Sorted by portfolio PF desc. * = preliminary 'winner'")
print("=" * 132)
hdr = (f"{'Sym':<6}{'Cur':<4}{'PrelimPF':>9}{'PortPF':>8}{'->':>3}"
       f"{'PrelimTrd':>10}{'PortTrd':>8}{'blkOpen':>8}{'blkCap':>7}"
       f"{'Win%':>6}{'NetP&L':>12}{'Comm':>10}")
print(hdr)
print("-" * 132)
for r in rows:
    star = "*" if r["winner"] else " "
    c = cs(r["cur"])
    shrink = ""
    print(f"{star}{r['sym']:<5}{r['cur']:<4}{pf_s(r['prelim_pf']):>9}{pf_s(r['port_pf']):>8}"
          f"{'':>3}{r['prelim_trd']:>10}{r['port_trd']:>8}{r['blk_open']:>8}{r['blk_cap']:>7}"
          f"{r['win']:>5.0f}%{c+format(r['net'],',.0f'):>12}{c+format(r['comm'],',.0f'):>10}")
print("-" * 132)

# Four-tier summary
survives = [r for r in rows if r["port_pf"] >= 1.20]
marginal = [r for r in rows if 1.0 <= r["port_pf"] < 1.20]
subbe = [r for r in rows if r["port_pf"] < 1.0]
print("\n### FOUR-TIER SUMMARY (after collapse) ###")
print(f"  Survives PF >= 1.20 (real winners):  {len(survives):2d}  "
      f"{[(r['sym'], pf_s(r['port_pf'])) for r in survives]}")
print(f"  Marginal 1.00-1.20:                  {len(marginal):2d}  "
      f"{[(r['sym'], pf_s(r['port_pf'])) for r in marginal]}")
print(f"  Sub-breakeven < 1.00:                {len(subbe):2d}  "
      f"{[(r['sym'], pf_s(r['port_pf'])) for r in subbe]}")
thin = [r for r in rows if r["port_trd"] < 30]
print(f"\n  ⚠ Thin sample (<30 portfolio trades — Phase 2 walk-forward OOS risk): "
      f"{len(thin)}")
for r in sorted(thin, key=lambda x: x["port_trd"]):
    print(f"      {r['sym']:5} {r['port_trd']:3d} trades  (PF {pf_s(r['port_pf'])})")

# Winner shrinkage callout
print("\n### WINNER SHRINKAGE (preliminary PF -> portfolio PF) ###")
for r in [x for x in rows if x["winner"]] + [x for x in rows if not x["winner"] and x["sym"] in ("MSFT","BARC","TSM","AAPL")]:
    tag = "winner" if r["winner"] else "middle"
    print(f"  {r['sym']:5} ({tag}): PF {pf_s(r['prelim_pf'])} -> {pf_s(r['port_pf'])}  "
          f"| trades {r['prelim_trd']} -> {r['port_trd']}")

# Portfolio-level
print("\n### PORTFOLIO-LEVEL ###")
total_trades = len(res.trades)
deployable = MAX_OPEN * TARGET
ret_pct = res.total_pnl_usd / deployable * 100
dd_pct = res.max_drawdown_usd / deployable * 100
tot_blk_cap = sum(res.blocked_by_cap.values())
tot_blk_open = sum(res.blocked_by_open.values())
print(f"  Total trades across universe: {total_trades}")
print(f"  Combined P&L (USD, static FX): ${res.total_pnl_usd:,.2f}")
print(f"  Deployable capital ({MAX_OPEN} slots x ${TARGET:.0f}): ${deployable:,.0f}")
print(f"  Return on deployable capital: {ret_pct:,.1f}%  (over ~2yr, P&L-on-committed-notional)")
print(f"  Max drawdown on real equity curve: ${res.max_drawdown_usd:,.2f} ({dd_pct:.1f}% of deployable)")
print(f"  Peak concurrent positions: {res.peak_concurrent} / {MAX_OPEN}-cap")
print(f"\n  Blocking totals:  blocked_by_open={tot_blk_open:,}   blocked_by_cap={tot_blk_cap:,}")
print(f"  -> does the cap bind, or does one-per-instrument do all the work?")
print(f"  Concurrency distribution (timestamps with N positions held, of {res.n_timestamps:,}):")
for n in sorted(res.concurrency_hist):
    c = res.concurrency_hist[n]
    bar = "#" * int(60 * c / res.n_timestamps)
    at_cap = "  <-- AT CAP" if n == MAX_OPEN else ""
    print(f"    {n} held: {c:6,} ({c/res.n_timestamps*100:5.1f}%) {bar}{at_cap}")
