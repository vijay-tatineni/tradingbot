"""
Deliberate-block experiment: prove the orchestrator wire actually
blocks a trade in the layer1 plugin chain.

Sets up:
- enable_event_overlays_live=true
- MACRO_LOCKOUT overlay active for AAPL
- A signal=1 BUY candidate

Asserts: pre_trade chain returns False, trade is blocked,
orchestrator gate result shows "overlay" block.
"""
import pytest
from unittest.mock import MagicMock, patch

from bot.regime.flags import FeatureFlags
from bot.regime.orchestrator import RegimeOrchestrator
from bot.plugins.base_plugin import BasePlugin


class StubPlugin(BasePlugin):
    name = "StubPlugin"

    def pre_trade(self, inst, signal, confidence):
        return True


def test_deliberate_overlay_block():
    """Simulates the exact layer1 pre_trade chain with orchestrator wired in."""
    overlay_fn = MagicMock(return_value=[
        {"overlay_name": "MACRO_LOCKOUT", "reason": "FOMC rate decision today"}
    ])

    flags = FeatureFlags({
        "enable_event_overlays_shadow": True,
        "enable_event_overlays_live": True,
    })

    orchestrator = RegimeOrchestrator(
        flags=flags,
        overlay_registry_fn=overlay_fn,
    )

    plugins = [
        StubPlugin(),
        orchestrator,
        StubPlugin(),
    ]

    inst = {"symbol": "AAPL", "name": "Apple Inc", "flag": "🇺🇸"}

    allowed = all(p.pre_trade(inst, signal=1, confidence="HIGH") for p in plugins)

    assert allowed is False, (
        "DELIBERATE BLOCK FAILED: Trade went through despite MACRO_LOCKOUT "
        "overlay being active and enable_event_overlays_live=true. "
        "The orchestrator wire is broken."
    )

    result = orchestrator.last_gate_result("AAPL")
    assert result is not None, "No gate result recorded — pre_trade was never called"
    assert result.allow is False
    assert result.gate == "overlay"
    assert "MACRO_LOCKOUT" in result.block_reason

    print(f"\n=== DELIBERATE BLOCK EXPERIMENT ===")
    print(f"Instrument: AAPL")
    print(f"Signal: BUY (signal=1, confidence=HIGH)")
    print(f"Overlay: MACRO_LOCKOUT (FOMC rate decision today)")
    print(f"enable_event_overlays_live: true")
    print(f"Result: BLOCKED")
    print(f"Gate: {result.gate}")
    print(f"Reason: {result.block_reason}")
    print(f"=== EXPERIMENT PASSED ===\n")


def test_deliberate_pause_block():
    """Instrument pause blocks even when all other gates would allow."""
    pause = MagicMock()
    pause.is_paused.return_value = True
    pause.pause_reason.return_value = "DATA_QUALITY hard failure: 3 consecutive timeouts"

    flags = FeatureFlags({})
    orchestrator = RegimeOrchestrator(flags=flags, pause_registry=pause)
    plugins = [StubPlugin(), orchestrator]

    inst = {"symbol": "MSFT", "name": "Microsoft Corp"}
    allowed = all(p.pre_trade(inst, signal=1, confidence="HIGH") for p in plugins)

    assert allowed is False
    result = orchestrator.last_gate_result("MSFT")
    assert result.gate == "pause"

    print(f"\n=== PAUSE BLOCK EXPERIMENT ===")
    print(f"Instrument: MSFT")
    print(f"Gate: {result.gate}")
    print(f"Reason: {result.block_reason}")
    print(f"=== EXPERIMENT PASSED ===\n")


def test_no_block_when_overlays_off():
    """With overlays_live=false, overlay presence does NOT block."""
    overlay_fn = MagicMock(return_value=[
        {"overlay_name": "MACRO_LOCKOUT", "reason": "FOMC rate decision"}
    ])

    flags = FeatureFlags({
        "enable_event_overlays_live": False,
    })

    orchestrator = RegimeOrchestrator(
        flags=flags,
        overlay_registry_fn=overlay_fn,
    )
    plugins = [StubPlugin(), orchestrator]

    inst = {"symbol": "AAPL", "name": "Apple Inc"}
    allowed = all(p.pre_trade(inst, signal=1, confidence="HIGH") for p in plugins)

    assert allowed is True, (
        "Trade should NOT be blocked when enable_event_overlays_live=false, "
        "even with active overlays present."
    )

    print(f"\n=== NO-BLOCK CONTROL EXPERIMENT ===")
    print(f"enable_event_overlays_live: false")
    print(f"Overlays present: MACRO_LOCKOUT")
    print(f"Result: ALLOWED (correct — overlays not live)")
    print(f"=== EXPERIMENT PASSED ===\n")
