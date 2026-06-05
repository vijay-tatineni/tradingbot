"""Phase 2 nested walk-forward runner (against the FROZEN spec).

STAGED. Default invocation runs ONLY window W1 and prints a train-selection ->
test-execution verification, then STOPS — so we can confirm the loop produces
sane OOS trades before committing to all 4 windows.

  python3 backtest/results/phase2_wf_run.py        # W1 verification only (default)
  python3 backtest/results/phase2_wf_run.py all     # full 4-window run + pooling

Reproduce: PYTHONPATH=/root/trading python3 backtest/results/phase2_wf_run.py
"""
import json
import logging
import sys

logging.disable(logging.INFO)   # silence the simulator's per-fill sizing logs

from backtest.database import get_connection
from backtest.portfolio_sim import build_specs, fx_to_usd
from backtest.walk_forward_phase2 import (
    make_windows, infer_t0, select_in_window, run_test_window,
    PF_BAR, MIN_TRAIN_TRADES, MAX_OPEN, TARGET_NOTIONAL, N_WINDOWS,
)


def load_full_specs():
    data = json.load(open("/root/trading/instruments.json"))
    settings = data.get("settings", {})
    conn = get_connection()
    specs = build_specs(data["layer1_active"], settings, conn, symbols=None)
    conn.close()
    return specs


def cs(c):
    return "£" if c == "GBP" else ("€" if c == "EUR" else "$")


def print_selection(rows, w):
    print(f"\n  TRAIN selection — {w.label()}")
    print(f"  (frozen bar: PF >= {PF_BAR:.2f} AND >= {MIN_TRAIN_TRADES} train trades; "
          f"independent per-instrument run on train data only)")
    print(f"    {'Sym':<6}{'Elig':>5}{'TrTrd':>7}{'TrPF':>8}   {'verdict':<8} reason")
    for r in sorted(rows, key=lambda x: (not x.selected, x.symbol)):
        pf = "  n/a" if r.train_pf != r.train_pf else f"{r.train_pf:6.2f}"  # nan check
        verdict = "SELECT" if r.selected else "  --"
        elig = "yes" if r.eligible else "NO"
        print(f"    {r.symbol:<6}{elig:>5}{r.train_trades:>7}{pf:>8}   {verdict:<8} {r.reason}")
    sel = [r.symbol for r in rows if r.selected]
    print(f"  -> selected ({len(sel)}): {sel}")
    return sel


def verify_test_window(full_specs, selected, w):
    res, test_specs = run_test_window(full_specs, selected, w)
    print(f"\n  TEST execution — {w.label()}")
    if res is None:
        print("    no selected instruments produced a runnable test window.")
        return res
    # Sanity: the test df is sliced to [test_start, test_end), so NO bar beyond
    # the window exists — every OOS entry must be >= test_start and every exit
    # must be < test_end. Both counts MUST be exactly 0 (no grace; a non-zero
    # here is a real leak, not an edge effect).
    ts0, ts1 = w.test_start, w.test_end
    import pandas as pd
    bad_entry = sum(1 for t in res.trades if pd.to_datetime(t.entry_date) < ts0)
    bad_exit = sum(1 for t in res.trades if pd.to_datetime(t.exit_date) >= ts1)
    ok = "OK" if (bad_entry == 0 and bad_exit == 0) else "*** LEAK ***"
    print(f"    OOS trades: {len(res.trades)}   "
          f"(sanity {ok}: {bad_entry} entries < test_start, "
          f"{bad_exit} exits >= test_end — both must be 0)")
    cur = {s.symbol: s.currency for s in test_specs}
    print(f"    Combined OOS P&L (USD, static FX): ${res.total_pnl_usd:,.2f}")
    print(f"    Peak concurrent: {res.peak_concurrent}/{MAX_OPEN}   "
          f"cap-saturation: {res.concurrency_hist.get(MAX_OPEN,0)}/{res.n_timestamps} "
          f"= {res.concurrency_hist.get(MAX_OPEN,0)/max(res.n_timestamps,1)*100:.1f}%")
    print(f"    Per-instrument OOS (PF / trades / net):")
    for sym in selected:
        summ = res.per_symbol_summary.get(sym)
        if summ is None or summ.trade_count == 0:
            print(f"      {sym:<6} no OOS trades")
            continue
        pf = ">99" if summ.profit_factor == float("inf") else f"{summ.profit_factor:.2f}"
        c = cs(cur.get(sym, "USD"))
        print(f"      {sym:<6} PF {pf:>5}  {summ.trade_count:>3} trd  "
              f"net {c}{summ.total_pnl:,.0f}")
    # Show first few trades to eyeball that dates land in the test window.
    print(f"    First OOS trades (date sanity):")
    for t in res.trades[:6]:
        print(f"      {t.symbol:<6} {str(t.entry_date)[:10]} -> {str(t.exit_date)[:10]}  "
              f"{t.direction:<4} pnl {t.pnl:+.2f} ({t.outcome})")
    return res


def main():
    full_specs = load_full_specs()
    t0 = infer_t0(full_specs)
    windows = make_windows(t0)

    print("=" * 100)
    print("  PHASE 2 NESTED WALK-FORWARD — Option A (no param search), frozen spec")
    print(f"  T0 = {t0.date()}   |  {N_WINDOWS} windows: 12mo train / 3mo test, roll 3mo")
    for w in windows:
        print(f"    {w.label()}")
    print("=" * 100)

    mode = sys.argv[1] if len(sys.argv) > 1 else "w1"

    if mode == "w1":
        print("\n### STAGED VERIFICATION — W1 ONLY (not running W2-W4 yet) ###")
        w1 = windows[0]
        rows = select_in_window(full_specs, w1)
        sel = print_selection(rows, w1)
        verify_test_window(full_specs, sel, w1)
        print("\n### STOP — W1 verified. Review before running all 4 windows "
              "(`phase2_wf_run.py all`). ###")
    elif mode == "all":
        print("\n### FULL RUN — all 4 windows + pooled OOS ###")
        raise SystemExit("full-run pooling not enabled in this staged build — "
                         "enable after W1 verification is reviewed.")
    else:
        raise SystemExit(f"unknown mode: {mode!r}")


if __name__ == "__main__":
    main()
