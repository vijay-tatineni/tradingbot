#!/usr/bin/env python3
"""
Self-consistency probe for the regime classifier.

For each enabled IBKR instrument:
  1. Pull the most recent cached feature set from regime_classification_cache.
  2. Call the classifier 5x with force=True so each call hits the API.
  3. Aggregate: modal label, modal-agreement fraction, confidence stdev.

Instruments with modal agreement < 80% are flagged as "unstable" —
that's the threshold the user picked; it means at least 2 of 5 calls
gave a different regime, which suggests the classifier's view on that
instrument is on a knife edge.

Cache pollution: force=True writes to RegimeCache.put() per call. We
swap in a tempfile-backed RegimeCache so the prod cache rows stay
untouched. The CostTracker stays pointed at the prod DB so daily
budget tracking and the regime_classification_log stay accurate.

Cost: ~$0.003 per call × 5 calls × 14 instruments ≈ $0.21. Refuses to
run if max_daily_cost_usd is already exceeded.
"""
import argparse
import json
import sqlite3
import statistics
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from dotenv import load_dotenv                       # noqa: E402
load_dotenv(PROJECT_DIR / ".env")

from bot.regime.cache import RegimeCache             # noqa: E402
from bot.regime.classifier import RegimeClassifier   # noqa: E402
from bot.regime.cost_tracker import CostTracker      # noqa: E402

REGIME_DB = PROJECT_DIR / "regime.db"
INSTRUMENTS_FILE = PROJECT_DIR / "instruments.json"
DOCS_DIR = PROJECT_DIR / "docs"

N_CALLS = 5
UNSTABLE_THRESHOLD = 0.80


def load_enabled_instruments() -> list[str]:
    cfg = json.loads(INSTRUMENTS_FILE.read_text())
    return [i["symbol"] for i in cfg["layer1_active"] if i.get("enabled")]


def latest_features_per_instrument(db_path: Path) -> dict:
    """Return {symbol: (trading_date, features)} using the most recent
    classification row per instrument."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT instrument, trading_date, classification_json "
        "FROM regime_classification_cache "
        "ORDER BY trading_date DESC"
    ).fetchall()
    conn.close()
    out = {}
    for inst, day, blob in rows:
        if inst in out:
            continue
        try:
            payload = json.loads(blob)
        except Exception:
            continue
        features = payload.get("features")
        if features:
            out[inst] = (day, features)
    return out


def score_calls(calls: list[dict]) -> dict:
    """Aggregate a list of {raw_regime, confidence} dicts into modal
    label, modal-agreement fraction, and confidence variance. Pure
    function — no side effects, easy to test."""
    labels = [c["raw_regime"] for c in calls]
    counter = Counter(labels)
    modal_label, modal_count = counter.most_common(1)[0]
    modal_frac = modal_count / len(labels) if labels else 0.0
    confs = [c["confidence"] for c in calls if c.get("confidence") is not None]
    return {
        "n_calls": len(calls),
        "label_counts": dict(counter),
        "modal_label": modal_label,
        "modal_agreement": modal_frac,
        "confidence_mean": statistics.mean(confs) if confs else None,
        "confidence_stdev": (statistics.stdev(confs) if len(confs) > 1 else 0.0),
        "unstable": modal_frac < UNSTABLE_THRESHOLD,
    }


def render_report(results: dict, cost_info: dict,
                  generated_at: str) -> str:
    lines = [
        "# Classifier self-consistency report",
        "",
        f"_Generated: {generated_at}_",
        f"_Calls per instrument: {N_CALLS}, unstable threshold: "
        f"<{int(UNSTABLE_THRESHOLD*100)}% modal agreement_",
        "",
        "## Per-instrument results",
        "",
        "| Instrument | Features as of | Modal | Agreement | Conf mean | "
        "Conf stdev | Unstable | Calls |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for sym in sorted(results.keys()):
        r = results[sym]
        if "error" in r:
            lines.append(f"| {sym} | — | — | — | — | — | — | "
                         f"error: {r['error']} |")
            continue
        unstable_mark = "**YES**" if r["unstable"] else "no"
        calls_summary = ", ".join(
            f"{c['raw_regime']}({c['confidence']:.2f})"
            if c.get("confidence") is not None else c["raw_regime"]
            for c in r["calls"]
        )
        cm = r["confidence_mean"]
        lines.append(
            f"| {sym} | {r['features_from_date']} | {r['modal_label']} | "
            f"{r['modal_agreement']*100:.0f}% | "
            f"{cm:.2f} | "
            f"{r['confidence_stdev']:.3f} | "
            f"{unstable_mark} | {calls_summary} |"
        )

    # Aggregate
    valid = [r for r in results.values() if "error" not in r]
    if valid:
        agreement_mean = statistics.mean(r["modal_agreement"] for r in valid)
        n_unstable = sum(1 for r in valid if r["unstable"])
        conf_means = [r["confidence_mean"] for r in valid
                      if r["confidence_mean"] is not None]
        lines += [
            "",
            "## Aggregate",
            "",
            f"- Instruments evaluated: **{len(valid)}** "
            f"(skipped {len(results) - len(valid)})",
            f"- Mean modal-agreement across instruments: "
            f"**{agreement_mean*100:.1f}%**",
            f"- Unstable instruments (<{int(UNSTABLE_THRESHOLD*100)}% modal "
            f"agreement): **{n_unstable}** of {len(valid)}",
            f"- Mean confidence (across instruments' means): "
            f"**{statistics.mean(conf_means):.2f}**"
            if conf_means else "",
        ]

    lines += [
        "",
        "## Cost",
        "",
        f"- Spend before run: ${cost_info.get('spend_before', 0.0):.4f}",
        f"- Spend after  run: ${cost_info.get('spend_after', 0.0):.4f}",
        f"- Cost of this run: ${cost_info.get('cost_total', 0.0):.4f}",
        "",
    ]
    return "\n".join(lines)


def run(instruments: list[str], n_calls: int,
        prod_db: Path) -> tuple[dict, dict, str | None]:
    """Orchestrate the live API calls. Returns (results, cost_info, error).
    On error returns ({}, {}, error_string)."""
    today = date.today().isoformat()
    cost_tracker = CostTracker(str(prod_db))
    if cost_tracker.is_budget_exceeded(today):
        return {}, {}, "budget exceeded for today"
    spend_before = cost_tracker.get_daily_spend(today)

    features_by_inst = latest_features_per_instrument(prod_db)
    if not features_by_inst:
        return {}, {}, "no cached features in regime_classification_cache"

    results: dict = {}
    with tempfile.TemporaryDirectory() as td:
        tmp_db = str(Path(td) / "eval_cache.db")
        cache = RegimeCache(tmp_db)
        classifier = RegimeClassifier(cache, cost_tracker)
        if not classifier.is_available():
            return {}, {}, "ANTHROPIC_API_KEY not set"

        for sym in instruments:
            if sym not in features_by_inst:
                results[sym] = {"error": "no cached features"}
                continue
            day, features = features_by_inst[sym]
            calls = []
            for _ in range(n_calls):
                clf = classifier.classify(sym, today, features, force=True)
                calls.append({"raw_regime": clf.raw_regime,
                              "confidence": clf.confidence})
            agg = score_calls(calls)
            results[sym] = {
                "features_from_date": day,
                "calls": calls,
                **agg,
            }

    spend_after = cost_tracker.get_daily_spend(today)
    return (results,
            {"spend_before": spend_before,
             "spend_after": spend_after,
             "cost_total": spend_after - spend_before},
            None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None,
                        help="Output markdown path (default: "
                             "docs/eval_consistency_<YYYY-MM-DD>.md)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't call the API; print plan and exit.")
    args = parser.parse_args()

    instruments = load_enabled_instruments()
    print(f"Consistency probe over {len(instruments)} instruments × "
          f"{N_CALLS} calls each.")

    if args.dry_run:
        print("dry-run: would call API "
              f"{len(instruments)*N_CALLS} times.")
        return 0

    results, cost_info, err = run(instruments, N_CALLS, REGIME_DB)
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1

    generated_at = datetime.now(timezone.utc).isoformat()
    md = render_report(results, cost_info, generated_at)

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = args.out or (
        DOCS_DIR / f"eval_consistency_{date.today().isoformat()}.md"
    )
    out_path.write_text(md)
    print(f"Wrote {out_path}")
    n_valid = sum(1 for r in results.values() if "error" not in r)
    n_unstable = sum(1 for r in results.values()
                     if r.get("unstable", False))
    print(f"  {n_valid} instruments evaluated, {n_unstable} unstable")
    print(f"  cost: ${cost_info.get('cost_total', 0.0):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
