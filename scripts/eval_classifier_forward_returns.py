#!/usr/bin/env python3
"""
Forward-returns evaluation for the regime classifier.

Two modes:

  default (live):  Walk every row already in regime_classification_cache.
                   For each row, look up the actual subsequent 20 trading
                   days from yfinance and compute the directional move
                   (in ATR units) and range efficiency. Aggregate by the
                   classifier's regime label. No API calls.

  --backfill N:    Walk the past N trading days per instrument, step 5
                   bars, compute features from yfinance bars as of each
                   date, call the classifier on those features (temp
                   cache, prod CostTracker), then compute the same
                   forward metrics. Costs API budget.

Key question the report answers:
  Do TRENDING calls precede larger directional moves than RANGING /
  UNCLEAR calls on average?

Reports mean ± SE per regime, the pairwise difference, and Cohen's d
for TRENDING vs not-TRENDING — so the answer is an effect size, not
just a direction.
"""
import argparse
import json
import math
import sqlite3
import statistics
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from dotenv import load_dotenv                       # noqa: E402
load_dotenv(PROJECT_DIR / ".env")

import pandas as pd                              # noqa: E402
import yfinance as yf                            # noqa: E402

from bot.regime.cache import RegimeCache             # noqa: E402
from bot.regime.classifier import RegimeClassifier   # noqa: E402
from bot.regime.cost_tracker import CostTracker      # noqa: E402
from bot.regime.features import compute_regime_features  # noqa: E402

REGIME_DB = PROJECT_DIR / "regime.db"
INSTRUMENTS_FILE = PROJECT_DIR / "instruments.json"
DOCS_DIR = PROJECT_DIR / "docs"

FORWARD_DAYS = 20
DEFAULT_BACKFILL_DAYS = 90
BACKFILL_STEP = 5
FETCH_PERIOD = "2y"

REGIMES = ("TRENDING", "RANGING", "UNCLEAR")


def yf_symbol(inst: dict):
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


def load_enabled_instruments() -> list[dict]:
    cfg = json.loads(INSTRUMENTS_FILE.read_text())
    return [i for i in cfg["layer1_active"] if i.get("enabled")]


def fetch_daily_bars(yf_sym: str):
    try:
        df = yf.Ticker(yf_sym).history(period=FETCH_PERIOD, interval="1d",
                                       auto_adjust=False)
    except Exception as e:
        print(f"  fetch failed: {e}", file=sys.stderr)
        return None
    if df.empty:
        return None
    df = df.rename(columns={"Open": "open", "High": "high",
                            "Low": "low", "Close": "close",
                            "Volume": "volume"})
    df = df[["open", "high", "low", "close", "volume"]].copy()
    # Normalise the index to plain naive Timestamps at midnight UTC so
    # date-string lookups match cleanly.
    df.index = pd.to_datetime(df.index).normalize().tz_localize(None)
    return df


def _bar_index_for_date(df: pd.DataFrame, target_date: str):
    """Return the integer location of the bar whose date equals
    target_date, or None if absent."""
    try:
        ts = pd.Timestamp(target_date)
    except Exception:
        return None
    # exact match preferred
    if ts in df.index:
        return df.index.get_loc(ts)
    # fall back: first bar >= target_date (handles weekends / holidays)
    later = df.index[df.index >= ts]
    if len(later) == 0:
        return None
    return df.index.get_loc(later[0])


def compute_forward_metrics(df: pd.DataFrame, classification_date: str,
                            atr_at_classification: float | None,
                            forward_days: int = FORWARD_DAYS) -> dict | None:
    """Return forward 20-day metrics for the bar matching
    classification_date, or None if there aren't enough subsequent
    bars in df."""
    if df is None or df.empty:
        return None
    idx = _bar_index_for_date(df, classification_date)
    if idx is None or idx + forward_days >= len(df):
        return None
    close_t = float(df.iloc[idx]["close"])
    close_fwd = float(df.iloc[idx + forward_days]["close"])
    net_change = close_fwd - close_t

    forward_window = df.iloc[idx + 1: idx + 1 + forward_days]
    bar_ranges_sum = float((forward_window["high"]
                            - forward_window["low"]).sum())
    forward_range_eff = (abs(net_change) / bar_ranges_sum
                         if bar_ranges_sum else 0.0)

    if atr_at_classification and atr_at_classification > 0:
        dir_move_atr = net_change / atr_at_classification
        abs_move_atr = abs(net_change) / atr_at_classification
    else:
        dir_move_atr = None
        abs_move_atr = None

    return {
        "close_t": close_t,
        "close_fwd": close_fwd,
        "net_change": net_change,
        "directional_move_atr": dir_move_atr,
        "abs_move_atr": abs_move_atr,
        "forward_range_efficiency": forward_range_eff,
        "forward_days": forward_days,
    }


def aggregate_by_regime(samples: list[dict]) -> dict:
    """Aggregate per-regime stats from forward-metric samples.

    Each sample dict needs: raw_regime, directional_move_atr,
    abs_move_atr, forward_range_efficiency.
    Returns per-regime stats and a TRENDING vs not-TRENDING effect-size.
    """
    by_regime: dict[str, list[dict]] = defaultdict(list)
    for s in samples:
        if s.get("raw_regime") in REGIMES:
            by_regime[s["raw_regime"]].append(s)

    per_regime = {}
    for regime in REGIMES:
        bucket = by_regime.get(regime, [])
        moves = [b["directional_move_atr"] for b in bucket
                 if b.get("directional_move_atr") is not None]
        abs_moves = [b["abs_move_atr"] for b in bucket
                     if b.get("abs_move_atr") is not None]
        range_effs = [b["forward_range_efficiency"] for b in bucket
                      if b.get("forward_range_efficiency") is not None]
        per_regime[regime] = {
            "n": len(bucket),
            "directional_move_atr_mean":
                statistics.mean(moves) if moves else None,
            "directional_move_atr_stdev":
                statistics.stdev(moves) if len(moves) > 1 else 0.0,
            "directional_move_atr_se":
                (statistics.stdev(moves) / math.sqrt(len(moves)))
                if len(moves) > 1 else 0.0,
            "abs_move_atr_mean":
                statistics.mean(abs_moves) if abs_moves else None,
            "abs_move_atr_stdev":
                statistics.stdev(abs_moves) if len(abs_moves) > 1 else 0.0,
            "forward_range_eff_mean":
                statistics.mean(range_effs) if range_effs else None,
            "forward_range_eff_stdev":
                statistics.stdev(range_effs) if len(range_effs) > 1 else 0.0,
        }

    # Effect size: TRENDING vs (RANGING + UNCLEAR), on directional_move_atr
    trend_moves = [b["directional_move_atr"]
                   for b in by_regime.get("TRENDING", [])
                   if b.get("directional_move_atr") is not None]
    other_moves = [
        b["directional_move_atr"]
        for r in ("RANGING", "UNCLEAR")
        for b in by_regime.get(r, [])
        if b.get("directional_move_atr") is not None
    ]
    trend_abs = [b["abs_move_atr"] for b in by_regime.get("TRENDING", [])
                 if b.get("abs_move_atr") is not None]
    other_abs = [b["abs_move_atr"] for r in ("RANGING", "UNCLEAR")
                 for b in by_regime.get(r, [])
                 if b.get("abs_move_atr") is not None]

    def _cohens_d(a, b):
        if len(a) < 2 or len(b) < 2:
            return None
        sa, sb = statistics.stdev(a), statistics.stdev(b)
        pooled = math.sqrt(((len(a) - 1) * sa ** 2
                            + (len(b) - 1) * sb ** 2)
                           / (len(a) + len(b) - 2))
        if pooled == 0:
            return None
        return (statistics.mean(a) - statistics.mean(b)) / pooled

    return {
        "per_regime": per_regime,
        "effect_size_trending_vs_other": {
            "n_trending": len(trend_moves),
            "n_other": len(other_moves),
            "mean_diff_directional": (
                statistics.mean(trend_moves) - statistics.mean(other_moves)
                if trend_moves and other_moves else None
            ),
            "cohens_d_directional": _cohens_d(trend_moves, other_moves),
            "mean_diff_abs": (
                statistics.mean(trend_abs) - statistics.mean(other_abs)
                if trend_abs and other_abs else None
            ),
            "cohens_d_abs": _cohens_d(trend_abs, other_abs),
        },
        "n_total_samples": sum(stats["n"] for stats in per_regime.values()),
    }


def _load_cached_classifications(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT instrument, trading_date, classification_json "
        "FROM regime_classification_cache "
        "ORDER BY trading_date ASC, instrument ASC"
    ).fetchall()
    conn.close()
    out = []
    for inst, day, blob in rows:
        try:
            payload = json.loads(blob)
        except Exception:
            continue
        out.append({
            "instrument": inst,
            "trading_date": day,
            "raw_regime": payload.get("raw_regime"),
            "confidence": payload.get("confidence"),
            "features": payload.get("features") or {},
        })
    return out


def run_live_mode(db_path: Path, instruments: list[dict]) -> dict:
    """Pair every cached classification with the forward metrics derived
    from yfinance. Skip rows whose forward window isn't fully populated."""
    classifications = _load_cached_classifications(db_path)
    if not classifications:
        return {"samples": [], "skipped": 0, "no_data": []}

    by_inst: dict[str, list[dict]] = defaultdict(list)
    for c in classifications:
        by_inst[c["instrument"]].append(c)

    inst_lookup = {i["symbol"]: i for i in instruments}
    samples: list[dict] = []
    skipped = 0
    no_data: list[str] = []
    for sym, rows in by_inst.items():
        inst = inst_lookup.get(sym)
        if inst is None:
            no_data.append(sym)
            continue
        yf_sym = yf_symbol(inst)
        if yf_sym is None:
            no_data.append(sym)
            continue
        print(f"  {sym} → {yf_sym}: fetching...", end="", flush=True)
        df = fetch_daily_bars(yf_sym)
        if df is None or df.empty:
            no_data.append(sym)
            print(" no data")
            continue
        per_inst_added = 0
        for row in rows:
            atr = row["features"].get("atr_14")
            metrics = compute_forward_metrics(df, row["trading_date"], atr)
            if metrics is None:
                skipped += 1
                continue
            samples.append({
                "instrument": sym,
                "trading_date": row["trading_date"],
                "raw_regime": row["raw_regime"],
                "confidence": row.get("confidence"),
                **metrics,
            })
            per_inst_added += 1
        print(f" {len(rows)} classifications, {per_inst_added} evaluable")
    return {"samples": samples, "skipped": skipped, "no_data": no_data}


def _trading_day_indices(df: pd.DataFrame, lookback_days: int,
                         step: int, forward_days: int) -> list[int]:
    """Return integer bar indices to backfill: each is at least
    forward_days away from the last bar (so the forward window exists)
    and within the past lookback_days. Walks backward in step-sized
    increments."""
    if len(df) <= forward_days + 1:
        return []
    last_eligible = len(df) - forward_days - 1
    earliest = max(0, len(df) - lookback_days)
    indices = []
    i = last_eligible
    while i >= earliest:
        indices.append(i)
        i -= step
    indices.reverse()
    return indices


def run_backfill_mode(db_path: Path, instruments: list[dict],
                      lookback_days: int, step: int) -> dict:
    today = date.today().isoformat()
    cost_tracker = CostTracker(str(db_path))
    if cost_tracker.is_budget_exceeded(today):
        return {"error": "budget exceeded for today", "samples": []}
    spend_before = cost_tracker.get_daily_spend(today)

    samples: list[dict] = []
    no_data: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        tmp_db = str(Path(td) / "backfill_cache.db")
        cache = RegimeCache(tmp_db)
        classifier = RegimeClassifier(cache, cost_tracker)
        if not classifier.is_available():
            return {"error": "ANTHROPIC_API_KEY not set", "samples": []}

        for inst in instruments:
            sym = inst["symbol"]
            yf_sym = yf_symbol(inst)
            if yf_sym is None:
                no_data.append(sym)
                continue
            print(f"  {sym} → {yf_sym}: fetching...", end="", flush=True)
            df = fetch_daily_bars(yf_sym)
            if df is None or len(df) < 220:
                no_data.append(sym)
                print(" insufficient history")
                continue
            indices = _trading_day_indices(df, lookback_days, step,
                                           FORWARD_DAYS)
            print(f" {len(df)} bars → {len(indices)} backfill points")
            for idx in indices:
                if cost_tracker.is_budget_exceeded(today):
                    print("    budget exceeded mid-backfill — stopping",
                          file=sys.stderr)
                    return {
                        "error": "budget exceeded mid-backfill",
                        "samples": samples,
                        "no_data": no_data,
                    }
                bars_up_to = df.iloc[: idx + 1]
                try:
                    features = compute_regime_features(bars_up_to)
                except Exception as e:
                    print(f"    feature compute failed at {idx}: {e}",
                          file=sys.stderr)
                    continue
                bar_date = df.index[idx].strftime("%Y-%m-%d")
                clf = classifier.classify(sym, bar_date, features, force=True)
                atr = features.get("atr_14")
                metrics = compute_forward_metrics(df, bar_date, atr)
                if metrics is None:
                    continue
                samples.append({
                    "instrument": sym,
                    "trading_date": bar_date,
                    "raw_regime": clf.raw_regime,
                    "confidence": clf.confidence,
                    **metrics,
                })

    spend_after = cost_tracker.get_daily_spend(today)
    return {
        "samples": samples,
        "no_data": no_data,
        "cost": {"spend_before": spend_before,
                 "spend_after": spend_after,
                 "cost_total": spend_after - spend_before},
    }


def _fmt(v, digits=3):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:+.{digits}f}" if digits > 0 and abs(v) > 0 else f"{v:.{digits}f}"
    return str(v)


def render_report(stats: dict, mode: str, run_info: dict,
                  generated_at: str) -> str:
    per_regime = stats["per_regime"]
    es = stats["effect_size_trending_vs_other"]
    n_total = stats["n_total_samples"]

    lines = [
        "# Classifier forward-returns evaluation",
        "",
        f"_Generated: {generated_at}_",
        f"_Mode: **{mode}**, forward window: {FORWARD_DAYS} trading days_",
        "",
    ]

    if "error" in run_info:
        lines += [f"**Run error:** {run_info['error']}", ""]

    lines += [
        "## Per-regime forward metrics",
        "",
        "| Regime | n | Mean dir. move (ATR) | SE | Mean |move| (ATR) "
        "| Mean fwd range eff |",
        "|---|---|---|---|---|---|",
    ]
    for regime in REGIMES:
        r = per_regime[regime]
        if r["n"] == 0:
            lines.append(f"| {regime} | 0 | — | — | — | — |")
            continue
        lines.append(
            f"| {regime} | {r['n']} | "
            f"{_fmt(r['directional_move_atr_mean'])} | "
            f"{_fmt(r['directional_move_atr_se'], 3)} | "
            f"{_fmt(r['abs_move_atr_mean'], 3)} | "
            f"{_fmt(r['forward_range_eff_mean'], 3)} |"
        )

    lines += [
        "",
        "## Effect size: TRENDING vs (RANGING + UNCLEAR)",
        "",
    ]
    if es["n_trending"] == 0 or es["n_other"] == 0:
        lines.append("_Insufficient samples in one or both buckets._")
    else:
        lines += [
            f"- n(TRENDING) = {es['n_trending']}, "
            f"n(other) = {es['n_other']}",
            f"- Mean directional move difference "
            f"(TRENDING − other): **{_fmt(es['mean_diff_directional'])}** ATR",
            f"- Cohen's d (directional): "
            f"**{_fmt(es['cohens_d_directional'])}**",
            f"- Mean |move| difference: "
            f"**{_fmt(es['mean_diff_abs'])}** ATR",
            f"- Cohen's d (|move|): "
            f"**{_fmt(es['cohens_d_abs'])}**",
            "",
            "_Cohen's d interpretation (rough rule of thumb): "
            "0.2 = small, 0.5 = medium, 0.8 = large._",
        ]

    lines += [
        "",
        "## Sample summary",
        "",
        f"- Total samples with forward metrics: **{n_total}**",
    ]
    if run_info.get("skipped"):
        lines.append(
            f"- Classifications skipped for insufficient forward data: "
            f"{run_info['skipped']}"
        )
    if run_info.get("no_data"):
        lines.append(
            f"- Instruments with no bars / no mapping: "
            f"{', '.join(run_info['no_data'])}"
        )
    if "cost" in run_info:
        c = run_info["cost"]
        lines += [
            "",
            "## Cost",
            "",
            f"- Spend before run: ${c.get('spend_before', 0.0):.4f}",
            f"- Spend after  run: ${c.get('spend_after', 0.0):.4f}",
            f"- Cost of this run: ${c.get('cost_total', 0.0):.4f}",
        ]
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill", type=int, default=None,
                        help=f"Run backfill mode over the past N trading "
                             f"days (default mode is live).")
    parser.add_argument("--step", type=int, default=BACKFILL_STEP,
                        help=f"Backfill step in trading days "
                             f"(default {BACKFILL_STEP}).")
    parser.add_argument("--db", type=Path, default=REGIME_DB,
                        help=f"Path to regime.db (default: {REGIME_DB})")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output markdown path (default: "
                             "docs/eval_forward_returns_<YYYY-MM-DD>.md)")
    args = parser.parse_args()

    instruments = load_enabled_instruments()
    print(f"Forward-returns eval over {len(instruments)} instruments...")

    if args.backfill:
        mode = f"backfill ({args.backfill}d, step {args.step})"
        result = run_backfill_mode(args.db, instruments,
                                   lookback_days=args.backfill,
                                   step=args.step)
    else:
        mode = "live (cache + yfinance)"
        result = run_live_mode(args.db, instruments)

    samples = result.get("samples", [])
    stats = aggregate_by_regime(samples)
    generated_at = datetime.now(timezone.utc).isoformat()
    md = render_report(stats, mode, result, generated_at)

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = args.out or (
        DOCS_DIR / f"eval_forward_returns_{date.today().isoformat()}.md"
    )
    out_path.write_text(md)
    print(f"Wrote {out_path}")
    print(f"  {stats['n_total_samples']} forward-evaluable samples")
    if "cost" in result:
        print(f"  cost: ${result['cost'].get('cost_total', 0.0):.4f}")
    if "error" in result:
        print(f"  run error: {result['error']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
