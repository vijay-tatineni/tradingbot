"""
bot/plugins/sentiment.py — Sentiment Gate Plugin

Before every BUY trade:
  1. Search DuckDuckGo for latest news headlines about the stock
  2. Send headlines to Claude API (claude-haiku-4-5) for sentiment scoring
  3. Score from -1.0 to +1.0
  4. Score > 0.2  → allow trade
  5. Score < -0.2 → BLOCK trade, log reason, send Telegram alert
  6. Results cached for 1 hour per symbol

Requires:
  ANTHROPIC_API_KEY env var
  anthropic pip package

USAGE in main.py:
  from bot.plugins.sentiment import SentimentEngine
  bot.register_plugin(SentimentEngine(cfg, alerts=alerts))
"""

import os
import json
import time
import urllib.request
import urllib.error
import urllib.parse
from bot.plugins.base_plugin import BasePlugin
from bot.logger import log


class SentimentEngine(BasePlugin):

    name = "SentimentEngine"

    # Thresholds
    ALLOW_ABOVE = 0.2
    BLOCK_BELOW = -0.2

    def __init__(self, cfg, alerts=None):
        self.cfg       = cfg
        self.alerts    = alerts   # TelegramAlerts instance (optional)
        self.api_key   = os.environ.get('ANTHROPIC_API_KEY', '')
        self.enabled   = bool(self.api_key)
        self.cache     = {}       # symbol → (score, headlines_summary, timestamp)
        self.cache_ttl = 3600     # 1 hour

    def on_start(self) -> None:
        if not self.enabled:
            log("[Sentiment] DISABLED — set ANTHROPIC_API_KEY env var")
            return
        log("[Sentiment] Active — Claude Haiku sentiment gate enabled")
        log(f"[Sentiment] Allow > {self.ALLOW_ABOVE}, "
            f"Block < {self.BLOCK_BELOW}, Cache TTL: {self.cache_ttl}s")

    def pre_trade(self, inst: dict, signal: int, confidence: str) -> bool:
        """
        Gate BUY trades on news sentiment.
        SELL signals pass through unchecked (we always want to exit).
        """
        if not self.enabled:
            return True

        # Only gate BUY entries, not sells/closes
        if signal != 1:
            return True

        symbol = inst['symbol']
        name   = inst.get('name', symbol)

        score, summary = self._get_sentiment(symbol, name)

        if score is None:
            # API error — fail open (allow the trade)
            log(f"[Sentiment] {symbol}: API error, allowing trade (fail-open)")
            return True

        if score < self.BLOCK_BELOW:
            reason = (f"[Sentiment] BLOCKED {symbol}: "
                      f"score {score:+.2f} < {self.BLOCK_BELOW}\n"
                      f"  {summary}")
            log(reason, "WARN")
            # Telegram alert for blocked trade
            if self.alerts:
                self.alerts.send(
                    f"🚫 <b>Sentiment BLOCKED</b> {inst.get('flag','')} "
                    f"{symbol}\nScore: {score:+.2f}\n{summary}"
                )
            return False

        log(f"[Sentiment] {symbol}: score {score:+.2f} — "
            f"{'PASS' if score > self.ALLOW_ABOVE else 'NEUTRAL (allow)'}")
        return True

    def _get_sentiment(self, symbol: str,
                       name: str) -> tuple[float | None, str]:
        """
        Get sentiment score for a symbol.
        Returns (score, summary) or (None, '') on error.
        Uses cache if fresh enough.
        """
        # Check cache
        if symbol in self.cache:
            score, summary, ts = self.cache[symbol]
            if time.time() - ts < self.cache_ttl:
                log(f"[Sentiment] {symbol}: cached score {score:+.2f}")
                return score, summary

        # Fetch headlines
        headlines = self._search_news(symbol, name)
        if not headlines:
            log(f"[Sentiment] {symbol}: no headlines found", "WARN")
            return None, ''

        # Score with Claude
        score, summary = self._call_claude(symbol, name, headlines)
        if score is not None:
            self.cache[symbol] = (score, summary, time.time())

        return score, summary

    def _search_news(self, symbol: str, name: str) -> list[str]:
        """
        Search DuckDuckGo Lite for recent news headlines.
        Returns list of headline strings (up to 10).
        """
        query = f"{name} {symbol} stock news"
        url   = (f"https://lite.duckduckgo.com/lite?"
                 f"q={urllib.parse.quote(query)}")

        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': ('Mozilla/5.0 (X11; Linux x86_64) '
                               'AppleWebKit/537.36 (KHTML, like Gecko) '
                               'Chrome/120.0.0.0 Safari/537.36')
            })
            resp = urllib.request.urlopen(req, timeout=10)
            html = resp.read().decode('utf-8', errors='replace')
            return self._parse_ddg_lite(html)
        except Exception as e:
            log(f"[Sentiment] Search error for {symbol}: {e}", "WARN")
            return []

    @staticmethod
    def _parse_ddg_lite(html: str) -> list[str]:
        """Extract result titles from DuckDuckGo Lite HTML."""
        import re
        headlines = []

        # DDG Lite uses <a rel="nofollow"> for result links
        links = re.findall(
            r'<a[^>]*rel="nofollow"[^>]*>([^<]+)</a>', html
        )
        for title in links[:10]:
            title = title.strip()
            if title and len(title) > 10:
                headlines.append(title)

        # Also grab snippets if present
        snippets = re.findall(
            r'class="result-snippet"[^>]*>\s*(.+?)\s*</td>', html,
            re.DOTALL
        )
        for snippet in snippets[:5]:
            snippet = re.sub(r'<[^>]+>', '', snippet).strip()
            if snippet and len(snippet) > 20:
                headlines.append(f"[snippet] {snippet[:200]}")

        return headlines

    def _call_claude(self, symbol: str, name: str,
                     headlines: list[str]) -> tuple[float | None, str]:
        """
        Call Claude Haiku to score sentiment of headlines.
        Returns (score, one_line_summary) or (None, '') on error.
        """
        headlines_text = '\n'.join(f"- {h}" for h in headlines[:15])

        prompt = f"""Analyse the sentiment of these news headlines for {name} ({symbol}).

Headlines:
{headlines_text}

Respond with ONLY valid JSON, no other text:
{{"score": <float from -1.0 to 1.0>, "summary": "<one sentence summary of overall sentiment>"}}

Scoring guide:
  -1.0 = extremely negative (fraud, bankruptcy, crash)
  -0.5 = negative (earnings miss, downgrade, lawsuit)
   0.0 = neutral (routine news, no strong signal)
  +0.5 = positive (earnings beat, upgrade, expansion)
  +1.0 = extremely positive (breakthrough, major deal)"""

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()

            # Parse JSON response
            # Handle potential markdown wrapping
            if text.startswith('```'):
                text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()

            data = json.loads(text)
            score   = max(-1.0, min(1.0, float(data['score'])))
            summary = data.get('summary', '')

            log(f"[Sentiment] {symbol}: Claude scored {score:+.2f} — {summary}")
            return score, summary

        except json.JSONDecodeError as e:
            log(f"[Sentiment] {symbol}: JSON parse error: {e} — raw: {text[:200]}", "WARN")
            return None, ''
        except Exception as e:
            log(f"[Sentiment] {symbol}: Claude API error: {e}", "WARN")
            return None, ''
