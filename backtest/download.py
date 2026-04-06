"""
backtest/download.py — Fetch 2 years of OHLCV bars from IBKR.

Connects using the same contract-building pattern as bot/connection.py
and bot/data.py, but with clientId=99 to avoid conflicting with the
live bot. Stores results in backtest.db via the database module.
"""

from datetime import datetime

from ib_insync import IB, CFD, Stock

from backtest.config import BACKTEST_CLIENT_ID, DATA_FRESHNESS_HOURS
from backtest.database import get_connection, store_bars, data_age_hours


def build_contract(inst: dict):
    """Build an IBKR contract from an instrument dict — mirrors bot/connection.py."""
    if inst["sec_type"] == "CFD":
        return CFD(inst["symbol"], inst["exchange"], inst["currency"])
    return Stock(inst["symbol"], inst["exchange"], inst["currency"])


def connect_ibkr(host: str, port: int) -> IB:
    """Connect to IBKR Gateway with the backtest client ID."""
    ib = IB()
    ib.connect(host, port, clientId=BACKTEST_CLIENT_ID)
    print(f"Connected to IBKR at {host}:{port} (clientId={BACKTEST_CLIENT_ID})")
    return ib


def download_instrument(ib: IB, inst: dict, conn, skip_download: bool = False) -> dict:
    """
    Download daily and 4hr bars for one instrument.
    Returns dict of {timeframe: bar_count} for bars successfully stored.

    Mirrors bot/data.py logic:
    - CFDs use whatToShow='MIDPOINT', stocks use 'TRADES'
    - 2 year lookback with durationStr='2 Y'
    - Pacing: ib.sleep(12) between requests to respect IBKR limits
    """
    symbol = inst["symbol"]
    what_to_show = "MIDPOINT" if inst["sec_type"] == "CFD" else "TRADES"
    result = {}

    contract = build_contract(inst)
    try:
        ib.qualifyContracts(contract)
    except Exception as e:
        print(f"  {symbol}: failed to qualify contract — {e}")
        return result

    timeframes = [
        ("daily", "1 day"),
        ("4hr", "4 hours"),
    ]

    for tf_name, bar_size in timeframes:
        # Skip logic
        if skip_download:
            age = data_age_hours(conn, symbol, tf_name)
            if age is not None and age < DATA_FRESHNESS_HOURS:
                print(f"  {symbol} {tf_name}: data is {age:.1f} hours old, "
                      f"skipping (use --fresh to force)")
                result[tf_name] = -1  # skipped
                continue

        print(f"  Downloading {symbol} {tf_name}...", end=" ", flush=True)
        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr="2 Y",
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=True,
            )

            if not bars:
                print("no data returned")
                ib.sleep(12)
                continue

            # Convert to dicts for storage
            bar_dicts = []
            for b in bars:
                dt = b.date
                if hasattr(dt, "isoformat"):
                    dt_str = dt.isoformat()
                else:
                    dt_str = str(dt)
                bar_dicts.append({
                    "datetime": dt_str,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": getattr(b, "volume", 0) or 0,
                })

            count = store_bars(conn, symbol, tf_name, bar_dicts)
            print(f"{count} bars")
            result[tf_name] = count

        except Exception as e:
            print(f"error — {e}")

        # Pacing: respect IBKR 60-requests-per-10-min limit
        ib.sleep(12)

    return result


def download_all(ib: IB, instruments: list, skip_download: bool = False) -> dict:
    """
    Download data for all instruments.
    Returns {symbol: {timeframe: bar_count}}.
    """
    conn = get_connection()
    results = {}

    print(f"\n{'='*60}")
    print(f"  Downloading historical data for {len(instruments)} instruments")
    print(f"{'='*60}\n")

    for i, inst in enumerate(instruments, 1):
        symbol = inst["symbol"]
        print(f"[{i}/{len(instruments)}] {symbol} ({inst['name']})")
        results[symbol] = download_instrument(ib, inst, conn, skip_download)

    conn.close()
    return results
