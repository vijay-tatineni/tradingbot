"""Unit tests for cost tracker — §9.5.

After the 2026-06-01 fix, get_daily_spend(day) filters by date(ts) — the
wall-clock day the call was logged — rather than by trading_date. The
tests below pass `day = today` whenever a freshly-logged row is expected
to contribute to the daily spend.
"""
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from bot.regime.cost_tracker import CostTracker, MODEL_ID, PRICING_USD_PER_TOKEN


def _today() -> str:
    return date.today().isoformat()


@pytest.fixture
def tracker(tmp_path):
    return CostTracker(str(tmp_path / "test.db"))


def _insert_with_ts(db_path: str, ts: str, cost_usd: float,
                    trading_date: str, cache_hit: int = 0,
                    instrument: str = "BARC"):
    """Direct INSERT so we can fake a row's wall-clock timestamp."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO regime_classification_log "
            "(ts, instrument, trading_date, model, prompt_version, "
            "input_tokens, output_tokens, cost_usd, latency_ms, "
            "raw_regime, confidence, cache_hit, error) "
            "VALUES (?,?,?,?,?,0,0,?,0,NULL,NULL,?,NULL)",
            (ts, instrument, trading_date, MODEL_ID, "V1",
             cost_usd, cache_hit),
        )


class TestCostTracker:
    def test_compute_cost(self, tracker):
        cost = tracker.compute_cost(MODEL_ID, 1000, 500)
        expected = 1000 * PRICING_USD_PER_TOKEN[MODEL_ID]["input"] + \
                   500 * PRICING_USD_PER_TOKEN[MODEL_ID]["output"]
        assert abs(cost - expected) < 1e-10

    def test_unknown_model_returns_zero(self, tracker):
        cost = tracker.compute_cost("unknown-model", 1000, 500)
        assert cost == 0.0

    def test_daily_spend_starts_at_zero(self, tracker):
        assert tracker.get_daily_spend(_today()) == 0.0

    def test_budget_not_exceeded_initially(self, tracker):
        assert tracker.is_budget_exceeded(_today()) is False

    def test_budget_exceeded_after_logging_today(self, tracker):
        # log_classification uses datetime.now() for ts → wall-clock today
        tracker.log_classification(
            instrument="BARC", trading_date=_today(),
            model=MODEL_ID, prompt_version="V1",
            input_tokens=0, output_tokens=0,
            cost_usd=5.01,
        )
        assert tracker.is_budget_exceeded(_today()) is True

    def test_cache_hit_not_counted_in_spend(self, tracker):
        tracker.log_classification(
            instrument="BARC", trading_date=_today(),
            model=MODEL_ID, prompt_version="V1",
            cost_usd=5.01, cache_hit=True,
        )
        assert tracker.is_budget_exceeded(_today()) is False

    def test_yesterdays_calls_dont_count_against_today(self, tracker):
        """A row logged yesterday must not contribute to today's spend."""
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)
                     ).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        _insert_with_ts(tracker._db_path, ts=yesterday, cost_usd=5.01,
                        trading_date=_today())
        # Yesterday's call should not push today over budget.
        assert tracker.is_budget_exceeded(_today()) is False
        # But yesterday itself should see it.
        yesterday_day = (date.today() - timedelta(days=1)).isoformat()
        assert tracker.get_daily_spend(yesterday_day) == pytest.approx(5.01)

    def test_backfill_with_historical_trading_dates_counts_today(self, tracker):
        """**Regression test for the 2026-06-01 backfill bug**:
        rows whose trading_date is historical but whose ts is today must
        count against today's budget. Previously filtered on
        trading_date and missed these; budget enforcement was therefore
        effectively absent for backfill runs."""
        for d in ["2025-12-01", "2025-12-15", "2026-01-10"]:
            tracker.log_classification(
                instrument="BARC", trading_date=d,
                model=MODEL_ID, prompt_version="V1",
                cost_usd=1.50,
            )
        assert tracker.get_daily_spend(_today()) == pytest.approx(4.50)
        # And a non-today wall-clock day still sees zero.
        assert tracker.get_daily_spend("2025-12-01") == 0.0

    def test_pre_fix_behaviour_would_have_returned_zero(self, tracker):
        """If we replicate the pre-fix query (`WHERE trading_date = ?`)
        against the same backfill scenario, it returns 0 — that's the
        bug the fix replaces. This guards against accidental revert."""
        tracker.log_classification(
            instrument="BARC", trading_date="2025-12-01",
            model=MODEL_ID, prompt_version="V1",
            cost_usd=3.00,
        )
        # Pre-fix semantics, run directly:
        with sqlite3.connect(tracker._db_path) as conn:
            pre_fix = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM regime_classification_log "
                "WHERE trading_date = ? AND cache_hit = 0",
                (_today(),),
            ).fetchone()[0]
        assert pre_fix == 0.0, "Pre-fix semantics misses today's spend"
        # Post-fix correctly captures it.
        assert tracker.get_daily_spend(_today()) == pytest.approx(3.00)
