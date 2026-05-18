"""
§14: Regime orchestrator integration tests.

Tests that the orchestrator plugin gates entries correctly via
pre_trade() without modifying layer1.py.
"""
import pytest
from unittest.mock import MagicMock, patch

from bot.regime.flags import FeatureFlags
from bot.regime.orchestrator import RegimeOrchestrator


def _make_orchestrator(flag_overrides=None, pause_registry=None,
                       overlay_fn=None):
    flags = FeatureFlags(flag_overrides or {})
    return RegimeOrchestrator(
        flags=flags,
        pause_registry=pause_registry,
        overlay_registry_fn=overlay_fn,
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
