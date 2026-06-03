"""
§14: Regime orchestrator integration tests.

Tests that the orchestrator plugin gates entries correctly via
pre_trade() without modifying layer1.py.
"""
from datetime import datetime, timezone

import pytest
from unittest.mock import MagicMock, patch

from bot.regime.flags import FeatureFlags
from bot.regime.models import SmoothedRegimeState
from bot.regime.orchestrator import RegimeOrchestrator
from bot.regime.router import route as regime_route


def _make_orchestrator(flag_overrides=None, pause_registry=None,
                       overlay_fn=None, router_fn=None, smoothing_store=None):
    flags = FeatureFlags(flag_overrides or {})
    return RegimeOrchestrator(
        flags=flags,
        pause_registry=pause_registry,
        overlay_registry_fn=overlay_fn,
        router_fn=router_fn,
        smoothing_store=smoothing_store,
    )


def _router_live_flags():
    """§6.3: enabling enable_router_live requires the full upstream chain
    (classifier_shadow → classifier_live → persistence_shadow →
    persistence_live → router_shadow → router_live)."""
    return {
        "enable_classifier_shadow": True,
        "enable_classifier_live": True,
        "enable_persistence_shadow": True,
        "enable_persistence_live": True,
        "enable_router_shadow": True,
        "enable_router_live": True,
    }


def _smoothed(symbol, regime, days=3):
    return SmoothedRegimeState(
        instrument=symbol,
        effective_regime=regime,
        source_regime=regime,
        days_in_regime=days,
        last_changed_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        confidence=0.9,
        pending_regime=None,
        pending_days=0,
        regime_history=[],
    )


def _inst(symbol="AAPL"):
    return {"symbol": symbol, "name": "Apple Inc"}


class TestPreTradeGating:
    def test_all_off_allows_entry(self):
        orch = _make_orchestrator()
        assert orch.pre_trade(_inst(), signal=1, confidence="HIGH") is True

    def test_paused_blocks_entry(self):
        pause = MagicMock()
        pause.is_paused.return_value = True
        pause.pause_reason.return_value = "DATA_QUALITY hard failure"
        orch = _make_orchestrator(pause_registry=pause)
        assert orch.pre_trade(_inst(), signal=1, confidence="HIGH") is False
        result = orch.last_gate_result("AAPL")
        assert result.gate == "pause"

    def test_overlay_blocks_when_live(self):
        overlay_fn = MagicMock(return_value=[
            {"overlay_name": "MACRO_LOCKOUT", "reason": "Fed meeting"}
        ])
        orch = _make_orchestrator(
            flag_overrides={
                "enable_event_overlays_shadow": True,
                "enable_event_overlays_live": True,
            },
            overlay_fn=overlay_fn,
        )
        assert orch.pre_trade(_inst(), signal=1, confidence="HIGH") is False
        result = orch.last_gate_result("AAPL")
        assert result.gate == "overlay"

    def test_overlay_ignored_when_not_live(self):
        overlay_fn = MagicMock(return_value=[
            {"overlay_name": "MACRO_LOCKOUT", "reason": "Fed meeting"}
        ])
        orch = _make_orchestrator(
            flag_overrides={"enable_event_overlays_live": False},
            overlay_fn=overlay_fn,
        )
        assert orch.pre_trade(_inst(), signal=1, confidence="HIGH") is True

    def test_non_entry_signals_always_allowed(self):
        """Signal 0 (no action) should not be gate-checked."""
        pause = MagicMock()
        pause.is_paused.return_value = True
        orch = _make_orchestrator(pause_registry=pause)
        assert orch.pre_trade(_inst(), signal=0, confidence="NONE") is True

    def test_close_signal_not_blocked(self):
        """Close signals (signal=-1 for close) pass through when not entry."""
        orch = _make_orchestrator()
        assert orch.pre_trade(_inst(), signal=0, confidence="NONE") is True


class TestOnStart:
    def test_on_start_sends_telegram(self):
        telegram = MagicMock()
        flags = FeatureFlags({})
        orch = RegimeOrchestrator(flags=flags, telegram_alerts=telegram)
        orch.on_start()
        telegram.send.assert_called_once()

    def test_on_start_no_telegram(self):
        flags = FeatureFlags({})
        orch = RegimeOrchestrator(flags=flags)
        orch.on_start()


class TestSmoothedStateReads:
    """Gap #8 / Commit 2: orchestrator must consult smoothing_store.

    Before this commit, _get_routing always received smoothed=None and
    bailed out — the router was structurally inert."""

    def test_routing_passes_store_value_to_router(self):
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "TRENDING")
        router_fn = MagicMock(return_value=None)
        # router_fn return is irrelevant here — what we assert is that the
        # orchestrator looked up the store and forwarded the smoothed state.
        orch = _make_orchestrator(
            router_fn=router_fn,
            smoothing_store=store,
        )
        orch.pre_trade(_inst("AAPL"), signal=1, confidence="HIGH")

        store.get_latest.assert_called_once_with("AAPL")
        assert router_fn.call_count == 1
        smoothed_arg = router_fn.call_args.args[0]
        assert smoothed_arg.effective_regime == "TRENDING"

    def test_missing_smoothed_state_falls_back_to_allow(self):
        """Warm-up day: scheduler hasn't run yet, store has no row.
        Preserves pre-commit-2 behaviour (no routing → no router block)."""
        store = MagicMock()
        store.get_latest.return_value = None
        router_fn = MagicMock()
        orch = _make_orchestrator(
            router_fn=router_fn,
            smoothing_store=store,
            flag_overrides=_router_live_flags(),
        )
        assert orch.pre_trade(_inst("AAPL"), signal=1, confidence="HIGH") is True
        router_fn.assert_not_called()

    def test_router_blocks_when_live_and_unclear(self):
        """End-to-end gate flow: UNCLEAR smoothed state + router_live=True
        → router_fn returns block decision → EntryGateResult.gate == 'router'."""
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "UNCLEAR")
        orch = _make_orchestrator(
            router_fn=regime_route,
            smoothing_store=store,
            flag_overrides=_router_live_flags(),
        )
        allowed = orch.pre_trade(_inst("AAPL"), signal=1, confidence="HIGH")
        assert allowed is False
        result = orch.last_gate_result("AAPL")
        assert result.gate == "router"
        assert "UNCLEAR" in result.block_reason

    def test_router_allows_trending_when_live(self):
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "TRENDING")
        orch = _make_orchestrator(
            router_fn=regime_route,
            smoothing_store=store,
            flag_overrides=_router_live_flags(),
        )
        allowed = orch.pre_trade(_inst("AAPL"), signal=1, confidence="HIGH")
        assert allowed is True

    def test_explicit_smoothed_arg_skips_store(self):
        """Direct callers (e.g. shadow simulator) can pass smoothed explicitly
        and the orchestrator should not consult the store in that case."""
        store = MagicMock()
        router_fn = MagicMock(return_value=None)
        orch = _make_orchestrator(
            router_fn=router_fn,
            smoothing_store=store,
        )
        explicit = _smoothed("AAPL", "RANGING")
        orch._get_routing("AAPL", smoothed=explicit)
        store.get_latest.assert_not_called()
        smoothed_arg = router_fn.call_args.args[0]
        assert smoothed_arg.effective_regime == "RANGING"


class TestGateResultTracking:
    def test_last_gate_result_stored(self):
        orch = _make_orchestrator()
        orch.pre_trade(_inst("MSFT"), signal=1, confidence="HIGH")
        result = orch.last_gate_result("MSFT")
        assert result is not None
        assert result.allow is True

    def test_last_gate_result_none_for_unseen(self):
        orch = _make_orchestrator()
        assert orch.last_gate_result("UNKNOWN") is None


class TestShadowSignalLogging:
    """Gap #10: log_signal writes a shadow_decisions row for every BUY/SELL
    engine signal, regardless of whether layer1 reaches pre_trade. The
    live_blocked_by argument carries which upstream gate (if any) stopped
    the trade — None means live took it."""

    def _orch_with_logger(self, cf_logger, **kwargs):
        flags = FeatureFlags(kwargs.pop("flag_overrides", None) or {})
        return RegimeOrchestrator(
            flags=flags,
            counterfactual_logger=cf_logger,
            **kwargs,
        )

    def test_signal_taken_logs_with_no_block_reason(self):
        """Signal fires + capital available + shadow allows → row with
        live_blocked_by=None, both actions TAKE, no disagreement."""
        cf_logger = MagicMock()
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "TRENDING")
        orch = self._orch_with_logger(
            cf_logger,
            router_fn=regime_route,
            smoothing_store=store,
        )

        orch.log_signal(_inst("AAPL"), signal=1, confidence="HIGH",
                        live_blocked_by=None)

        cf_logger.log_decision.assert_called_once()
        kwargs = cf_logger.log_decision.call_args.kwargs
        assert kwargs["instrument"] == "AAPL"
        assert kwargs["live_blocked_by"] is None
        assert kwargs["live_action"] == "TAKE"
        assert kwargs["shadow_action"] == "TAKE"
        assert kwargs["disagreement_type"] is None
        assert kwargs["shadow_smoothed_regime"] == "TRENDING"
        assert kwargs["live_engine"] == "triple_confirmation"
        assert kwargs["live_signal"] == {"signal": 1, "confidence": "HIGH"}

    def test_position_limit_with_shadow_allow_logs_disagreement(self):
        """Signal fires + capital full + shadow allows → row with
        live_blocked_by='position_limit', live=BLOCK, shadow=TAKE,
        disagreement_type='shadow_allows_live_blocks'."""
        cf_logger = MagicMock()
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "TRENDING")
        orch = self._orch_with_logger(
            cf_logger,
            router_fn=regime_route,
            smoothing_store=store,
        )

        orch.log_signal(_inst("AAPL"), signal=1, confidence="HIGH",
                        live_blocked_by="position_limit")

        kwargs = cf_logger.log_decision.call_args.kwargs
        assert kwargs["live_blocked_by"] == "position_limit"
        assert kwargs["live_action"] == "BLOCK"
        assert kwargs["shadow_action"] == "TAKE"
        assert kwargs["disagreement_type"] == "shadow_allows_live_blocks"

    def test_position_limit_with_shadow_block_records_diagnostic(self):
        """Signal fires + capital full + shadow blocks (UNCLEAR) → row with
        live_blocked_by='position_limit', both actions BLOCK,
        disagreement_type='shadow_blocked_position_limit_blocked'."""
        cf_logger = MagicMock()
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "UNCLEAR")
        orch = self._orch_with_logger(
            cf_logger,
            router_fn=regime_route,
            smoothing_store=store,
        )

        orch.log_signal(_inst("AAPL"), signal=1, confidence="HIGH",
                        live_blocked_by="position_limit")

        kwargs = cf_logger.log_decision.call_args.kwargs
        assert kwargs["live_blocked_by"] == "position_limit"
        assert kwargs["live_action"] == "BLOCK"
        assert kwargs["shadow_action"] == "BLOCK"
        assert kwargs["disagreement_type"] == "shadow_blocked_position_limit_blocked"
        assert kwargs["shadow_smoothed_regime"] == "UNCLEAR"

    def test_non_entry_signal_does_not_log(self):
        """signal=0 short-circuits — no comparison to record."""
        cf_logger = MagicMock()
        orch = self._orch_with_logger(cf_logger)
        orch.log_signal(_inst("AAPL"), signal=0, confidence="NONE",
                        live_blocked_by=None)
        cf_logger.log_decision.assert_not_called()

    def test_pre_trade_does_not_write_shadow_row(self):
        """After gap #10, pre_trade is gate-only. Shadow logging lives
        in log_signal so layer1 can drive it before its position-limit
        check."""
        cf_logger = MagicMock()
        orch = self._orch_with_logger(cf_logger)
        orch.pre_trade(_inst("AAPL"), signal=1, confidence="HIGH")
        cf_logger.log_decision.assert_not_called()

    def test_logger_exception_does_not_propagate(self):
        cf_logger = MagicMock()
        cf_logger.log_decision.side_effect = RuntimeError("disk full")
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "TRENDING")
        orch = self._orch_with_logger(
            cf_logger,
            router_fn=regime_route,
            smoothing_store=store,
        )

        # Must not raise.
        orch.log_signal(_inst("AAPL"), signal=1, confidence="HIGH",
                        live_blocked_by=None)
        cf_logger.log_decision.assert_called_once()

    def test_no_logger_is_silent(self):
        orch = RegimeOrchestrator(flags=FeatureFlags({}),
                                  counterfactual_logger=None)
        # Must not raise.
        orch.log_signal(_inst("AAPL"), signal=1, confidence="HIGH",
                        live_blocked_by="position_limit")

    def test_validation_block_is_shadow_blocked_live_would_take(self):
        """live_blocked_by='order_validator' + shadow blocks → diagnostic
        bucket for 'shadow agrees the trade is bad but live also blocks
        via a different mechanism'."""
        cf_logger = MagicMock()
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "UNCLEAR")
        orch = self._orch_with_logger(
            cf_logger,
            router_fn=regime_route,
            smoothing_store=store,
        )

        orch.log_signal(_inst("AAPL"), signal=1, confidence="HIGH",
                        live_blocked_by="order_validator")

        kwargs = cf_logger.log_decision.call_args.kwargs
        assert kwargs["disagreement_type"] == "shadow_blocked_live_would_take"

    def test_orchestrator_block_is_agreement(self):
        """live_blocked_by='orchestrator' + shadow blocks → both gates
        are the same evaluation, so this is agreement not disagreement."""
        cf_logger = MagicMock()
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "UNCLEAR")
        orch = self._orch_with_logger(
            cf_logger,
            router_fn=regime_route,
            smoothing_store=store,
            flag_overrides=_router_live_flags(),  # orchestrator gate is live
        )

        orch.log_signal(_inst("AAPL"), signal=1, confidence="HIGH",
                        live_blocked_by="orchestrator")

        kwargs = cf_logger.log_decision.call_args.kwargs
        assert kwargs["live_blocked_by"] == "orchestrator"
        assert kwargs["live_action"] == "BLOCK"
        assert kwargs["shadow_action"] == "BLOCK"
        assert kwargs["disagreement_type"] is None
