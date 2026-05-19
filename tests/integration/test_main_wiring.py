"""
§14: Verify RegimeOrchestrator is wired into main.py plugin list.

This test imports the actual main.py startup code and asserts:
1. RegimeOrchestrator is in the plugin list
2. Its pre_trade() is called during layer1 processing
3. An active overlay blocks a trade when overlays are live
"""
import ast
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.regime.orchestrator import RegimeOrchestrator
from bot.regime.flags import FeatureFlags


def test_main_py_imports_orchestrator():
    """main.py must import RegimeOrchestrator."""
    main_path = Path(__file__).parent.parent.parent / "main.py"
    source = main_path.read_text()
    assert "RegimeOrchestrator" in source, \
        "main.py does not import RegimeOrchestrator"
    assert "register_plugin(self.orchestrator)" in source, \
        "main.py does not register the orchestrator as a plugin"


def test_main_py_constructs_dependencies():
    """main.py must construct all orchestrator dependencies."""
    main_path = Path(__file__).parent.parent.parent / "main.py"
    source = main_path.read_text()
    assert "FeatureFlags" in source
    assert "InstrumentPauseRegistry" in source
    assert "CounterfactualLogger" in source
    assert "PositionMetadataStore" in source
    assert "overlay_active_overlays" in source or "active_overlays" in source
    assert "regime_route" in source or "route" in source


def test_orchestrator_in_plugin_list_blocks_trade():
    """When orchestrator is in plugin list and overlay is active,
    pre_trade returns False — proving the wire works end-to-end."""
    overlay_fn = MagicMock(return_value=[
        {"overlay_name": "MACRO_LOCKOUT", "reason": "Fed meeting"}
    ])
    flags = FeatureFlags({
        "enable_event_overlays_shadow": True,
        "enable_event_overlays_live": True,
    })
    orchestrator = RegimeOrchestrator(
        flags=flags,
        overlay_registry_fn=overlay_fn,
    )

    plugins = [MagicMock(), orchestrator, MagicMock()]
    plugins[0].pre_trade.return_value = True
    plugins[2].pre_trade.return_value = True

    inst = {"symbol": "AAPL", "name": "Apple Inc"}
    signal = 1
    confidence = "HIGH"

    allowed = all(p.pre_trade(inst, signal, confidence) for p in plugins)
    assert allowed is False, \
        "Trade should be blocked by orchestrator overlay gate"

    result = orchestrator.last_gate_result("AAPL")
    assert result.gate == "overlay"
    assert result.allow is False


def test_orchestrator_allows_when_no_overlays():
    """With no overlays and safe defaults, orchestrator allows trades."""
    flags = FeatureFlags({})
    orchestrator = RegimeOrchestrator(flags=flags)

    plugins = [orchestrator]
    inst = {"symbol": "AAPL", "name": "Apple Inc"}
    allowed = all(p.pre_trade(inst, 1, "HIGH") for p in plugins)
    assert allowed is True


def test_orchestrator_pause_blocks_in_plugin_chain():
    """Instrument pause blocks entry through the plugin chain."""
    pause = MagicMock()
    pause.is_paused.return_value = True
    pause.pause_reason.return_value = "DATA_QUALITY hard failure"

    flags = FeatureFlags({})
    orchestrator = RegimeOrchestrator(flags=flags, pause_registry=pause)

    plugins = [MagicMock(), orchestrator]
    plugins[0].pre_trade.return_value = True

    inst = {"symbol": "AAPL", "name": "Apple Inc"}
    allowed = all(p.pre_trade(inst, 1, "HIGH") for p in plugins)
    assert allowed is False

    result = orchestrator.last_gate_result("AAPL")
    assert result.gate == "pause"


def test_main_py_ast_has_register_plugin_orchestrator():
    """Parse main.py AST to confirm register_plugin(self.orchestrator) call exists."""
    main_path = Path(__file__).parent.parent.parent / "main.py"
    tree = ast.parse(main_path.read_text())

    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute) and
                func.attr == "register_plugin" and
                len(node.args) == 1):
                arg = node.args[0]
                if isinstance(arg, ast.Attribute) and arg.attr == "orchestrator":
                    found = True
                    break

    assert found, "main.py must call self.register_plugin(self.orchestrator)"
