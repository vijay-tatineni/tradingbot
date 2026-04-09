"""
backtest/run.py — Main orchestrator for the backtest framework.

Two modes:
    walkforward (default) — sliding-window optimisation with grid search
    backtest              — simple run with fixed params, shows every trade

Usage:
    python3 -m backtest.run --profile paper
    python3 -m backtest.run --profile paper --mode backtest --symbol BARC --stop 4.0 --tp 12.0
    python3 -m backtest.run --profile paper --mode backtest
"""

import argparse
import json
import sys
from pathlib import Path

from backtest.config import DEFAULT_TRAIN_MONTHS, DEFAULT_TEST_MONTHS
from backtest.database import get_connection, load_bars
from backtest.download import connect_ibkr, download_all
from backtest.walk_forward import run_walk_forward
from backtest.report import generate_report

BASE_DIR = Path(__file__).parent.parent


def load_instruments_and_settings(profile: str) -> tuple[list[dict], dict, str, int]:
    """
    Load instruments and connection settings.

    Scenario A: profiles.json exists — read profile for host, port, instruments file.
    Scenario B: profiles.json doesn't exist — read from instruments.json directly.

    Returns: (instruments_list, indicator_settings, host, port)
    """
    profiles_path = BASE_DIR / "profiles.json"

    if profiles_path.exists():
        # Scenario A: multi-instance setup
        with open(profiles_path) as f:
            profiles = json.load(f)
        if profile not in profiles:
            print(f"ERROR: Profile '{profile}' not found in profiles.json")
            print(f"Available: {list(profiles.keys())}")
            sys.exit(1)
        p = profiles[profile]
        host = p["host"]
        port = p["port"]
        inst_file = BASE_DIR / p["instruments_file"]
        with open(inst_file) as f:
            data = json.load(f)
    else:
        # Scenario B: single-instance — read instruments.json directly
        inst_path = BASE_DIR / "instruments.json"
        if not inst_path.exists():
            print("ERROR: instruments.json not found")
            sys.exit(1)
        with open(inst_path) as f:
            data = json.load(f)
        host = data["settings"]["host"]
        port = data["settings"]["port"]

    # Collect ALL layer1_active instruments (enabled AND disabled)
    instruments = data.get("layer1_active", [])
    settings = data.get("settings", {})

    return instruments, settings, host, port


def parse_args():
    parser = argparse.ArgumentParser(
        description="Backtest framework for CogniflowAI Trading Bot"
    )
    parser.add_argument(
        "--profile", required=True,
        help="Profile name (reads from profiles.json or labels output)"
    )
    parser.add_argument(
        "--symbol", default=None,
        help="Test a single instrument symbol (default: all)"
    )
    parser.add_argument(
        "--skip-download", action="store_true",
        help="Skip IBKR download if data is less than 24 hours old"
    )
    parser.add_argument(
        "--mode", default="walkforward",
        choices=["walkforward", "backtest", "deep-optimise"],
        help="walkforward (default), backtest (fixed params), or deep-optimise (indicator + stop/TP grid)"
    )
    parser.add_argument(
        "--stop", type=float, default=None,
        help="Trail stop %% for backtest mode (default: from instruments.json)"
    )
    parser.add_argument(
        "--tp", type=float, default=None,
        help="Take profit %% for backtest mode (default: from instruments.json)"
    )
    parser.add_argument(
        "--llm-filter", action="store_true",
        help="Enable LLM pattern analysis filter (slow — validates each signal with LLM)"
    )
    parser.add_argument(
        "--train-months", type=int, default=DEFAULT_TRAIN_MONTHS,
        help=f"Training window size in months (default: {DEFAULT_TRAIN_MONTHS})"
    )
    parser.add_argument(
        "--test-months", type=int, default=DEFAULT_TEST_MONTHS,
        help=f"Test window size in months (default: {DEFAULT_TEST_MONTHS})"
    )
    return parser.parse_args()


def _download_data(args, instruments, host, port):
    """Handle IBKR data download (shared by both modes). Returns fresh_download bool."""
    ib = None
    fresh_download = not args.skip_download
    try:
        if not args.skip_download:
            ib = connect_ibkr(host, port)
            download_all(ib, instruments, skip_download=False)
        else:
            conn = get_connection()
            from backtest.database import data_age_hours
            need_download = False
            for inst in instruments:
                age = data_age_hours(conn, inst["symbol"], "daily")
                if age is None:
                    need_download = True
                    break
            conn.close()

            if need_download:
                ib = connect_ibkr(host, port)
                download_all(ib, instruments, skip_download=True)
            else:
                print("\n  All data is cached, skipping IBKR connection")
    except Exception as e:
        print(f"\nIBKR connection/download failed: {e}")
        print("Continuing with any cached data in backtest.db...")
        fresh_download = False

    if ib is not None:
        try:
            ib.disconnect()
            print("Disconnected from IBKR")
        except Exception:
            pass

    return fresh_download


def _load_dataframe(conn, inst):
    """Load the best available timeframe DataFrame for an instrument."""
    symbol = inst["symbol"]
    timeframe = inst.get("timeframe", "daily")
    tf_db = timeframe
    df = load_bars(conn, symbol, tf_db)

    if df.empty and tf_db != "daily":
        df = load_bars(conn, symbol, "daily")
        if not df.empty:
            tf_db = "daily"

    return df, tf_db


def _run_walkforward_mode(args, instruments, settings, enabled_count, fresh_download):
    """Run walk-forward analysis (existing mode)."""
    print(f"\n{'='*60}")
    print(f"  Running walk-forward analysis")
    print(f"{'='*60}")

    conn = get_connection()
    wf_results = []

    for inst in instruments:
        symbol = inst["symbol"]
        df, tf_db = _load_dataframe(conn, inst)

        if df.empty:
            print(f"\n  {symbol}: no data in backtest.db, skipping")
            continue

        print(f"\n  {symbol}: {len(df)} {tf_db} bars "
              f"({df['datetime'].iloc[0].date()} to {df['datetime'].iloc[-1].date()})")

        try:
            # Resolve per-instrument indicator settings
            inst_indicator_settings = _resolve_indicator_settings(settings, inst)
            result = run_walk_forward(
                symbol=symbol,
                df=df,
                indicator_settings=inst_indicator_settings,
                instrument_config=inst,
                train_months=args.train_months,
                test_months=args.test_months,
            )
            if result is not None:
                wf_results.append(result)
        except Exception as e:
            print(f"  {symbol}: walk-forward failed — {e}")
            import traceback
            traceback.print_exc()

    conn.close()

    if not wf_results:
        print("\nNo walk-forward results generated. Check data and parameters.")
        sys.exit(1)

    generate_report(
        results=wf_results,
        profile=args.profile,
        train_months=args.train_months,
        test_months=args.test_months,
        total_instruments=len(instruments),
        enabled_count=enabled_count,
        fresh_download=fresh_download,
        instruments=instruments,
    )


def _run_backtest_mode(args, instruments, settings):
    """Run simple backtest with fixed params."""
    from backtest.simple_backtest import (
        run_simple_backtest, generate_backtest_report,
    )

    print(f"\n{'='*60}")
    print(f"  Running simple backtest")
    print(f"{'='*60}")

    conn = get_connection()
    bt_results = []

    for inst in instruments:
        symbol = inst["symbol"]
        df, tf_db = _load_dataframe(conn, inst)

        if df.empty:
            print(f"\n  {symbol}: no data in backtest.db, skipping")
            continue

        # Determine stop/TP: CLI args override, else instrument config
        stop_pct = args.stop if args.stop is not None else inst.get("trail_stop_pct", 2.0)
        tp_pct = args.tp if args.tp is not None else inst.get("take_profit_pct", 8.0)

        print(f"\n  {symbol}: {len(df)} {tf_db} bars "
              f"({df['datetime'].iloc[0].date()} to {df['datetime'].iloc[-1].date()})")

        try:
            inst_indicator_settings = _resolve_indicator_settings(settings, inst)
            result = run_simple_backtest(
                symbol=symbol,
                df=df,
                stop_pct=stop_pct,
                tp_pct=tp_pct,
                indicator_settings=inst_indicator_settings,
                instrument_config=inst,
            )
            if result is not None:
                bt_results.append(result)
        except Exception as e:
            print(f"  {symbol}: backtest failed — {e}")
            import traceback
            traceback.print_exc()

    conn.close()

    if not bt_results:
        print("\nNo backtest results generated. Check data and parameters.")
        sys.exit(1)

    generate_backtest_report(
        results=bt_results,
        profile=args.profile,
        single_symbol=args.symbol is not None,
    )


def _run_deep_optimise_mode(args, instruments, settings):
    """Run deep optimisation: indicator + stop/TP grid search for all instruments."""
    import time as _time
    from backtest.grid_search import full_optimise
    from backtest.database import store_optimise_result
    import datetime

    print(f"\n{'='*60}")
    print(f"  Deep Optimisation — indicator + stop/TP grid search")
    print(f"{'='*60}")

    conn = get_connection()
    run_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    overall_start = _time.time()
    results = []

    for inst in instruments:
        symbol = inst["symbol"]
        df, tf_db = _load_dataframe(conn, inst)

        if df.empty:
            print(f"\n  {symbol}: no data in backtest.db, skipping")
            continue

        print(f"\n  {symbol}: {len(df)} {tf_db} bars "
              f"({df['datetime'].iloc[0].date()} to {df['datetime'].iloc[-1].date()})")

        inst_settings = _resolve_indicator_settings(settings, inst)

        try:
            result = full_optimise(
                symbol=symbol,
                df=df,
                base_indicator_settings=inst_settings,
                instrument_config=inst,
                train_months=args.train_months,
                test_months=args.test_months,
            )
            if result is not None:
                results.append(result)
                # Save to DB
                store_optimise_result(conn, {
                    "run_date": run_date,
                    "symbol": symbol,
                    "best_stop_pct": result.best_stop_pct,
                    "best_tp_pct": result.best_tp_pct,
                    "best_rsi_period": result.best_indicators.get("rsi_period"),
                    "best_rsi_oversold": result.best_indicators.get("rsi_oversold"),
                    "best_rsi_overbought": result.best_indicators.get("rsi_overbought"),
                    "best_wr_period": result.best_indicators.get("williams_r_period"),
                    "best_adx_threshold": result.best_indicators.get("adx_threshold"),
                    "best_ma_period": result.best_indicators.get("ma200_period"),
                    "wf_efficiency": result.wf_efficiency,
                    "oos_pnl": result.oos_pnl,
                    "oos_profit_factor": result.oos_profit_factor,
                    "oos_win_rate": result.oos_win_rate,
                    "oos_trade_count": result.oos_trade_count,
                    "current_oos_pnl": result.current_oos_pnl,
                    "improvement_pct": result.improvement_pct,
                    "combos_tested": result.combos_tested,
                    "duration_seconds": result.duration_seconds,
                })
        except Exception as e:
            print(f"  {symbol}: deep optimisation failed — {e}")
            import traceback
            traceback.print_exc()

    conn.close()

    # Print summary
    total_duration = _time.time() - overall_start
    hours = int(total_duration // 3600)
    mins = int((total_duration % 3600) // 60)

    print(f"\n{'='*65}")
    print(f"  Deep Optimisation Report — {run_date}")
    print(f"  Duration: {hours}h {mins}m | {len(results)} instruments")
    print(f"{'='*65}")
    print(f"{'Symbol':<8} {'Current OOS':>12} {'Optimal OOS':>12} {'Change':>8} {'Key Changes'}")
    print(f"{'─'*8} {'─'*12} {'─'*12} {'─'*8} {'─'*30}")

    for r in results:
        change = f"+{r.improvement_pct:.0f}%" if r.improvement_pct > 0 else f"{r.improvement_pct:.0f}%"
        changes = []
        ind = r.best_indicators
        if ind.get("rsi_period", 14) != 14:
            changes.append(f"RSI 14->{ind['rsi_period']}")
        if ind.get("adx_threshold", 20) != 20:
            changes.append(f"ADX 20->{ind['adx_threshold']}")
        if ind.get("ma200_period", 200) != 200:
            changes.append(f"MA 200->{ind['ma200_period']}")
        change_str = ", ".join(changes) if changes else "No significant change"
        print(f"{r.symbol:<8} ${r.current_oos_pnl:>10,.0f} ${r.oos_pnl:>10,.0f} {change:>8} {change_str}")


def _resolve_indicator_settings(global_settings: dict, instrument: dict) -> dict:
    """Resolve per-instrument indicator settings with global fallbacks."""
    defaults = {
        "rsi_period": global_settings.get("rsi_period", 14),
        "rsi_oversold": global_settings.get("rsi_oversold", 35),
        "rsi_overbought": global_settings.get("rsi_overbought", 70),
        "williams_r_period": global_settings.get("williams_r_period", 14),
        "williams_r_mid": global_settings.get("williams_r_mid", -50),
        "williams_r_oversold": global_settings.get("williams_r_oversold", -80),
        "williams_r_overbought": global_settings.get("williams_r_overbought", -20),
        "adx_period": global_settings.get("adx_period", 14),
        "adx_threshold": global_settings.get("adx_threshold", 20),
        "ma200_period": global_settings.get("ma200_period", 200),
        "alligator_min_gap_pct": global_settings.get("alligator_min_gap_pct", 0.003),
    }
    overrides = instrument.get("indicators", {})
    for key, val in overrides.items():
        if val is not None:
            defaults[key] = val
    return defaults


def main():
    args = parse_args()

    mode_label = "Walk-Forward" if args.mode == "walkforward" else "Simple Backtest"
    print(f"\n{'='*60}")
    print(f"  CogniflowAI Backtest Framework — {mode_label}")
    print(f"  Profile: {args.profile}")
    if args.mode == "walkforward":
        print(f"  Train: {args.train_months} months | Test: {args.test_months} months")
    else:
        stop_str = f"{args.stop}%" if args.stop else "from instruments.json"
        tp_str = f"{args.tp}%" if args.tp else "from instruments.json"
        print(f"  Stop: {stop_str} | TP: {tp_str}")
    print(f"{'='*60}")

    # Load instruments and settings
    instruments, settings, host, port = load_instruments_and_settings(args.profile)
    enabled_count = sum(1 for i in instruments if i.get("enabled", True))

    # Filter to single symbol if requested
    if args.symbol:
        instruments = [i for i in instruments if i["symbol"] == args.symbol]
        if not instruments:
            print(f"ERROR: Symbol '{args.symbol}' not found in instruments")
            sys.exit(1)

    print(f"  Instruments: {len(instruments)} "
          f"({'all' if not args.symbol else args.symbol})")

    # Download data (shared by both modes)
    fresh_download = _download_data(args, instruments, host, port)

    # Branch on mode
    if args.mode == "walkforward":
        _run_walkforward_mode(args, instruments, settings, enabled_count, fresh_download)
    elif args.mode == "deep-optimise":
        _run_deep_optimise_mode(args, instruments, settings)
    else:
        _run_backtest_mode(args, instruments, settings)


if __name__ == "__main__":
    main()
