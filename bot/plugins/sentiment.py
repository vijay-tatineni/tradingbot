"""
bot/plugins/sentiment.py
Sentiment Engine Plugin — skeleton ready for Phase 3.

When activated, calls Claude API before each trade to check
news sentiment. Can block trades if sentiment is strongly negative.

Phase 3 will implement:
  - Web search for latest headlines per instrument
  - Claude API sentiment scoring (-1.0 to +1.0)
  - pre_trade() hook blocks if score < threshold
  - Sentiment score added to dashboard and learning loop

USAGE in main.py (uncomment when ready):
  from bot.plugins.sentiment import SentimentEngine
  bot.register_plugin(SentimentEngine(cfg))
"""

from bot.plugins.base_plugin import BasePlugin
from bot.logger import log


class SentimentEngine(BasePlugin):

    name = "SentimentEngine"

    def __init__(self, cfg):
        self.cfg       = cfg
        self.threshold = -0.5    # block trade if score below this
        self.cache     = {}      # symbol → (score, timestamp)
        self.cache_ttl = 3600    # re-check after 1 hour

    def on_start(self) -> None:
        log("[Sentiment] Started — Phase 3 skeleton (not yet active)")

    def pre_trade(self, inst: dict, signal: int, confidence: str) -> bool:
        """
        Phase 3: Check sentiment before allowing a BUY.
        Currently always returns True (allow all trades).

        When implemented:
          1. Search web for latest headlines for inst['symbol']
          2. Send to Claude API for sentiment scoring
          3. Return False if score < self.threshold
        """
        # TODO Phase 3: implement Claude API sentiment check
        return True   # currently always allow

    def _get_sentiment(self, symbol: str) -> float:
        """
        TODO Phase 3: Call Claude API with web search.

        Will look like:
          headlines = web_search(f"{symbol} stock news today")
          prompt    = f"Score sentiment -1.0 to +1.0. JSON only. Headlines: {headlines}"
          response  = claude_api(prompt)
          return float(response['score'])
        """
        return 0.0   # neutral placeholder
