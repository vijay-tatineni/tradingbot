"""Unit tests for the regime classification scheduler.

The scheduler is the missing link from §14: classifier exists, smoothing
exists, router exists — nothing called them daily. These tests pin the
contract: post-close gating, once-per-day idempotency, fallback persistence,
flag-off no-op, LSE vs US market-close times.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from bot.regime.cache import RegimeCache
from bot.regime.flags import FeatureFlags
from bot.regime.models import RegimeClassification
from bot.regime.scheduler import RegimeClassificationScheduler
from bot.regime.smoothing_store import SmoothedStateStore


def _bars_df(n: int = 250) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + np.abs(rng.normal(0, 0.5, n))
    low = close - np.abs(rng.normal(0, 0.5, n))
    open_ = close + rng.normal(0, 0.2, n)
    volume = rng.integers(1_000, 10_000, n)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    })


def _make_classification(symbol, trading_date, regime="TRENDING",
                         confidence=0.85, features=None):
    return RegimeClassification(
        instrument=symbol,
        classified_at=datetime(2026, 5, 19, 18, 0, tzinfo=timezone.utc),
        trading_date=trading_date,
        raw_regime=regime,
        confidence=confidence,
        rationale="test",
        features=features or {},
        model_version="claude-sonnet-4-6",
        prompt_version="V1",
        input_hash="testhash",
        cache_hit=False,
    )


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "regime.db")


@pytest.fixture
def cache(tmp_db):
    return RegimeCache(tmp_db)


@pytest.fixture
def store(tmp_db):
    return SmoothedStateStore(tmp_db)


@pytest.fixture
def bars():
    return _bars_df()


def _scheduler(flags, classifier, cache, store, bars_or_fetcher, now):
    if callable(bars_or_fetcher):
        fetcher = bars_or_fetcher
    else:
        fetcher = lambda inst: bars_or_fetcher  # noqa: E731
    return RegimeClassificationScheduler(
        flags=flags,
        classifier=classifier,
        cache=cache,
        smoothing_store=store,
        bars_fetcher=fetcher,
        now_fn=lambda: now,
    )


def _flags(classifier_shadow=True):
    return FeatureFlags({
        "enable_classifier_shadow": classifier_shadow,
    })


class TestFlagGate:
    def test_flag_off_is_noop(self, cache, store, bars):
        classifier = MagicMock()
        sched = _scheduler(
            _flags(classifier_shadow=False), classifier, cache, store, bars,
            now=datetime(2026, 5, 19, 18, 0, tzinfo=timezone.utc),
        )
        summary = sched.maybe_run([{"symbol": "BARC.L", "market": "LSE"}])
        assert summary["classified"] == []
        assert "BARC.L" in summary["skipped_flag_off"]
        classifier.classify.assert_not_called()


class TestPostCloseGate:
    def test_before_lse_close_is_skipped(self, cache, store, bars):
        classifier = MagicMock()
        sched = _scheduler(
            _flags(), classifier, cache, store, bars,
            now=datetime(2026, 5, 19, 14, 0, tzinfo=timezone.utc),  # pre-17:00
        )
        summary = sched.maybe_run([{"symbol": "BARC.L", "market": "LSE"}])
        assert "BARC.L" in summary["skipped_not_post_close"]
        assert summary["classified"] == []
        classifier.classify.assert_not_called()

    def test_after_lse_close_us_still_pending(self, cache, store, bars):
        """At 18:00 UTC: LSE post-close (>17:00), US not yet (<21:30)."""
        classifier = MagicMock()
        classifier.classify.return_value = _make_classification(
            "BARC.L", "2026-05-19",
        )
        sched = _scheduler(
            _flags(), classifier, cache, store, bars,
            now=datetime(2026, 5, 19, 18, 0, tzinfo=timezone.utc),
        )
        summary = sched.maybe_run([
            {"symbol": "BARC.L", "market": "LSE"},
            {"symbol": "AAPL"},
        ])
        assert any(c["symbol"] == "BARC.L" for c in summary["classified"])
        assert "AAPL" in summary["skipped_not_post_close"]

    def test_after_us_close_both_fire(self, cache, store, bars):
        classifier = MagicMock()
        classifier.classify.side_effect = [
            _make_classification("BARC.L", "2026-05-19"),
            _make_classification("AAPL", "2026-05-19"),
        ]
        sched = _scheduler(
            _flags(), classifier, cache, store, bars,
            now=datetime(2026, 5, 19, 22, 0, tzinfo=timezone.utc),
        )
        summary = sched.maybe_run([
            {"symbol": "BARC.L", "market": "LSE"},
            {"symbol": "AAPL"},
        ])
        symbols = [c["symbol"] for c in summary["classified"]]
        assert "BARC.L" in symbols
        assert "AAPL" in symbols


class TestIdempotency:
    def test_second_run_same_day_is_cache_hit(self, cache, store, bars):
        classifier = MagicMock()
        classifier.classify.return_value = _make_classification(
            "AAPL", "2026-05-19",
        )
        sched = _scheduler(
            _flags(), classifier, cache, store, bars,
            now=datetime(2026, 5, 19, 22, 0, tzinfo=timezone.utc),
        )
        first = sched.maybe_run([{"symbol": "AAPL"}])
        assert any(c["symbol"] == "AAPL" for c in first["classified"])

        second = sched.maybe_run([{"symbol": "AAPL"}])
        assert "AAPL" in second["skipped_cache_hit"]
        # classifier called exactly once across both runs
        assert classifier.classify.call_count == 1

    def test_fresh_run_writes_cache_row(self, cache, store, bars):
        classifier = MagicMock()
        classifier.classify.return_value = _make_classification(
            "AAPL", "2026-05-19",
        )
        sched = _scheduler(
            _flags(), classifier, cache, store, bars,
            now=datetime(2026, 5, 19, 22, 0, tzinfo=timezone.utc),
        )
        sched.maybe_run([{"symbol": "AAPL"}])
        assert cache.has_for_day("AAPL", "2026-05-19") is True


class TestFallbackHandling:
    def test_fallback_classification_marks_day_done(self, cache, store, bars):
        """Budget exceeded ⇒ classifier returns UNCLEAR fallback.
        Scheduler still persists it so we don't retry within the day."""
        classifier = MagicMock()
        classifier.classify.return_value = _make_classification(
            "AAPL", "2026-05-19", regime="UNCLEAR", confidence=0.0,
        )
        sched = _scheduler(
            _flags(), classifier, cache, store, bars,
            now=datetime(2026, 5, 19, 22, 0, tzinfo=timezone.utc),
        )
        first = sched.maybe_run([{"symbol": "AAPL"}])
        assert any(c["symbol"] == "AAPL" for c in first["classified"])
        assert cache.has_for_day("AAPL", "2026-05-19") is True

        second = sched.maybe_run([{"symbol": "AAPL"}])
        assert "AAPL" in second["skipped_cache_hit"]
        assert classifier.classify.call_count == 1


class TestSmoothingPersistence:
    def test_first_classification_seeds_smoothed_state(self, cache, store, bars):
        classifier = MagicMock()
        classifier.classify.return_value = _make_classification(
            "AAPL", "2026-05-19", regime="TRENDING", confidence=0.9,
        )
        sched = _scheduler(
            _flags(), classifier, cache, store, bars,
            now=datetime(2026, 5, 19, 22, 0, tzinfo=timezone.utc),
        )
        sched.maybe_run([{"symbol": "AAPL"}])

        smoothed = store.get_latest("AAPL")
        assert smoothed is not None
        # First-run high-confidence promotion (§9.3) → TRENDING on day 1
        assert smoothed.effective_regime == "TRENDING"

    def test_subsequent_classification_updates_smoothed(self, cache, store, bars):
        # Day 1: TRENDING
        classifier = MagicMock()
        classifier.classify.return_value = _make_classification(
            "AAPL", "2026-05-19", regime="TRENDING", confidence=0.9,
        )
        sched = _scheduler(
            _flags(), classifier, cache, store, bars,
            now=datetime(2026, 5, 19, 22, 0, tzinfo=timezone.utc),
        )
        sched.maybe_run([{"symbol": "AAPL"}])

        # Day 2: another TRENDING confirmation → days_in_regime increments
        classifier2 = MagicMock()
        classifier2.classify.return_value = _make_classification(
            "AAPL", "2026-05-20", regime="TRENDING", confidence=0.9,
        )
        sched2 = _scheduler(
            _flags(), classifier2, cache, store, bars,
            now=datetime(2026, 5, 20, 22, 0, tzinfo=timezone.utc),
        )
        sched2.maybe_run([{"symbol": "AAPL"}])

        smoothed = store.get_latest("AAPL")
        assert smoothed.effective_regime == "TRENDING"
        assert smoothed.days_in_regime >= 2


class TestErrorIsolation:
    def test_insufficient_bars_recorded_as_error(self, cache, store):
        short_df = _bars_df(n=50)  # below MIN_BARS_REQUIRED
        classifier = MagicMock()
        sched = _scheduler(
            _flags(), classifier, cache, store, short_df,
            now=datetime(2026, 5, 19, 22, 0, tzinfo=timezone.utc),
        )
        summary = sched.maybe_run([{"symbol": "AAPL"}])
        assert any(e["symbol"] == "AAPL" for e in summary["errors"])
        classifier.classify.assert_not_called()
        assert cache.has_for_day("AAPL", "2026-05-19") is False

    def test_one_instrument_failure_does_not_block_others(self, cache, store, bars):
        def fetcher(inst):
            if inst["symbol"] == "BAD":
                return None
            return bars
        classifier = MagicMock()
        classifier.classify.return_value = _make_classification(
            "AAPL", "2026-05-19",
        )
        sched = _scheduler(
            _flags(), classifier, cache, store, fetcher,
            now=datetime(2026, 5, 19, 22, 0, tzinfo=timezone.utc),
        )
        summary = sched.maybe_run([
            {"symbol": "BAD"},
            {"symbol": "AAPL"},
        ])
        assert any(e["symbol"] == "BAD" for e in summary["errors"])
        assert any(c["symbol"] == "AAPL" for c in summary["classified"])
