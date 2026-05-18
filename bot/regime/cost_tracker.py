"""
Token and cost tracking for regime classifier — §9.5 of CLAUDE_STRATEGY_SPEC_v3.

Pricing source: https://docs.anthropic.com/en/docs/about-claude/models
  (redirects to https://platform.claude.com/docs/en/docs/about-claude/models)
Checked: 2026-05-18
Model: claude-sonnet-4-6
  Input:  $3.00 per million tokens
  Output: $15.00 per million tokens

Note: spec originally referenced claude-sonnet-4-20250514 which is deprecated
(retiring 2026-06-15). Updated to claude-sonnet-4-6 per §5 rule 10 and
CLAUDE.md pricing verification rule.
"""
import sqlite3
import logging
from datetime import datetime, timezone

logger = logging.getLogger("regime.cost_tracker")

MODEL_ID = "claude-sonnet-4-6"

PRICING_USD_PER_TOKEN = {
    MODEL_ID: {
        "input": 3.00 / 1_000_000,
        "output": 15.00 / 1_000_000,
    },
}

DEFAULT_MAX_DAILY_COST_USD = 5.0

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS regime_classification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    instrument TEXT NOT NULL,
    trading_date TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    latency_ms INTEGER,
    raw_regime TEXT,
    confidence REAL,
    cache_hit INTEGER DEFAULT 0,
    error TEXT
)
"""


class CostTracker:
    def __init__(self, db_path: str, max_daily_cost_usd: float = DEFAULT_MAX_DAILY_COST_USD):
        self._db_path = db_path
        self._max_daily_cost = max_daily_cost_usd
        self._ensure_table()

    def _ensure_table(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(CREATE_TABLE)

    def compute_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        pricing = PRICING_USD_PER_TOKEN.get(model)
        if pricing is None:
            logger.warning(f"No pricing for model {model}, assuming zero cost")
            return 0.0
        return input_tokens * pricing["input"] + output_tokens * pricing["output"]

    def get_daily_spend(self, trading_date: str) -> float:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM regime_classification_log "
                "WHERE trading_date = ? AND cache_hit = 0",
                (trading_date,),
            ).fetchone()
        return row[0]

    def is_budget_exceeded(self, trading_date: str) -> bool:
        return self.get_daily_spend(trading_date) >= self._max_daily_cost

    def log_classification(
        self,
        instrument: str,
        trading_date: str,
        model: str,
        prompt_version: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: int = 0,
        raw_regime: str = None,
        confidence: float = None,
        cache_hit: bool = False,
        error: str = None,
    ) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO regime_classification_log "
                "(ts, instrument, trading_date, model, prompt_version, "
                "input_tokens, output_tokens, cost_usd, latency_ms, "
                "raw_regime, confidence, cache_hit, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    instrument,
                    trading_date,
                    model,
                    prompt_version,
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    latency_ms,
                    raw_regime,
                    confidence,
                    1 if cache_hit else 0,
                    error,
                ),
            )
