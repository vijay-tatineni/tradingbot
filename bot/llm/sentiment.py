"""
Price action analysis for pre-entry filtering.

When a triple confirmation signal fires, this module asks the LLM:
"Based on the recent price action, should we proceed with this trade?"

Uses OHLCV data that the bot already fetches for indicator calculations.
No external news API needed.
"""
import re
import logging

logger = logging.getLogger("llm.sentiment")

SYSTEM_PROMPT = """You are a trading analyst. You will be given recent price data for a stock
and a proposed trade direction. Analyze the price action and determine if
the trade should proceed.

Consider:
- Is the price trending in the proposed direction?
- Are there signs of reversal (volume spikes, long wicks, gaps)?
- Is the price near a key level (recent high/low, round number)?
- Any unusual volume patterns?

Respond in EXACTLY this format:
VERDICT: CONFIRM | CAUTION | REJECT
CONFIDENCE: 0.0 to 1.0
REASON: One sentence explaining your assessment"""


def analyze_sentiment(llm, symbol: str, bars_df, signal_direction: str,
                      news_sentiment: float = 0.0) -> dict:
    """
    Analyze price action before entering a trade.

    Args:
        llm: BaseLLM instance
        symbol: e.g. "BARC"
        bars_df: DataFrame with last 20 bars (datetime, open, high, low, close, volume)
        signal_direction: "BUY" or "SELL"
        news_sentiment: aggregate news sentiment score (-1.0 to +1.0)

    Returns:
        {
            "verdict": "CONFIRM" | "CAUTION" | "REJECT",
            "confidence": 0.0-1.0,
            "reason": "Brief explanation",
            "raw_response": "Full LLM response"
        }

    On ANY error, returns {"verdict": "CONFIRM", "confidence": 0.0, "reason": "LLM unavailable"}
    The LLM should NEVER block a trade due to errors.
    """
    default_result = {
        "verdict": "CONFIRM",
        "confidence": 0.0,
        "reason": "LLM unavailable",
        "raw_response": "",
    }

    if not llm or not llm.is_available():
        return default_result

    try:
        # Format bars for the prompt
        formatted_bars = _format_bars(bars_df)

        user_prompt = (
            f"Symbol: {symbol}\n"
            f"Proposed trade: {signal_direction}\n"
            f"Last 20 bars (date, open, high, low, close, volume):\n"
            f"{formatted_bars}\n\n"
            f"Should this trade proceed?"
        )

        if news_sentiment != 0:
            sentiment_label = "bullish" if news_sentiment > 0 else "bearish"
            user_prompt += (
                f"\nRecent news sentiment: {sentiment_label} "
                f"({news_sentiment:+.1f})"
            )

        response = llm.chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ], temperature=0.3, max_tokens=200)

        result = _parse_response(response)
        result["raw_response"] = response
        return result

    except Exception as e:
        logger.error(f"Sentiment analysis failed for {symbol}: {e}")
        return default_result


def _format_bars(bars_df) -> str:
    """Format a DataFrame of bars into a compact string."""
    lines = []
    for _, row in bars_df.iterrows():
        dt = str(row.get("datetime", ""))[:10]
        o = f"{row['open']:.2f}"
        h = f"{row['high']:.2f}"
        l = f"{row['low']:.2f}"
        c = f"{row['close']:.2f}"
        v = str(int(row.get("volume", 0)))
        lines.append(f"{dt} {o} {h} {l} {c} {v}")
    return "\n".join(lines)


def _parse_response(response: str) -> dict:
    """
    Parse LLM response. Try structured format first, then fuzzy matching.
    Default to CONFIRM on any parsing failure.
    """
    result = {"verdict": "CONFIRM", "confidence": 0.5, "reason": "Could not parse"}

    if not response:
        return result

    text = response.upper()

    # Try to find verdict
    if "REJECT" in text:
        result["verdict"] = "REJECT"
    elif "CAUTION" in text:
        result["verdict"] = "CAUTION"
    elif "CONFIRM" in text:
        result["verdict"] = "CONFIRM"

    # Try to find confidence
    conf_match = re.search(r'CONFIDENCE[:\s]+\w*\s*([0-9]+\.?[0-9]*)', text)
    if conf_match:
        try:
            result["confidence"] = float(conf_match.group(1))
        except ValueError:
            pass

    # Try to find reason
    for line in response.split("\n"):
        if line.strip().upper().startswith("REASON"):
            result["reason"] = line.split(":", 1)[-1].strip()
            break

    return result
