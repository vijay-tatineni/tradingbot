"""
Preliminary full-universe honest read (read-only, no repo code changed).

Reuses backtest.simple_backtest.run_simple_backtest as-is:
  Fix 1   next-bar-open entry + entry-bar-inclusive scan
  Fix 1b  check-then-ratchet trailing stop
  Fix 2   base commission + half-spread/slippage + gap-through fills
  Fix 3   target_notional sizing ($1,000)

Each instrument runs on its OWN real config from instruments.json
(timeframe, trail_stop_pct, take_profit_pct, long_only, currency, qty).
No portfolio cap, no walk-forward, no OOS split — a sanity read only.
"""
import json
from backtest.database import get_connection, load_bars
from backtest.run import _resolve_indicator_settings
from backtest.simple_backtest import run_simple_backtest

TARGET_NOTIONAL = 1000.0

data = json.load(open("/root/trading/instruments.json"))
settings = data.get("settings", {})
enabled = [i for i in data["layer1_active"] if i.get("enabled")]

conn = get_connection()
rows = []

for inst in enabled:
    symbol = inst["symbol"]
    tf = inst.get("timeframe", "daily")
    df = load_bars(conn, symbol, tf)
    tf_used = tf
    if df.empty and tf != "daily":
        df = load_bars(conn, symbol, "daily")
        tf_used = "daily"
    if df.empty:
        print(f"  {symbol}: NO DATA — skipped")
        continue

    stop_pct = inst.get("trail_stop_pct", 2.0)
    tp_pct = inst.get("take_profit_pct", 8.0)
    ind = _resolve_indicator_settings(settings, inst)

    res = run_simple_backtest(
        symbol=symbol,
        df=df,
        stop_pct=stop_pct,
        tp_pct=tp_pct,
        indicator_settings=ind,
        instrument_config=inst,
        default_target_notional=TARGET_NOTIONAL,
    )
    if res is None:
        print(f"  {symbol}: no result")
        continue

    s = res.summary
    trades = res.trades
    total_comm = sum(t.commission for t in trades)
    n = len(trades)
    avg_comm = total_comm / n if n else 0.0
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    # How often the $1 per-side floor binds: commission of a round trip pinned
    # at ~2.0 means both legs hit the minimum (per-share charge < the floor).
    floor_pinned = sum(1 for t in trades if abs(t.commission - 2.0) < 0.05)
    floor_pct = floor_pinned / n * 100 if n else 0.0
    # P&L before commission, to size the commission drag against edge.
    pnl_gross = s.total_pnl + total_comm

    rows.append({
        "symbol": symbol,
        "tf": tf_used,
        "cur": res.currency,
        "stop": stop_pct,
        "tp": tp_pct,
        "trades": n,
        "win_rate": s.win_rate * 100,
        "pnl": s.total_pnl,
        "pnl_gross": pnl_gross,
        "pf": s.profit_factor,
        "comm": total_comm,
        "avg_comm": avg_comm,
        "floor_pct": floor_pct,
        "avg_hold": s.avg_holding_bars,
        "gp": gross_profit,
        "gl": gross_loss,
    })

conn.close()

# Sort by profit factor descending (inf sorts to top)
rows.sort(key=lambda r: r["pf"], reverse=True)


def cur_sym(c):
    return "£" if c == "GBP" else "$"


print("\n" + "=" * 120)
print("  PRELIMINARY FULL-UNIVERSE HONEST READ  (per-instrument, no portfolio cap, no walk-forward/OOS)")
print("  Fixes 1+1b+2+3:  next-open entry | check-then-ratchet | base costs | $1,000 target_notional")
print("  Sorted by after-cost profit factor descending")
print("=" * 120)
hdr = (f"{'Sym':<6}{'TF':<6}{'Cur':<4}{'Stp%':>5}{'TP%':>5}{'Trd':>5}"
       f"{'Win%':>7}{'NetP&L':>12}{'PF':>7}{'Comm':>11}{'Cm/Tr':>8}"
       f"{'Flr%':>6}{'Hold':>7}")
print(hdr)
print("-" * 120)
for r in rows:
    pf = ">99" if r["pf"] == float("inf") or r["pf"] >= 100 else f"{r['pf']:.2f}"
    cs = cur_sym(r["cur"])
    print(f"{r['symbol']:<6}{r['tf']:<6}{r['cur']:<4}{r['stop']:>5.1f}{r['tp']:>5.1f}"
          f"{r['trades']:>5}{r['win_rate']:>6.0f}%"
          f"{cs+format(r['pnl'],',.0f'):>12}{pf:>7}"
          f"{cs+format(r['comm'],',.0f'):>11}{cs+format(r['avg_comm'],'.2f'):>8}"
          f"{r['floor_pct']:>5.0f}%{r['avg_hold']:>7.0f}")
print("-" * 120)

# Buckets (PF thresholds on after-cost numbers)
pf_above_120 = [r for r in rows if r["pf"] >= 1.20]
pf_above_100 = [r for r in rows if r["pf"] >= 1.00 and r["pf"] < 1.20]
pf_above_100_all = [r for r in rows if r["pf"] >= 1.00]
pf_sub_be = [r for r in rows if r["pf"] < 1.00]

print(f"\nBuckets (after-cost profit factor):")
print(f"  PF >= 1.20 (Phase 2 bar):   {len(pf_above_120):2d}  -> {[r['symbol'] for r in pf_above_120]}")
print(f"  1.00 <= PF < 1.20:          {len(pf_above_100):2d}  -> {[r['symbol'] for r in pf_above_100]}")
print(f"  PF >= 1.00 (any positive):  {len(pf_above_100_all):2d}")
print(f"  PF <  1.00 (sub-breakeven): {len(pf_sub_be):2d}  -> {[r['symbol'] for r in pf_sub_be]}")

# Commission-drag diagnostic
print(f"\nCommission-floor diagnostic (does the $1/side minimum dominate?):")
print(f"  {'Sym':<6}{'AvgCm/Tr':>10}{'Floor-pinned%':>15}{'Comm/|NetEdge|':>16}{'NetP&L':>12}{'GrossP&L':>12}")
for r in rows:
    cs = cur_sym(r["cur"])
    drag_ratio = (r["comm"] / abs(r["pnl"])) if r["pnl"] != 0 else float("inf")
    dr = ">9" if drag_ratio == float("inf") or drag_ratio > 9 else f"{drag_ratio:.2f}"
    print(f"  {r['symbol']:<6}{cs+format(r['avg_comm'],'.2f'):>10}{r['floor_pct']:>14.0f}%"
          f"{dr:>16}{cs+format(r['pnl'],',.0f'):>12}{cs+format(r['pnl_gross'],',.0f'):>12}")

# How many would flip sign if commission were zero?
flip = [r for r in rows if r["pnl"] < 0 and r["pnl_gross"] > 0]
print(f"\n  Instruments net-negative AFTER cost but positive BEFORE commission: "
      f"{len(flip)} -> {[r['symbol'] for r in flip]}")
avg_floor = sum(r["floor_pct"] for r in rows) / len(rows) if rows else 0
print(f"  Mean floor-pinned trade %% across universe: {avg_floor:.0f}%")
