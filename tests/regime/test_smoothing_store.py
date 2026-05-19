"""Unit tests for SmoothedStateStore."""
from datetime import datetime, timezone

import pytest

from bot.regime.models import SmoothedRegimeState
from bot.regime.smoothing_store import SmoothedStateStore


@pytest.fixture
def store(tmp_path):
    return SmoothedStateStore(str(tmp_path / "regime.db"))


def _state(instrument="AAPL", regime="TRENDING", days=3,
           pending=None, history=None):
    return SmoothedRegimeState(
        instrument=instrument,
        effective_regime=regime,
        source_regime=regime,
        days_in_regime=days,
        last_changed_at=datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
        confidence=0.9,
        pending_regime=pending,
        pending_days=0,
        regime_history=history or ["UNCLEAR", "RANGING"],
    )


class TestGetLatest:
    def test_returns_none_when_empty(self, store):
        assert store.get_latest("AAPL") is None

    def test_round_trip(self, store):
        original = _state()
        store.put(original)
        loaded = store.get_latest("AAPL")
        assert loaded is not None
        assert loaded.effective_regime == "TRENDING"
        assert loaded.days_in_regime == 3
        assert loaded.regime_history == ["UNCLEAR", "RANGING"]

    def test_per_instrument_isolation(self, store):
        store.put(_state(instrument="AAPL", regime="TRENDING"))
        store.put(_state(instrument="MSFT", regime="RANGING"))
        assert store.get_latest("AAPL").effective_regime == "TRENDING"
        assert store.get_latest("MSFT").effective_regime == "RANGING"


class TestPutOverwrites:
    def test_put_replaces_existing_row(self, store):
        store.put(_state(regime="TRENDING", days=3))
        store.put(_state(regime="RANGING", days=1))
        loaded = store.get_latest("AAPL")
        assert loaded.effective_regime == "RANGING"
        assert loaded.days_in_regime == 1

    def test_put_preserves_pending(self, store):
        state = _state(pending="RANGING")
        # pending_days = 0 in default; bump for clarity
        store.put(SmoothedRegimeState(
            instrument=state.instrument,
            effective_regime=state.effective_regime,
            source_regime=state.source_regime,
            days_in_regime=state.days_in_regime,
            last_changed_at=state.last_changed_at,
            confidence=state.confidence,
            pending_regime="RANGING",
            pending_days=1,
            regime_history=state.regime_history,
        ))
        loaded = store.get_latest("AAPL")
        assert loaded.pending_regime == "RANGING"
        assert loaded.pending_days == 1
