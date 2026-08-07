"""Unit tests for regime smoothing — §9.3."""
from datetime import datetime, timezone

from bot.regime.models import RegimeClassification, SmoothedRegimeState
from bot.regime.smoothing import (
    initial_state, update, update_first_run,
    SAME_REGIME_PERSISTENCE_THRESHOLD,
    NEW_REGIME_PENDING_THRESHOLD,
    TRENDING_TO_RANGING_HYSTERESIS,
    PENDING_DAYS_TO_PROMOTE,
    MAX_HISTORY_LENGTH,
)


def _make_classification(regime="TRENDING", confidence=0.9, instrument="BARC.L"):
    return RegimeClassification(
        instrument=instrument,
        classified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        trading_date="2026-01-01",
        raw_regime=regime,
        confidence=confidence,
        rationale="Test",
        features={},
        model_version="test",
        prompt_version="V1",
        input_hash="test",
    )


class TestInitialState:
    def test_defaults(self):
        s = initial_state("BARC.L")
        assert s.instrument == "BARC.L"
        assert s.effective_regime == "UNCLEAR"
        assert s.days_in_regime == 0
        assert s.pending_regime is None


class TestFirstRun:
    def test_first_high_confidence_promotes(self):
        s = initial_state("BARC.L")
        c = _make_classification("TRENDING", 0.9)
        result = update_first_run(s, c)
        assert result.effective_regime == "TRENDING"
        assert result.days_in_regime == 1

    def test_first_low_confidence_retains_unclear(self):
        s = initial_state("BARC.L")
        c = _make_classification("TRENDING", 0.5)
        result = update_first_run(s, c)
        assert result.effective_regime == "UNCLEAR"


class TestPersistence:
    def test_same_regime_high_confidence_increments(self):
        s = SmoothedRegimeState(
            instrument="BARC.L", effective_regime="TRENDING",
            source_regime="TRENDING", days_in_regime=3,
            last_changed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            confidence=0.9, pending_regime=None, pending_days=0,
        )
        c = _make_classification("TRENDING", 0.9)
        result = update(s, c)
        assert result.effective_regime == "TRENDING"
        assert result.days_in_regime == 4
        assert result.pending_regime is None


class TestPendingPromotion:
    def test_new_regime_starts_pending(self):
        s = SmoothedRegimeState(
            instrument="BARC.L", effective_regime="TRENDING",
            source_regime="TRENDING", days_in_regime=5,
            last_changed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            confidence=0.9, pending_regime=None, pending_days=0,
        )
        c = _make_classification("UNCLEAR", 0.75)
        result = update(s, c)
        assert result.effective_regime == "TRENDING"
        assert result.pending_regime == "UNCLEAR"
        assert result.pending_days == 1

    def test_pending_promotes_after_threshold(self):
        s = SmoothedRegimeState(
            instrument="BARC.L", effective_regime="TRENDING",
            source_regime="TRENDING", days_in_regime=5,
            last_changed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            confidence=0.9, pending_regime="UNCLEAR", pending_days=1,
        )
        c = _make_classification("UNCLEAR", 0.75)
        result = update(s, c)
        assert result.effective_regime == "UNCLEAR"
        assert result.days_in_regime == 1
        assert result.pending_regime is None

    def test_different_pending_resets(self):
        s = SmoothedRegimeState(
            instrument="BARC.L", effective_regime="TRENDING",
            source_regime="TRENDING", days_in_regime=5,
            last_changed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            confidence=0.9, pending_regime="UNCLEAR", pending_days=1,
        )
        c = _make_classification("RANGING", 0.80)
        result = update(s, c)
        assert result.pending_regime == "RANGING"
        assert result.pending_days == 1


class TestHysteresis:
    def test_trending_to_ranging_requires_higher_confidence(self):
        s = SmoothedRegimeState(
            instrument="BARC.L", effective_regime="TRENDING",
            source_regime="TRENDING", days_in_regime=5,
            last_changed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            confidence=0.9, pending_regime=None, pending_days=0,
        )
        # 0.72 >= NEW_REGIME_PENDING (0.70) but < HYSTERESIS (0.75)
        c = _make_classification("RANGING", 0.72)
        result = update(s, c)
        assert result.effective_regime == "TRENDING"
        assert result.pending_regime is None

    def test_trending_to_ranging_works_above_hysteresis(self):
        s = SmoothedRegimeState(
            instrument="BARC.L", effective_regime="TRENDING",
            source_regime="TRENDING", days_in_regime=5,
            last_changed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            confidence=0.9, pending_regime=None, pending_days=0,
        )
        c = _make_classification("RANGING", 0.80)
        result = update(s, c)
        assert result.pending_regime == "RANGING"
        assert result.pending_days == 1


class TestFallback:
    def test_zero_confidence_retains_state(self):
        s = SmoothedRegimeState(
            instrument="BARC.L", effective_regime="TRENDING",
            source_regime="TRENDING", days_in_regime=5,
            last_changed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            confidence=0.9, pending_regime=None, pending_days=0,
        )
        c = _make_classification("RANGING", 0.0)
        result = update(s, c)
        assert result is s  # Same object, retained entirely

    def test_low_confidence_retains_state(self):
        s = SmoothedRegimeState(
            instrument="BARC.L", effective_regime="TRENDING",
            source_regime="TRENDING", days_in_regime=5,
            last_changed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            confidence=0.9, pending_regime=None, pending_days=0,
        )
        c = _make_classification("RANGING", 0.5)
        result = update(s, c)
        assert result is s


class TestHistory:
    def test_history_appended_on_promotion(self):
        s = SmoothedRegimeState(
            instrument="BARC.L", effective_regime="TRENDING",
            source_regime="TRENDING", days_in_regime=5,
            last_changed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            confidence=0.9, pending_regime="UNCLEAR",
            pending_days=1, regime_history=["UNCLEAR"],
        )
        c = _make_classification("UNCLEAR", 0.80)
        result = update(s, c)
        assert "TRENDING" in result.regime_history

    def test_history_bounded(self):
        history = [f"REGIME_{i}" for i in range(MAX_HISTORY_LENGTH)]
        s = SmoothedRegimeState(
            instrument="BARC.L", effective_regime="TRENDING",
            source_regime="TRENDING", days_in_regime=5,
            last_changed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            confidence=0.9, pending_regime="UNCLEAR",
            pending_days=1, regime_history=history,
        )
        c = _make_classification("UNCLEAR", 0.80)
        result = update(s, c)
        assert len(result.regime_history) <= MAX_HISTORY_LENGTH
