"""Unit tests for cost tracker — §9.5."""
import pytest

from bot.regime.cost_tracker import CostTracker, MODEL_ID, PRICING_USD_PER_TOKEN


@pytest.fixture
def tracker(tmp_path):
    return CostTracker(str(tmp_path / "test.db"))


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
        assert tracker.get_daily_spend("2026-01-01") == 0.0

    def test_budget_not_exceeded_initially(self, tracker):
        assert tracker.is_budget_exceeded("2026-01-01") is False

    def test_budget_exceeded_after_logging(self, tracker):
        tracker.log_classification(
            instrument="BARC.L", trading_date="2026-01-01",
            model=MODEL_ID, prompt_version="V1",
            input_tokens=0, output_tokens=0,
            cost_usd=5.01,
        )
        assert tracker.is_budget_exceeded("2026-01-01") is True

    def test_cache_hit_not_counted_in_spend(self, tracker):
        tracker.log_classification(
            instrument="BARC.L", trading_date="2026-01-01",
            model=MODEL_ID, prompt_version="V1",
            cost_usd=5.01, cache_hit=True,
        )
        assert tracker.is_budget_exceeded("2026-01-01") is False

    def test_different_dates_independent(self, tracker):
        tracker.log_classification(
            instrument="BARC.L", trading_date="2026-01-01",
            model=MODEL_ID, prompt_version="V1",
            cost_usd=5.01,
        )
        assert tracker.is_budget_exceeded("2026-01-02") is False
