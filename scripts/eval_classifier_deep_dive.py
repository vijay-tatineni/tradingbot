#!/usr/bin/env python3
"""
Three follow-up analyses on the regime-classifier backfill that
Script 3 produced. No new API calls — re-reads today's rows from
regime_classification_log and re-fetches yfinance bars to recompute
forward metrics for each backfill classification.

Section 1: TRENDING vs UNCLEAR alone (not the combined RANGING+UNCLEAR
           bucket Script 3 used). Isolates the trade-vs-don't-trade
           question.

Section 2: Within each regime, split confidence ≥ 0.75 vs < 0.75 and
           ask whether high-confidence calls predict larger forward
           moves than low-confidence calls of the same label.

Section 3: Per-instrument breakdown — regime label counts, per-regime
           mean forward move (only where n ≥ 3), modal label + modal
           agreement from today's consistency runs, and a flag for
           instruments whose mean |move| is materially larger than the
           cross-instrument aggregate for the same regime.

Cohort: today's wall-clock backfill log rows
   (trading_date != today AND cache_hit = 0 AND error IS NULL).
Today's consistency runs come from the same log, filtered to
trading_date == today.
"""
import argparse
import importlib.util
import json
import math
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from dotenv import load_dotenv                       # noqa: E402
load_dotenv(PROJECT_DIR / ".env")

from bot.regime.features import compute_regime_features  # noqa: E402

# Reuse forward-metric + yfinance helpers without copy-paste.
_efr_spec = importlib.util.spec_from_file_location(
    "_efr", SCRIPT_DIR / "eval_classifier_forward_returns.py")
_efr = importlib.util.module_from_spec(_efr_spec)
_efr_spec.loader.exec_module(_efr)

REGIME_DB = PROJECT_DIR / "regime.db"
INSTRUMENTS_FILE = PROJECT_DIR / "instruments.json"
DOCS_DIR = PROJECT_DIR / "docs"

REGIMES = ("TRENDING", "RANGING", "UNCLEAR")
HIGH_CONF_THRESHOLD = 0.75
MIN_INSTRUMENT_N = 3
FLAG_RATIO = 1.5  # mean |move| ≥ FLAG_RATIO × aggregate → flag


def load_enabled_instruments() -> list[dict]:
    cfg = json.loads(INSTRUMENTS_FILE.read_text())
    return [i for i in cfg["layer1_active"] if i.get("enabled")]


def load_backfill_meta(db_path: Path, day: str) -> list[dict]:
    """Return today's backfill log rows: classifications whose
    trading_date is historical (not equal to wall-clock day)."""
    if not db_path.exists():
        return []
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT instrument, trading_date, raw_regime, confidence "
            "FROM regime_classification_log "
            "WHERE date(ts) = ? AND trading_date != ? AND cache_hit = 0 "
            "AND error IS NULL AND raw_regime IS NOT NULL "
            "ORDER BY id",
            (day, day),
        ).fetchall()
    return [{"instrument": i, "trading_date": d, "raw_regime": r,
             "confidence": c} for i, d, r, c in rows]


def load_consistency_meta(db_path: Path, day: str) -> dict:
    """Per-instrument modal label + modal agreement from today's
    consistency rows (trading_date == today). Combines all consistency
    runs landing on the same wall-clock day."""
    if not db_path.exists():
        return {}
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT instrument, raw_regime, confidence "
            "FROM regime_classification_log "
            "WHERE date(ts) = ? AND trading_date = ? AND cache_hit = 0 "
            "AND error IS NULL AND raw_regime IS NOT NULL",
            (day, day),
        ).fetchall()
    by_inst = defaultdict(list)
    for i, r, c in rows:
        by_inst[i].append({"raw_regime": r, "confidence": c})
    out = {}
    for inst, calls in by_inst.items():
        labels = [c["raw_regime"] for c in calls]
        modal_label, modal_count = Counter(labels).most_common(1)[0]
        out[inst] = {
            "n_calls": len(calls),
            "modal_label": modal_label,
            "modal_agreement": modal_count / len(calls),
        }
    return out


def attach_forward_metrics(meta_samples: list[dict],
                           instruments: list[dict]) -> list[dict]:
    """Fetch yfinance bars per instrument, recompute features at each
    backfill bar to extract atr_14, compute forward 20-day metrics.
    Drops rows where the forward window doesn't fit in the bar history."""
    inst_by_sym = {i["symbol"]: i for i in instruments}
    by_inst = defaultdict(list)
    for s in meta_samples:
        by_inst[s["instrument"]].append(s)

    samples: list[dict] = []
    for sym, rows in by_inst.items():
        inst = inst_by_sym.get(sym)
        if inst is None:
            print(f"  {sym}: no instrument mapping, skipping")
            continue
        yf_sym = _efr.yf_symbol(inst)
        if yf_sym is None:
            continue
        print(f"  {sym} → {yf_sym}: fetching...", end="", flush=True)
        df = _efr.fetch_daily_bars(yf_sym)
        if df is None or df.empty:
            print(" no data")
            continue
        attached = 0
        for row in rows:
            tdate = row["trading_date"]
            idx = _efr._bar_index_for_date(df, tdate)
            if idx is None or idx < 200:
                continue
            try:
                features = compute_regime_features(df.iloc[: idx + 1])
            except Exception:
                continue
            atr = features.get("atr_14")
            metrics = _efr.compute_forward_metrics(df, tdate, atr)
            if metrics is None:
                continue
            samples.append({
                "instrument": sym,
                "trading_date": tdate,
                "raw_regime": row["raw_regime"],
                "confidence": row["confidence"],
                **metrics,
            })
            attached += 1
        print(f" {len(rows)} rows → {attached} forward-evaluable")
    return samples


# ── stats helpers ──────────────────────────────────────────────

def _values(samples: list[dict], key: str) -> list[float]:
    return [s[key] for s in samples if s.get(key) is not None]


def _mean_or_none(values: list[float]):
    return statistics.mean(values) if values else None


def _cohens_d(a: list[float], b: list[float]):
    if len(a) < 2 or len(b) < 2:
        return None
    sa, sb = statistics.stdev(a), statistics.stdev(b)
    pooled = math.sqrt(((len(a) - 1) * sa ** 2 + (len(b) - 1) * sb ** 2)
                       / (len(a) + len(b) - 2))
    if pooled == 0:
        return None
    return (statistics.mean(a) - statistics.mean(b)) / pooled


# ── Section 1 — TRENDING vs UNCLEAR ──────────────────────────

def section1_trending_vs_unclear(samples: list[dict]) -> dict:
    trend = [s for s in samples if s["raw_regime"] == "TRENDING"]
    unc = [s for s in samples if s["raw_regime"] == "UNCLEAR"]
    a_dir = _values(trend, "directional_move_atr")
    b_dir = _values(unc, "directional_move_atr")
    a_abs = _values(trend, "abs_move_atr")
    b_abs = _values(unc, "abs_move_atr")
    return {
        "n_trending": len(trend),
        "n_unclear": len(unc),
        "mean_directional_trending": _mean_or_none(a_dir),
        "mean_directional_unclear": _mean_or_none(b_dir),
        "mean_abs_trending": _mean_or_none(a_abs),
        "mean_abs_unclear": _mean_or_none(b_abs),
        "cohens_d_directional": _cohens_d(a_dir, b_dir),
        "cohens_d_abs": _cohens_d(a_abs, b_abs),
    }


# ── Section 2 — Confidence splits ───────────────────────────

def section2_confidence_splits(samples: list[dict],
                               threshold: float = HIGH_CONF_THRESHOLD
                               ) -> dict:
    out = {}
    for regime in REGIMES:
        bucket = [s for s in samples if s["raw_regime"] == regime
                  and s.get("confidence") is not None]
        high = [s for s in bucket if s["confidence"] >= threshold]
        low = [s for s in bucket if s["confidence"] < threshold]
        out[regime] = {
            "n_high": len(high),
            "n_low": len(low),
            "mean_directional_high":
                _mean_or_none(_values(high, "directional_move_atr")),
            "mean_directional_low":
                _mean_or_none(_values(low, "directional_move_atr")),
            "mean_abs_high":
                _mean_or_none(_values(high, "abs_move_atr")),
            "mean_abs_low":
                _mean_or_none(_values(low, "abs_move_atr")),
            "cohens_d_directional": _cohens_d(
                _values(high, "directional_move_atr"),
                _values(low, "directional_move_atr"),
            ),
            "cohens_d_abs": _cohens_d(
                _values(high, "abs_move_atr"),
                _values(low, "abs_move_atr"),
            ),
        }
    return out


# ── Section 3 — Per-instrument ──────────────────────────────

def section3_per_instrument(samples: list[dict], consistency: dict,
                            min_n: int = MIN_INSTRUMENT_N,
                            flag_ratio: float = FLAG_RATIO) -> dict:
    by_inst: dict[str, list[dict]] = defaultdict(list)
    for s in samples:
        by_inst[s["instrument"]].append(s)

    # Cross-instrument aggregate mean |move| per regime, used as
    # baseline for the "more consistent than aggregate" flag.
    agg_abs = {}
    for regime in REGIMES:
        bucket = [s for s in samples if s["raw_regime"] == regime]
        agg_abs[regime] = _mean_or_none(_values(bucket, "abs_move_atr"))

    out = {}
    for inst, rows in by_inst.items():
        counts = Counter(r["raw_regime"] for r in rows)
        per_regime = {}
        flags: list[str] = []
        for regime in REGIMES:
            bucket = [r for r in rows if r["raw_regime"] == regime]
            n = len(bucket)
            mean_dir = None
            mean_abs = None
            if n >= min_n:
                mean_dir = _mean_or_none(
                    _values(bucket, "directional_move_atr"))
                mean_abs = _mean_or_none(_values(bucket, "abs_move_atr"))
                agg = agg_abs.get(regime)
                if (agg is not None and mean_abs is not None
                        and agg > 0 and mean_abs >= flag_ratio * agg):
                    flags.append(
                        f"{regime}: |move|={mean_abs:.2f} ATR vs "
                        f"aggregate {agg:.2f} (n={n})"
                    )
            per_regime[regime] = {
                "n": n,
                "mean_directional": mean_dir,
                "mean_abs": mean_abs,
            }
        cons = consistency.get(inst, {})
        out[inst] = {
            "counts": dict(counts),
            "per_regime": per_regime,
            "modal_label": cons.get("modal_label"),
            "modal_agreement": cons.get("modal_agreement"),
            "consistency_n_calls": cons.get("n_calls"),
            "flags": flags,
        }
    return out, agg_abs


# ── Rendering ─────────────────────────────────────────────────

def _fmt(v, digits: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if v == 0:
            return f"{v:.{digits}f}"
        return f"{v:+.{digits}f}"
    return str(v)


def render_report(s1: dict, s2: dict, s3: dict, s3_aggregate: dict,
                  generated_at: str, n_samples: int) -> str:
    lines = [
        "# Classifier deep-dive analysis",
        "",
        f"_Generated: {generated_at}_",
        f"_Cohort: today's backfill log rows ({n_samples} "
        "forward-evaluable samples). No new API calls._",
        "",
        "## Section 1 — TRENDING vs UNCLEAR alone",
        "",
        "Repeats Script 3's Cohen's d calculation but isolates "
        "TRENDING vs UNCLEAR — the narrow trade-vs-don't-trade comparison "
        "— rather than TRENDING vs (RANGING + UNCLEAR).",
        "",
    ]
    if s1["n_trending"] == 0 or s1["n_unclear"] == 0:
        lines.append("_Insufficient samples in one or both buckets._")
    else:
        lines += [
            f"- n(TRENDING) = {s1['n_trending']}, n(UNCLEAR) = "
            f"{s1['n_unclear']}",
            f"- Mean directional move (ATR): TRENDING "
            f"{_fmt(s1['mean_directional_trending'])}, UNCLEAR "
            f"{_fmt(s1['mean_directional_unclear'])}",
            f"- Mean |move| (ATR): TRENDING "
            f"{_fmt(s1['mean_abs_trending'])}, UNCLEAR "
            f"{_fmt(s1['mean_abs_unclear'])}",
            f"- Cohen's d (directional, TRENDING − UNCLEAR): "
            f"**{_fmt(s1['cohens_d_directional'])}**",
            f"- Cohen's d (|move|, TRENDING − UNCLEAR): "
            f"**{_fmt(s1['cohens_d_abs'])}**",
        ]

    lines += [
        "",
        f"## Section 2 — Confidence calibration "
        f"(high ≥ {HIGH_CONF_THRESHOLD}, low < {HIGH_CONF_THRESHOLD})",
        "",
        "For each regime label, do high-confidence calls precede larger "
        "forward moves than low-confidence calls of the same label? "
        "If confidence is informative, expect high − low > 0 within "
        "TRENDING and the absolute-move column to grow with confidence.",
        "",
        "| Regime | n high | n low | High mean dir | Low mean dir | "
        "High |move| | Low |move| | Cohen's d (dir, high−low) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for regime in REGIMES:
        r = s2[regime]
        if r["n_high"] + r["n_low"] == 0:
            lines.append(
                f"| {regime} | 0 | 0 | — | — | — | — | _insufficient data_ |"
            )
            continue
        if r["n_high"] == 0 or r["n_low"] == 0:
            lines.append(
                f"| {regime} | {r['n_high']} | {r['n_low']} | "
                f"{_fmt(r['mean_directional_high'])} | "
                f"{_fmt(r['mean_directional_low'])} | "
                f"{_fmt(r['mean_abs_high'])} | "
                f"{_fmt(r['mean_abs_low'])} | "
                "_one bucket empty_ |"
            )
            continue
        lines.append(
            f"| {regime} | {r['n_high']} | {r['n_low']} | "
            f"{_fmt(r['mean_directional_high'])} | "
            f"{_fmt(r['mean_directional_low'])} | "
            f"{_fmt(r['mean_abs_high'])} | "
            f"{_fmt(r['mean_abs_low'])} | "
            f"{_fmt(r['cohens_d_directional'])} |"
        )

    lines += [
        "",
        "## Section 3 — Per-instrument breakdown",
        "",
        f"Per-regime forward returns shown only where n ≥ "
        f"{MIN_INSTRUMENT_N}. Modal label + agreement come from today's "
        "consistency-run log rows. Flag = a per-instrument regime bucket "
        f"whose mean |move| is ≥ {FLAG_RATIO}× the cross-instrument "
        f"aggregate for that regime.",
        "",
        "Cross-instrument aggregate mean |move| per regime: "
        + ", ".join(
            f"{r} = {_fmt(s3_aggregate.get(r))}"
            for r in REGIMES
        ),
        "",
        "| Instr | Modal | Agree | Counts T/R/U | TRENDING dir | "
        "RANGING dir | UNCLEAR dir | Flags |",
        "|---|---|---|---|---|---|---|---|",
    ]

    def _cell(p: dict) -> str:
        if p["n"] < MIN_INSTRUMENT_N or p["mean_directional"] is None:
            return f"— (n={p['n']})"
        return f"{_fmt(p['mean_directional'])} (n={p['n']})"

    for inst in sorted(s3.keys()):
        r = s3[inst]
        ct = r["counts"].get("TRENDING", 0)
        cr = r["counts"].get("RANGING", 0)
        cu = r["counts"].get("UNCLEAR", 0)
        modal = r.get("modal_label") or "—"
        agree = (f"{r['modal_agreement']*100:.0f}%"
                 if r.get("modal_agreement") is not None else "—")
        pt = r["per_regime"]["TRENDING"]
        pr = r["per_regime"]["RANGING"]
        pu = r["per_regime"]["UNCLEAR"]
        flags = "; ".join(r["flags"]) if r["flags"] else ""
        lines.append(
            f"| {inst} | {modal} | {agree} | {ct}/{cr}/{cu} | "
            f"{_cell(pt)} | {_cell(pr)} | {_cell(pu)} | {flags} |"
        )

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=REGIME_DB)
    parser.add_argument("--day", type=str, default=date.today().isoformat(),
                        help="Wall-clock day to draw log rows from "
                             "(default: today)")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output markdown path (default: "
                             "docs/eval_classifier_deep_dive_<day>.md)")
    args = parser.parse_args()

    print(f"Deep-dive analysis from log rows on wall-clock day "
          f"{args.day}...")

    backfill_meta = load_backfill_meta(args.db, args.day)
    print(f"  Backfill rows in log: {len(backfill_meta)}")
    if not backfill_meta:
        print("  No backfill rows. Run Script 3 with --backfill first.",
              file=sys.stderr)
        return 1

    instruments = load_enabled_instruments()
    samples = attach_forward_metrics(backfill_meta, instruments)
    print(f"  Forward-evaluable samples: {len(samples)}")

    consistency = load_consistency_meta(args.db, args.day)
    print(f"  Consistency-run instruments: {len(consistency)}")

    s1 = section1_trending_vs_unclear(samples)
    s2 = section2_confidence_splits(samples)
    s3, s3_agg = section3_per_instrument(samples, consistency)

    generated_at = datetime.now(timezone.utc).isoformat()
    md = render_report(s1, s2, s3, s3_agg, generated_at, len(samples))

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = args.out or (
        DOCS_DIR / f"eval_classifier_deep_dive_{args.day}.md"
    )
    out_path.write_text(md)
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
