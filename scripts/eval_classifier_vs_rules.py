#!/usr/bin/env python3
"""
Compare Claude's regime calls against a deterministic rule baseline.

For every row in regime_classification_cache, apply this rule:
  ADX_14 > 25 AND range_efficiency > 0.5 → TRENDING
  ADX_14 < 20 AND range_efficiency < 0.3 → RANGING
  otherwise                              → UNCLEAR

…then compare to Claude's raw_regime on the same features.

What the report shows:
- Overall agreement rate (% of rows where rule == Claude)
- 3x3 confusion matrix (rules-rows × Claude-columns), so you can see
  *how* the two disagree, not just *that* they do
- Per-regime accuracy: when rules said X, what fraction of Claude's
  calls agreed
- Full list of disagreements with feature values, for spot-checks

No API calls — pure analysis over existing cache rows.
"""
import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent

REGIME_DB = PROJECT_DIR / "regime.db"
DOCS_DIR = PROJECT_DIR / "docs"

REGIMES = ("TRENDING", "RANGING", "UNCLEAR")


def classify_by_rules(features: dict) -> str:
    """Deterministic regime label from feature dict.

    Returns UNCLEAR for any missing/None key — the rules need both
    adx_14 and range_efficiency to fire either way, so absence means
    we can't make a confident call."""
    if not features:
        return "UNCLEAR"
    adx = features.get("adx_14")
    re_eff = features.get("range_efficiency")
    if adx is None or re_eff is None:
        return "UNCLEAR"
    if adx > 25 and re_eff > 0.5:
        return "TRENDING"
    if adx < 20 and re_eff < 0.3:
        return "RANGING"
    return "UNCLEAR"


def load_classifications(db_path: Path) -> list[dict]:
    """Return [{instrument, trading_date, raw_regime, confidence,
    features}] for every row whose JSON parses cleanly."""
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


def build_comparison(classifications: list[dict]) -> dict:
    """Apply rules to every classification, build the confusion matrix
    and a disagreement list."""
    confusion = {r: {c: 0 for c in REGIMES} for r in REGIMES}
    agree = 0
    disagreements: list[dict] = []
    n_valid = 0

    for row in classifications:
        claude = row.get("raw_regime")
        if claude not in REGIMES:
            continue
        rules = classify_by_rules(row["features"])
        confusion[rules][claude] += 1
        n_valid += 1
        if rules == claude:
            agree += 1
        else:
            disagreements.append({
                "instrument": row["instrument"],
                "trading_date": row["trading_date"],
                "claude_regime": claude,
                "claude_confidence": row.get("confidence"),
                "rules_regime": rules,
                "adx_14": row["features"].get("adx_14"),
                "range_efficiency": row["features"].get("range_efficiency"),
                "ma_200_slope_pct_per_day":
                    row["features"].get("ma_200_slope_pct_per_day"),
                "atr_pct": row["features"].get("atr_pct"),
            })

    return {
        "n_total": len(classifications),
        "n_valid": n_valid,
        "n_agree": agree,
        "agreement_rate": (agree / n_valid) if n_valid else 0.0,
        "confusion": confusion,
        "disagreements": disagreements,
    }


def _fmt(v, digits=2):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def render_report(comparison: dict, generated_at: str) -> str:
    n_total = comparison["n_total"]
    n_valid = comparison["n_valid"]
    n_agree = comparison["n_agree"]
    rate = comparison["agreement_rate"]
    confusion = comparison["confusion"]

    lines = [
        "# Classifier vs deterministic rules",
        "",
        f"_Generated: {generated_at}_",
        "",
        "Rule baseline:",
        "",
        "- `adx_14 > 25 AND range_efficiency > 0.5` → TRENDING",
        "- `adx_14 < 20 AND range_efficiency < 0.3` → RANGING",
        "- otherwise → UNCLEAR",
        "",
        "## Summary",
        "",
        f"- Cache rows scanned: **{n_total}**",
        f"- Rows with a valid Claude regime: **{n_valid}** "
        f"(skipped {n_total - n_valid})",
        f"- Agreement (rules == Claude): "
        f"**{n_agree} / {n_valid} = {rate*100:.1f}%**",
        "",
        "## Confusion matrix",
        "",
        "Rows = the rule's verdict. Columns = Claude's verdict. "
        "Diagonal cells are agreement; off-diagonal cells are disagreement.",
        "",
        "| Rules \\ Claude | TRENDING | RANGING | UNCLEAR | Row total |",
        "|---|---|---|---|---|",
    ]
    for r in REGIMES:
        row_total = sum(confusion[r].values())
        cells = [str(confusion[r][c]) for c in REGIMES]
        lines.append(f"| **{r}** | " + " | ".join(cells)
                     + f" | {row_total} |")
    col_totals = [sum(confusion[r][c] for r in REGIMES) for c in REGIMES]
    lines.append("| **Column total** | " + " | ".join(str(x) for x in col_totals)
                 + f" | {n_valid} |")

    # Per-regime accuracy
    lines += ["", "## Per-rule-label accuracy", ""]
    lines.append("| When rules said... | Claude agreed | Agreement % |")
    lines.append("|---|---|---|")
    for r in REGIMES:
        row_total = sum(confusion[r].values())
        agree_count = confusion[r][r]
        pct = (agree_count / row_total * 100) if row_total else 0.0
        lines.append(f"| {r} | {agree_count} / {row_total} | "
                     f"{pct:.1f}% |")

    # Disagreements
    disagreements = comparison["disagreements"]
    lines += [
        "",
        f"## Disagreements ({len(disagreements)})",
        "",
    ]
    if not disagreements:
        lines.append("_None — rules and Claude agreed on every evaluable row._")
    else:
        lines.append(
            "| Instrument | Date | Claude | Conf | Rules | ADX | "
            "Range eff | MA200 slope %/d | ATR % |"
        )
        lines.append(
            "|---|---|---|---|---|---|---|---|---|"
        )
        for d in disagreements:
            lines.append(
                f"| {d['instrument']} | {d['trading_date']} | "
                f"{d['claude_regime']} | {_fmt(d.get('claude_confidence'))} | "
                f"{d['rules_regime']} | {_fmt(d.get('adx_14'),1)} | "
                f"{_fmt(d.get('range_efficiency'),3)} | "
                f"{_fmt(d.get('ma_200_slope_pct_per_day'),3)} | "
                f"{_fmt(d.get('atr_pct'),2)} |"
            )

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=REGIME_DB,
                        help=f"Path to regime.db (default: {REGIME_DB})")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output markdown path (default: "
                             "docs/eval_vs_rules_<YYYY-MM-DD>.md)")
    args = parser.parse_args()

    classifications = load_classifications(args.db)
    if not classifications:
        print(f"No rows in regime_classification_cache at {args.db}",
              file=sys.stderr)
        return 1

    comparison = build_comparison(classifications)
    generated_at = datetime.now(timezone.utc).isoformat()
    md = render_report(comparison, generated_at)

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = args.out or (
        DOCS_DIR / f"eval_vs_rules_{date.today().isoformat()}.md"
    )
    out_path.write_text(md)
    print(f"Wrote {out_path}")
    print(f"  {comparison['n_valid']} rows compared, "
          f"agreement {comparison['agreement_rate']*100:.1f}%, "
          f"{len(comparison['disagreements'])} disagreements")
    return 0


if __name__ == "__main__":
    sys.exit(main())
