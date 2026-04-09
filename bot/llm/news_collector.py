"""
News headline collector using Google News RSS + Finnhub API.

Two sources for comprehensive coverage:
- Google News RSS: Free, general press coverage, no API key needed
- Finnhub: Free tier (60 calls/min), financial/analyst news with summaries

Collects headlines for each instrument every 4 hours.
Headlines are scored by the LLM for sentiment and stored in news.db.
"""

import os
import re
import sqlite3
import logging
import xml.etree.ElementTree as ET
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("news_collector")

BASE_DIR = Path(__file__).parent.parent.parent
NEWS_DB = str(BASE_DIR / "news.db")

# Map bot symbols to Google News search terms
GOOGLE_SEARCH_TERMS = {
    "BARC": "Barclays stock",
    "SHEL": "Shell stock LSE",
    "MSFT": "Microsoft stock",
    "AAPL": "Apple stock",
    "GOOGL": "Alphabet Google stock",
    "PLTR": "Palantir stock",
    "ANTO": "Antofagasta stock",
    "SGLN": "iShares Physical Gold",
    "SSLN": "iShares Physical Silver",
    "CVX": "Chevron stock",
    "MU": "Micron Technology stock",
    "CRWD": "Crowdstrike stock",
    "ANET": "Arista Networks stock",
    "NBIS": "Nebius stock",
    "CEG": "Constellation Energy stock",
    "VRT": "Vertiv stock",
    "SCCO": "Southern Copper stock",
    "FCX": "Freeport McMoRan stock",
    "XAUUSD": "gold price",
    "XAGUSD": "silver price",
}

# Map bot symbols to Finnhub symbols
FINNHUB_SYMBOLS = {
    "BARC": "BARC.L",
    "SHEL": "SHEL.L",
    "ANTO": "ANTO.L",
    "SGLN": "SGLN.L",
    "SSLN": "SSLN.L",
    "SU": "SU.PA",
}


def init_news_db(db_path: str = NEWS_DB) -> None:
    """Create the headlines table if it doesn't exist."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS headlines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            headline TEXT NOT NULL,
            source TEXT,
            published TEXT,
            sentiment_score REAL,
            collected_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_headlines_symbol "
        "ON headlines(symbol, collected_at)"
    )
    conn.commit()
    conn.close()


def collect_news(symbol: str, company_name: str = None) -> list:
    """
    Fetch recent news headlines from both sources.

    Returns combined, deduplicated list:
    [{"headline": "...", "source": "...", "published": "...", "origin": "google|finnhub"}, ...]
    """
    headlines = []

    # Source 1: Google News RSS
    headlines.extend(_collect_google_news(symbol, company_name))

    # Source 2: Finnhub API
    headlines.extend(_collect_finnhub_news(symbol))

    # Deduplicate by headline similarity (exact match on first 50 chars)
    seen = set()
    unique = []
    for h in headlines:
        key = h["headline"][:50].lower()
        if key not in seen:
            seen.add(key)
            unique.append(h)

    return unique[:10]  # Cap at 10 headlines per instrument


def _collect_google_news(symbol: str, company_name: str = None) -> list:
    """Fetch from Google News RSS."""
    query = GOOGLE_SEARCH_TERMS.get(symbol, f"{company_name or symbol} stock")
    headlines = []

    try:
        url = f"https://news.google.com/rss/search?q={query}&hl=en-GB&gl=GB&ceid=GB:en"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        for item in root.findall(".//item")[:5]:
            title = item.findtext("title", "")
            parts = title.rsplit(" - ", 1)
            headline = parts[0] if parts else title
            source = parts[1] if len(parts) > 1 else "Google News"
            published = item.findtext("pubDate", "")

            headlines.append({
                "headline": headline,
                "source": source,
                "published": published,
                "origin": "google",
            })
    except Exception as e:
        logger.warning(f"Google News RSS failed for {symbol}: {e}")

    return headlines


def _collect_finnhub_news(symbol: str) -> list:
    """Fetch from Finnhub company news API."""
    api_key = os.environ.get("FINNHUB_API_KEY", "")
    if not api_key:
        return []

    # Map to Finnhub symbol
    fh_symbol = FINNHUB_SYMBOLS.get(symbol, symbol)

    # Skip commodities — Finnhub doesn't have company news for them
    if symbol in ("XAUUSD", "XAGUSD"):
        return []

    headlines = []
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

        url = (
            f"https://finnhub.io/api/v1/company-news"
            f"?symbol={fh_symbol}&from={week_ago}&to={today}&token={api_key}"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()

        data = resp.json()
        if isinstance(data, list):
            for item in data[:5]:
                headlines.append({
                    "headline": item.get("headline", ""),
                    "source": item.get("source", "Finnhub"),
                    "published": datetime.fromtimestamp(
                        item.get("datetime", 0), tz=timezone.utc
                    ).strftime("%a, %d %b %Y %H:%M:%S GMT"),
                    "summary": item.get("summary", "")[:200],
                    "origin": "finnhub",
                })
    except Exception as e:
        logger.warning(f"Finnhub failed for {symbol} ({fh_symbol}): {e}")

    return headlines


def score_headlines(llm, symbol: str, headlines: list) -> list:
    """
    Ask LLM to score each headline as bullish/neutral/bearish.
    Returns headlines with added sentiment_score field (-1 to +1).
    """
    if not headlines or not llm or not llm.is_available():
        return headlines

    headline_text = "\n".join(
        f"{i+1}. {h['headline']}" for i, h in enumerate(headlines)
    )

    response = llm.chat([
        {"role": "system", "content": "You score financial news sentiment. Reply ONLY with numbers, one per line: +1 (bullish), 0 (neutral), -1 (bearish)."},
        {"role": "user", "content": f"Score each headline for {symbol} stock:\n{headline_text}"}
    ], temperature=0.1, max_tokens=50)

    scores = _parse_scores(response, len(headlines))
    for i, h in enumerate(headlines):
        h["sentiment_score"] = scores[i] if i < len(scores) else 0.0

    return headlines


def save_headlines(symbol: str, headlines: list, db_path: str = NEWS_DB) -> None:
    """Save scored headlines to news.db."""
    try:
        init_news_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        now = datetime.now(timezone.utc).isoformat()
        for h in headlines:
            conn.execute(
                "INSERT INTO headlines (symbol, headline, source, published, "
                "sentiment_score, collected_at) VALUES (?, ?, ?, ?, ?, ?)",
                (symbol, h["headline"], h.get("source", ""),
                 h.get("published", ""), h.get("sentiment_score", 0.0), now)
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to save headlines for {symbol}: {e}")


def get_aggregate_sentiment(symbol: str, db_path: str = NEWS_DB,
                            hours_back: int = 24) -> float:
    """
    Get average sentiment score for a symbol from recent headlines.
    Returns: -1.0 (very bearish) to +1.0 (very bullish), 0.0 if no data.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA busy_timeout = 5000")
        cursor = conn.execute("""
            SELECT AVG(sentiment_score) FROM headlines
            WHERE symbol = ? AND collected_at > datetime('now', ?)
        """, (symbol, f"-{hours_back} hours"))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result and result[0] is not None else 0.0
    except Exception:
        return 0.0


def _parse_scores(response: str, expected_count: int) -> list:
    """Parse score lines from LLM response."""
    scores = []
    for line in response.strip().split("\n"):
        match = re.search(r'[+-]?[01]', line)
        if match:
            scores.append(float(match.group()))
    while len(scores) < expected_count:
        scores.append(0.0)
    return scores[:expected_count]
