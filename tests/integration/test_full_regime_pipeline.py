"""
End-to-end regime pipeline integration test.

This is the test that would have caught Gap #8 — the classifier, smoothing,
router, and orchestrator all existed but no production code path connected
them. Anything below short of full E2E (unit tests for each component) was
green while the production loop did nothing.

Pipeline under test:
    scheduler.maybe_run()
        → bars_fetcher → compute_regime_features
        → classifier.classify (Anthropic API mocked)
        → cache.put (regime_classification_cache)
        → smoothing.update_first_run
        → smoothing_store.put (smoothed_regime_state)
    orchestrator.pre_trade()
        → smoothing_store.get_latest
        → router.route
        → evaluate_entry_gates
        → EntryGateResult
"""
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from bot.regime.cache import RegimeCache
from bot.regime.classifier import RegimeClassifier
from bot.regime.cost_tracker import CostTracker
from bot.regime.flags import FeatureFlags
from bot.regime.orchestrator import RegimeOrchestrator
from bot.regime.router import route as regime_route
from bot.regime.scheduler import RegimeClassificationScheduler
from bot.regime.smoothing_store import SmoothedStateStore


# ── Helpers ──────────────────────────────────────────────────────────

def _bars_df(n: int = 250) -> pd.DataFrame:
    """Synthetic OHLCV bars deep enough for MA200 + range_efficiency."""
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + np.abs(rng.normal(0, 0.5, n))
    low = close - np.abs(rng.normal(0, 0.5, n))
    open_ = close + rng.normal(0, 0.2, n)
    volume = rng.integers(1_000, 10_000, n)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    })


def _fake_anthropic_response(regime: str, confidence: float):
    """Mimic the shape of anthropic.types.Message for tool-use responses.

    The classifier reads .content[i].type, .name, .input plus .usage.*."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "classify_regime"
    tool_block.input = {
        "regime": regime,
        "confidence": confidence,
        "rationale": f"Synthetic {regime} for integration test",
        "key_features": ["adx_14", "ma_200_slope_pct_per_day"],
    }
    response = MagicMock()
    response.content = [tool_block]
    response.usage.input_tokens = 100
    response.usage.output_tokens = 50
    return response


def _router_live_flags() -> dict:
    """§6.3: enabling enable_router_live requires the full upstream chain."""
    return {
        "enable_classifier_shadow": True,
        "enable_classifier_live": True,
        "enable_persistence_shadow": True,
        "enable_persistence_live": True,
        "enable_router_shadow": True,
        "enable_router_live": True,
    }


def _build_pipeline(tmp_path, regime: str, confidence: float):
    """Wire up the full pipeline against a single shared SQLite DB and a
    mocked Anthropic client returning (regime, confidence)."""
    db_path = str(tmp_path / "regime.db")
    cache = RegimeCache(db_path)
    cost_tracker = CostTracker(db_path)
    store = SmoothedStateStore(db_path)

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        classifier = RegimeClassifier(cache, cost_tracker)
    classifier._client = MagicMock()
    classifier._client.messages.create.return_value = (
        _fake_anthropic_response(regime, confidence)
    )

    scheduler = RegimeClassificationScheduler(
        flags=FeatureFlags({"enable_classifier_shadow": True}),
        classifier=classifier,
        cache=cache,
        smoothing_store=store,
        bars_fetcher=lambda inst: _bars_df(),
        now_fn=lambda: datetime(2026, 5, 19, 22, 0, tzinfo=timezone.utc),
    )

    return {
        "db_path": db_path,
        "cache": cache,
        "store": store,
        "classifier": classifier,
        "scheduler": scheduler,
    }


# ── End-to-end tests ─────────────────────────────────────────────────

class TestTrendingPipelineAllowsEntry:
    """TRENDING classification → smoothed=TRENDING → router selects
    TripleConfirmationEngine with allow=True → gate allows."""

    def test_full_pipeline_trending(self, tmp_path):
        pipeline = _build_pipeline(tmp_path, regime="TRENDING", confidence=0.9)
        scheduler = pipeline["scheduler"]
        cache = pipeline["cache"]
        store = pipeline["store"]

        # 1. Scheduler runs → cache + smoothed store get populated
        summary = scheduler.maybe_run([{"symbol": "AAPL"}])
        assert any(c["symbol"] == "AAPL" for c in summary["classified"]), (
            f"Scheduler did not classify AAPL: {summary}"
        )

        # 2. Cache row exists for today
        assert cache.has_for_day("AAPL", "2026-05-19") is True

        # 3. Smoothed state persisted as TRENDING (§9.3 first-run promotion)
        smoothed = store.get_latest("AAPL")
        assert smoothed is not None
        assert smoothed.effective_regime == "TRENDING"

        # 4. Orchestrator constructed against the populated DB
        orch = RegimeOrchestrator(
            flags=FeatureFlags(_router_live_flags()),
            router_fn=regime_route,
            smoothing_store=store,
        )

        # 5. BUY signal goes through pre_trade
        allowed = orch.pre_trade({"symbol": "AAPL"}, signal=1, confidence="HIGH")
        assert allowed is True, (
            "TRENDING regime + no overlays should allow entry"
        )

        # 6. Gate result reflects the router evaluation
        result = orch.last_gate_result("AAPL")
        assert result is not None
        assert result.allow is True
        assert result.gate is None  # no gate blocked


class TestUnclearPipelineBlocksEntry:
    """UNCLEAR classification → smoothed=UNCLEAR → router selects
    NoOpEngine with allow=False → gate blocks via gate=='router'."""

    def test_full_pipeline_unclear(self, tmp_path):
        pipeline = _build_pipeline(tmp_path, regime="UNCLEAR", confidence=0.9)
        scheduler = pipeline["scheduler"]
        cache = pipeline["cache"]
        store = pipeline["store"]

        summary = scheduler.maybe_run([{"symbol": "AAPL"}])
        assert any(c["symbol"] == "AAPL" for c in summary["classified"])
        assert cache.has_for_day("AAPL", "2026-05-19") is True

        smoothed = store.get_latest("AAPL")
        assert smoothed is not None
        assert smoothed.effective_regime == "UNCLEAR"

        orch = RegimeOrchestrator(
            flags=FeatureFlags(_router_live_flags()),
            router_fn=regime_route,
            smoothing_store=store,
        )

        allowed = orch.pre_trade({"symbol": "AAPL"}, signal=1, confidence="HIGH")
        assert allowed is False, (
            "UNCLEAR regime should block entry via router gate"
        )

        result = orch.last_gate_result("AAPL")
        assert result is not None
        assert result.allow is False
        assert result.gate == "router", (
            f"Expected router gate to block, got gate={result.gate} "
            f"reason={result.block_reason}"
        )
        assert "UNCLEAR" in (result.block_reason or "")


class TestPipelineIdempotency:
    """Two scheduler invocations on the same day → exactly one API call."""

    def test_second_cycle_does_not_call_api(self, tmp_path):
        pipeline = _build_pipeline(tmp_path, regime="TRENDING", confidence=0.9)
        scheduler = pipeline["scheduler"]
        classifier = pipeline["classifier"]

        first = scheduler.maybe_run([{"symbol": "AAPL"}])
        assert any(c["symbol"] == "AAPL" for c in first["classified"])
        assert classifier._client.messages.create.call_count == 1

        second = scheduler.maybe_run([{"symbol": "AAPL"}])
        assert "AAPL" in second["skipped_cache_hit"]
        # Second call hit the cache, did not invoke Anthropic again.
        assert classifier._client.messages.create.call_count == 1


class TestShadowModeDoesNotBlock:
    """With enable_router_live=False (default shadow), router decision is
    evaluated but the gate does not block. Confirms shadow stays observation-only."""

    def test_unclear_in_shadow_still_allows(self, tmp_path):
        pipeline = _build_pipeline(tmp_path, regime="UNCLEAR", confidence=0.9)
        scheduler = pipeline["scheduler"]
        store = pipeline["store"]

        scheduler.maybe_run([{"symbol": "AAPL"}])

        # Shadow flags only — no live routing
        orch = RegimeOrchestrator(
            flags=FeatureFlags({
                "enable_classifier_shadow": True,
                "enable_persistence_shadow": True,
                "enable_router_shadow": True,
            }),
            router_fn=regime_route,
            smoothing_store=store,
        )

        allowed = orch.pre_trade({"symbol": "AAPL"}, signal=1, confidence="HIGH")
        assert allowed is True, "Shadow mode must never block via router gate"
        result = orch.last_gate_result("AAPL")
        assert result.allow is True
        assert result.gate is None
