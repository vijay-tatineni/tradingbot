"""
OHLCV pattern analysis for walk-forward testing.

During WF simulation, for each signal the triple confirmation generates,
the pattern analyzer looks at the surrounding bars and decides if the
entry looks good from a price-action perspective.

NOTE: This is expensive in API calls. Use sparingly.
"""
import hashlib
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("llm.pattern_analyzer")

BASE_DIR = Path(__file__).parent.parent.parent
BACKTEST_DB = str(BASE_DIR / "backtest.db")


def analyze_pattern(llm, symbol: str, bars: list,
                    signal_direction: str, use_cache: bool = True) -> str:
    """
    Quick pattern analysis on OHLCV bars.

    Args:
        llm: BaseLLM instance
        symbol: e.g. "BARC"
        bars: Last 10 bars as list of dicts [{date, open, high, low, close, vol}, ...]
        signal_direction: "BUY" or "SELL"
        use_cache: Whether to check/store cache

    Returns: "CONFIRM" | "CAUTION" | "REJECT"

    Uses a concise prompt to minimize tokens and API calls.
    """
    if not llm or not llm.is_available():
        return "CONFIRM"

    try:
        bars_hash = _compute_bars_hash(bars, signal_direction)

        # Check cache
        if use_cache:
            cached = _get_cached_verdict(symbol, bars_hash)
            if cached:
                return cached

        # Build compact bar data
        compact_lines = []
        for b in bars:
            compact_lines.append(
                f"{b.get('open',0):.2f},{b.get('high',0):.2f},"
                f"{b.get('low',0):.2f},{b.get('close',0):.2f},"
                f"{int(b.get('volume',0))}"
            )
        compact_data = "\n".join(compact_lines)

        prompt = (
            f"Last 10 bars for {symbol} (O,H,L,C,V):\n"
            f"{compact_data}\n\n"
            f"Signal: {signal_direction}\n"
            f"Reply ONLY: CONFIRM, CAUTION, or REJECT"
        )

        response = llm.chat([
            {"role": "user", "content": prompt},
        ], temperature=0.1, max_tokens=10)

        verdict = _parse_verdict(response)

        # Cache the result
        if use_cache:
            _cache_verdict(symbol, bars_hash, verdict,
                           str(bars[-1].get("date", bars[-1].get("datetime", "")))[:10],
                           signal_direction)

        return verdict

    except Exception as e:
        logger.error(f"Pattern analysis failed for {symbol}: {e}")
        return "CONFIRM"


def _compute_bars_hash(bars: list, direction: str) -> str:
    """Hash the bar data + direction for cache lookup."""
    data = f"{direction}|"
    for b in bars:
        data += (
            f"{b.get('open',0):.2f},{b.get('high',0):.2f},"
            f"{b.get('low',0):.2f},{b.get('close',0):.2f}|"
        )
    return hashlib.md5(data.encode()).hexdigest()


def _parse_verdict(response: str) -> str:
    """Parse verdict from response."""
    if not response:
        return "CONFIRM"
    text = response.upper()
    if "REJECT" in text:
        return "REJECT"
    if "CAUTION" in text:
        return "CAUTION"
    return "CONFIRM"


def _get_cached_verdict(symbol: str, bars_hash: str) -> str | None:
    """Look up cached verdict in backtest.db."""
    try:
        conn = sqlite3.connect(BACKTEST_DB)
        conn.execute("PRAGMA busy_timeout = 5000")
        cursor = conn.execute(
            "SELECT verdict FROM llm_pattern_cache "
            "WHERE symbol = ? AND bars_hash = ?",
            (symbol, bars_hash)
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _cache_verdict(symbol: str, bars_hash: str, verdict: str,
                   bar_date: str, direction: str) -> None:
    """Store verdict in cache."""
    try:
        conn = sqlite3.connect(BACKTEST_DB)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        _ensure_cache_table(conn)
        conn.execute(
            "INSERT OR REPLACE INTO llm_pattern_cache "
            "(symbol, bar_date, direction, bars_hash, verdict, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, bar_date, direction, bars_hash, verdict,
             datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to cache pattern verdict: {e}")


def _ensure_cache_table(conn: sqlite3.Connection) -> None:
    """Create the cache table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_pattern_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            bar_date TEXT NOT NULL,
            direction TEXT NOT NULL,
            bars_hash TEXT NOT NULL,
            verdict TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)
