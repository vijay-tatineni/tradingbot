#!/usr/bin/env python3
"""
Generate specs/regime_labels.json for the classifier-vs-human evaluation.

For each enabled IBKR instrument, partition the last 90 trading days into
overlapping 6-day windows (step 5 → ~18 windows per instrument), compute
the classifier's deterministic features at each window's end date, plus a
few window-internal aggregates, and write a worksheet with my_label /
my_confidence / my_notes placeholders for the human labeller.

Bar source: yfinance. The bot's classifier reads IBKR bars; yfinance is
the closer proxy for what TradingView shows the labeller, which is the
right basis for human-vs-classifier comparison. Small numeric drift
between the two bar sources is expected and accepted.

Re-runs preserve any labels already entered in the existing file.
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

import pandas as pd                              # noqa: E402
import yfinance as yf                            # noqa: E402

from bot.regime.features import compute_regime_features  # noqa: E402

INSTRUMENTS_FILE = PROJECT_DIR / "instruments.json"
OUTPUT_FILE = PROJECT_DIR / "specs" / "regime_labels.json"

WINDOW_SIZE = 6       # trading days per window
WINDOW_STEP = 5       # advance between window starts
LOOKBACK_DAYS = 90    # how many trading days back to cover
FETCH_PERIOD = "2y"   # fetch generously so MA200 has room


def yf_symbol(inst: dict):
    """Map an IBKR instrument dict to its yfinance ticker."""
    sym = inst["symbol"]
    ccy = inst.get("currency")
    mkt = inst.get("market")
    if mkt == "LSE":
        return f"{sym}.L"
    if ccy == "EUR":
        return f"{sym}.PA"
    if ccy == "USD":
        return sym
    return None


def load_enabled_instruments():
    cfg = json.loads(INSTRUMENTS_FILE.read_text())
    return [i for i in cfg["layer1_active"] if i.get("enabled")]


def fetch_daily_bars(yf_sym: str):
    try:
        ticker = yf.Ticker(yf_sym)
        df = ticker.history(period=FETCH_PERIOD, interval="1d",
                            auto_adjust=False)
    except Exception as e:
        print(f"  fetch failed: {e}", file=sys.stderr)
        return None
    if df.empty:
        return None
    df = df.rename(columns={"Open": "open", "High": "high",
                            "Low": "low", "Close": "close",
                            "Volume": "volume"})
    return df[["open", "high", "low", "close", "volume"]]


def _round_features(features: dict) -> dict:
    out = {}
    for k, v in features.items():
        if isinstance(v, float):
            out[k] = round(v, 4)
        else:
            out[k] = v
    return out


def build_windows(symbol: str, df: pd.DataFrame) -> list:
    """Return a list of window dicts. Each covers WINDOW_SIZE trading days
    within the most recent LOOKBACK_DAYS of df. Classifier features are
    computed using bars up to and including the window's end bar — that's
    the snapshot the classifier would have seen at that point in time.
    """
    if len(df) < 200 + WINDOW_SIZE:
        return []

    last_n = df.iloc[-LOOKBACK_DAYS:]
    n = len(last_n)
    if n < WINDOW_SIZE:
        return []

    starts = list(range(0, n - WINDOW_SIZE + 1, WINDOW_STEP))
    if not starts or starts[-1] + WINDOW_SIZE < n:
        starts.append(n - WINDOW_SIZE)

    windows = []
    for start_idx in starts:
        end_idx = start_idx + WINDOW_SIZE
        win = last_n.iloc[start_idx:end_idx]
        start_date = win.index[0].strftime("%Y-%m-%d")
        end_date = win.index[-1].strftime("%Y-%m-%d")

        start_price = float(win.iloc[0]["open"])
        end_price = float(win.iloc[-1]["close"])
        pct_change = ((end_price - start_price) / start_price * 100
                      if start_price else 0.0)
        range_high = float(win["high"].max())
        range_low = float(win["low"].min())
        bar_ranges_sum = float((win["high"] - win["low"]).sum())
        range_eff_window = (abs(end_price - start_price) / bar_ranges_sum
                            if bar_ranges_sum else 0.0)

        end_pos_in_df = df.index.get_loc(win.index[-1])
        bars_up_to_end = df.iloc[: end_pos_in_df + 1]
        try:
            classifier_features = compute_regime_features(bars_up_to_end)
        except Exception as e:
            print(f"  feature compute failed for {symbol} {end_date}: {e}",
                  file=sys.stderr)
            classifier_features = {}

        windows.append({
            "window_id": f"{symbol}_{start_date}_{end_date}",
            "start_date": start_date,
            "end_date": end_date,
            "start_price": round(start_price, 4),
            "end_price": round(end_price, 4),
            "pct_change": round(pct_change, 2),
            "range_high": round(range_high, 4),
            "range_low": round(range_low, 4),
            "range_efficiency_window": round(range_eff_window, 3),
            "classifier_features_at_end": _round_features(classifier_features),
            "my_label": None,
            "my_confidence": None,
            "my_notes": None,
        })
    return windows


def _load_existing_labels() -> dict:
    """Return {(instrument, window_id): {label, confidence, notes}} from a
    previous run, so re-generating doesn't wipe work in progress."""
    if not OUTPUT_FILE.exists():
        return {}
    try:
        existing = json.loads(OUTPUT_FILE.read_text())
    except Exception as e:
        print(f"  WARN: could not read existing labels: {e}", file=sys.stderr)
        return {}
    preserved = {}
    for entry in existing.get("instruments", []):
        inst_sym = entry["instrument"]
        for w in entry.get("windows", []):
            if w.get("my_label") is not None:
                preserved[(inst_sym, w["window_id"])] = {
                    "my_label": w.get("my_label"),
                    "my_confidence": w.get("my_confidence"),
                    "my_notes": w.get("my_notes"),
                }
    return preserved


def main() -> int:
    instruments = load_enabled_instruments()
    print(f"Building worksheet for {len(instruments)} instruments...")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing_labels = _load_existing_labels()

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_size_days": WINDOW_SIZE,
        "window_step_days": WINDOW_STEP,
        "lookback_days": LOOKBACK_DAYS,
        "bar_source": "yfinance",
        "instruments": [],
    }
    total_windows = 0
    preserved = 0
    skipped: list[str] = []

    for inst in instruments:
        sym = inst["symbol"]
        yf_sym = yf_symbol(inst)
        if yf_sym is None:
            print(f"  {sym}: no yfinance mapping, skipping")
            skipped.append(sym)
            continue
        print(f"  {sym} → {yf_sym}: fetching...", end="", flush=True)
        df = fetch_daily_bars(yf_sym)
        if df is None or df.empty:
            print(" no data")
            skipped.append(sym)
            continue
        windows = build_windows(sym, df)
        for w in windows:
            key = (sym, w["window_id"])
            if key in existing_labels:
                w.update(existing_labels[key])
                preserved += 1
        print(f" {len(df)} bars → {len(windows)} windows")
        output["instruments"].append({
            "instrument": sym,
            "yfinance_symbol": yf_sym,
            "name": inst.get("name"),
            "currency": inst.get("currency"),
            "windows": windows,
        })
        total_windows += len(windows)

    OUTPUT_FILE.write_text(json.dumps(output, indent=2) + "\n")
    print(f"\nWrote {OUTPUT_FILE}")
    print(f"  {len(output['instruments'])} instruments, "
          f"{total_windows} total windows")
    if preserved:
        print(f"  {preserved} existing labels preserved")
    if skipped:
        print(f"  skipped (no data): {', '.join(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
