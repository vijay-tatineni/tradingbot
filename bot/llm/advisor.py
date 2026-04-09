"""
Weekly strategy advisor using LLM.

Analyzes:
- All trades from the past week
- Win/loss patterns per instrument
- WF results and current settings
- Trade review lessons accumulated

Produces recommendations for parameter changes, instruments to
enable/disable, and general strategy improvements.
"""
import json
import re
import logging

logger = logging.getLogger("llm.advisor")

SYSTEM_PROMPT = """You are a quantitative trading strategy advisor. You review a week of
trading performance and provide actionable recommendations.

You will receive:
- All trades from the past week (entry, exit, P&L, hold time, outcome)
- LLM trade reviews with lessons learned
- Walk-forward test results (which instruments are robust/no-edge)
- Current instrument settings (indicators, stop%, TP%)

Provide:
1. Performance summary (total P&L, win rate, best/worst instruments)
2. Per-instrument recommendations (keep/disable/adjust params)
3. Pattern observations (common winning/losing patterns)
4. Risk warnings (concentrated exposure, overtrading, etc.)
5. Next week outlook (based on observed momentum/trends)

Be specific and actionable. Reference actual numbers.

Respond in this JSON format:
{
    "summary": "Overall performance summary",
    "instrument_recommendations": [
        {"symbol": "BARC", "action": "keep|disable|adjust", "reason": "..."}
    ],
    "parameter_suggestions": [
        {"symbol": "BARC", "param": "adx_threshold", "current": 20, "suggested": 15, "reason": "..."}
    ],
    "patterns_observed": ["Pattern 1", "Pattern 2"],
    "risk_warnings": ["Warning 1"],
    "next_week_outlook": "Brief market outlook"
}"""


def generate_weekly_report(llm, trades: list, reviews: list,
                           wf_results: list, instruments: list) -> dict:
    """
    Generate a weekly strategy review.

    Returns:
        {
            "summary": "Overall performance summary",
            "instrument_recommendations": [...],
            "parameter_suggestions": [...],
            "patterns_observed": [...],
            "risk_warnings": [...],
            "next_week_outlook": "..."
        }
    """
    default_report = {
        "summary": "No report available",
        "instrument_recommendations": [],
        "parameter_suggestions": [],
        "patterns_observed": [],
        "risk_warnings": [],
        "next_week_outlook": "",
    }

    if not llm or not llm.is_available():
        default_report["summary"] = "LLM unavailable — cannot generate report"
        return default_report

    try:
        # Format trades
        if trades:
            trades_text = "\n".join(
                f"  {t.get('symbol','?')}: {t.get('action','?')} "
                f"entry={t.get('entry_price',0):.2f} exit={t.get('exit_price',0):.2f} "
                f"P&L=${t.get('pnl_usd',0):.2f} hold={t.get('hold_days',0)}d "
                f"outcome={t.get('outcome','?')} reason={t.get('exit_reason','?')}"
                for t in trades
            )
        else:
            trades_text = "  No trades this week"

        # Format reviews
        if reviews:
            reviews_text = "\n".join(
                f"  {r.get('symbol','?')}: {r.get('analysis','N/A')} "
                f"(entry: {r.get('entry_quality','?')}, exit: {r.get('exit_quality','?')})"
                for r in reviews
            )
        else:
            reviews_text = "  No reviews available"

        # Format WF results
        if wf_results:
            wf_text = "\n".join(
                f"  {w.get('symbol','?')}: OOS P&L=${w.get('oos_pnl',0):.0f} "
                f"WR={w.get('oos_win_rate',0):.0%} "
                f"verdict={w.get('verdict','?')}"
                for w in wf_results
            )
        else:
            wf_text = "  No WF results available"

        # Format instruments
        if instruments:
            inst_text = "\n".join(
                f"  {i.get('symbol','?')}: stop={i.get('trail_stop_pct',2.0)}% "
                f"TP={i.get('take_profit_pct',8.0)}% "
                f"enabled={i.get('enabled',True)}"
                for i in instruments
            )
        else:
            inst_text = "  No instruments configured"

        user_prompt = (
            f"Trades this week:\n{trades_text}\n\n"
            f"Trade reviews:\n{reviews_text}\n\n"
            f"Walk-forward results:\n{wf_text}\n\n"
            f"Current instruments:\n{inst_text}"
        )

        response = llm.chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ], temperature=0.4, max_tokens=1000)

        return _parse_report(response, default_report)

    except Exception as e:
        logger.error(f"Weekly report generation failed: {e}")
        default_report["summary"] = f"Report generation failed: {e}"
        return default_report


def _parse_report(response: str, default: dict) -> dict:
    """Parse the JSON report from the LLM response."""
    if not response:
        return default

    # Try to extract JSON from the response
    try:
        # Handle markdown-wrapped JSON
        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        report = json.loads(text)

        # Validate expected keys
        result = {
            "summary": report.get("summary", default["summary"]),
            "instrument_recommendations": report.get("instrument_recommendations", []),
            "parameter_suggestions": report.get("parameter_suggestions", []),
            "patterns_observed": report.get("patterns_observed", []),
            "risk_warnings": report.get("risk_warnings", []),
            "next_week_outlook": report.get("next_week_outlook", ""),
        }
        result["raw_response"] = response
        return result

    except (json.JSONDecodeError, KeyError):
        # Fallback: use the raw response as the summary
        logger.warning("Could not parse advisor JSON response, using raw text")
        result = dict(default)
        result["summary"] = response[:500]
        result["raw_response"] = response
        return result
