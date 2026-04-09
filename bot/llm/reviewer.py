"""
Post-trade review using LLM.

After each trade closes, this module asks the LLM to analyze:
- Why did this trade win/lose?
- What patterns were present at entry?
- What could be improved?

Results are stored in a 'trade_reviews' table in learning_loop.db.
"""
import re
import logging

logger = logging.getLogger("llm.reviewer")

SYSTEM_PROMPT = """You are a trading performance analyst. You review completed trades
and extract lessons.

You will receive:
- Trade details (entry/exit price, P&L, hold time, exit reason)
- Price bars before entry and before exit
- The indicator values at entry

Analyze concisely. Focus on:
1. Was the entry well-timed? (price action, volume, momentum)
2. Was the exit optimal? (left money on the table, or got out at the right time)
3. What pattern was present at entry? (breakout, pullback, trend continuation)
4. One specific lesson for future trades

Respond in EXACTLY this format:
ANALYSIS: 2-3 sentences about why this trade won or lost
ENTRY_QUALITY: GOOD | FAIR | POOR
EXIT_QUALITY: GOOD | FAIR | POOR
PATTERN: Brief description of the price pattern at entry
LESSON: One actionable takeaway"""


def review_trade(llm, trade_data: dict, bars_at_entry: list,
                 bars_at_exit: list) -> dict:
    """
    Review a completed trade.

    Args:
        llm: BaseLLM instance
        trade_data: {symbol, entry_price, exit_price, pnl, hold_days,
                     outcome, exit_reason, indicators_at_entry}
        bars_at_entry: Last 10 bars before entry
        bars_at_exit: Last 10 bars before exit

    Returns:
        {
            "analysis": "2-3 sentence analysis",
            "lessons": ["Lesson 1"],
            "entry_quality": "GOOD" | "FAIR" | "POOR",
            "exit_quality": "GOOD" | "FAIR" | "POOR",
            "pattern_at_entry": "Description of price pattern",
            "suggestion": "One actionable suggestion",
            "raw_response": "Full LLM response"
        }
    """
    default_result = {
        "analysis": "Review unavailable",
        "lessons": [],
        "entry_quality": "FAIR",
        "exit_quality": "FAIR",
        "pattern_at_entry": "Unknown",
        "suggestion": "",
        "raw_response": "",
    }

    if not llm or not llm.is_available():
        return default_result

    try:
        entry_bars_str = _format_bars(bars_at_entry)
        exit_bars_str = _format_bars(bars_at_exit)

        indicators = trade_data.get("indicators_at_entry", {})
        indicators_str = ", ".join(f"{k}={v}" for k, v in indicators.items()) if indicators else "N/A"

        user_prompt = (
            f"Symbol: {trade_data['symbol']}\n"
            f"Direction: {trade_data.get('action', 'BUY')}\n"
            f"Entry price: {trade_data['entry_price']}\n"
            f"Exit price: {trade_data['exit_price']}\n"
            f"P&L: ${trade_data['pnl']:.2f}\n"
            f"Outcome: {trade_data['outcome']}\n"
            f"Hold days: {trade_data['hold_days']}\n"
            f"Exit reason: {trade_data['exit_reason']}\n"
            f"Indicators at entry: {indicators_str}\n\n"
            f"Bars before entry (O,H,L,C,V):\n{entry_bars_str}\n\n"
            f"Bars before exit (O,H,L,C,V):\n{exit_bars_str}"
        )

        response = llm.chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ], temperature=0.3, max_tokens=400)

        result = _parse_review(response)
        result["raw_response"] = response
        return result

    except Exception as e:
        logger.error(f"Trade review failed for {trade_data.get('symbol', '?')}: {e}")
        return default_result


def _format_bars(bars: list) -> str:
    """Format bar list into compact string."""
    lines = []
    for bar in bars:
        if isinstance(bar, dict):
            dt = str(bar.get("datetime", bar.get("date", "")))[:10]
            o = f"{bar['open']:.2f}"
            h = f"{bar['high']:.2f}"
            l = f"{bar['low']:.2f}"
            c = f"{bar['close']:.2f}"
            v = str(int(bar.get("volume", 0)))
            lines.append(f"{dt} {o} {h} {l} {c} {v}")
    return "\n".join(lines) if lines else "No bar data available"


def _parse_review(response: str) -> dict:
    """Parse the structured review response from the LLM."""
    result = {
        "analysis": "Could not parse review",
        "lessons": [],
        "entry_quality": "FAIR",
        "exit_quality": "FAIR",
        "pattern_at_entry": "Unknown",
        "suggestion": "",
    }

    if not response:
        return result

    text = response.upper()

    # Parse analysis
    analysis_match = re.search(r'ANALYSIS:\s*(.+?)(?=\nENTRY_QUALITY|\n[A-Z_]+:|\Z)',
                               response, re.DOTALL | re.IGNORECASE)
    if analysis_match:
        result["analysis"] = analysis_match.group(1).strip()

    # Parse entry quality
    for quality in ("GOOD", "FAIR", "POOR"):
        if f"ENTRY_QUALITY" in text and quality in text.split("ENTRY_QUALITY")[1][:20]:
            result["entry_quality"] = quality
            break

    # Parse exit quality
    for quality in ("GOOD", "FAIR", "POOR"):
        if f"EXIT_QUALITY" in text and quality in text.split("EXIT_QUALITY")[1][:20]:
            result["exit_quality"] = quality
            break

    # Parse pattern
    pattern_match = re.search(r'PATTERN:\s*(.+?)(?=\n[A-Z_]+:|\Z)',
                              response, re.IGNORECASE)
    if pattern_match:
        result["pattern_at_entry"] = pattern_match.group(1).strip()

    # Parse lesson
    lesson_match = re.search(r'LESSON:\s*(.+?)(?=\n[A-Z_]+:|\Z)',
                             response, re.IGNORECASE)
    if lesson_match:
        lesson = lesson_match.group(1).strip()
        result["lessons"] = [lesson]
        result["suggestion"] = lesson

    return result
