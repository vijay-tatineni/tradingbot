"""
§14.2: Entry gate independence — full 8-row truth table.

Overlays can block entries without router being live.
Router can block entries without overlays being live.
Instrument pause always blocks regardless of other gates.
"""
import pytest

from bot.regime.entry_gate import evaluate_entry_gates, EntryGateResult


# Row 1: router_live=F, overlays_live=F → True
def test_row1_both_off_no_pause():
    r = evaluate_entry_gates(
        is_paused=False, pause_reason=None,
        overlays_live=False, active_overlay_names=[],
        router_live=False, router_allows=True, router_block_reason=None,
    )
    assert r.allow is True
    assert r.gate is None


# Row 2: router_live=F, overlays_live=T, overlays present → False (overlay)
def test_row2_overlays_live_with_overlays():
    r = evaluate_entry_gates(
        is_paused=False, pause_reason=None,
        overlays_live=True, active_overlay_names=["MACRO_LOCKOUT"],
        router_live=False, router_allows=True, router_block_reason=None,
    )
    assert r.allow is False
    assert r.gate == "overlay"
    assert "MACRO_LOCKOUT" in r.block_reason


# Row 3: router_live=F, overlays_live=T, no overlays → True
def test_row3_overlays_live_no_overlays():
    r = evaluate_entry_gates(
        is_paused=False, pause_reason=None,
        overlays_live=True, active_overlay_names=[],
        router_live=False, router_allows=True, router_block_reason=None,
    )
    assert r.allow is True


# Row 4: router_live=T, overlays_live=F, regime UNCLEAR → False (router)
def test_row4_router_live_unclear():
    r = evaluate_entry_gates(
        is_paused=False, pause_reason=None,
        overlays_live=False, active_overlay_names=[],
        router_live=True, router_allows=False, router_block_reason="Regime UNCLEAR",
    )
    assert r.allow is False
    assert r.gate == "router"
    assert "UNCLEAR" in r.block_reason


# Row 5: router_live=T, overlays_live=T, overlays present, trending → False (overlay)
def test_row5_both_live_with_overlays():
    r = evaluate_entry_gates(
        is_paused=False, pause_reason=None,
        overlays_live=True, active_overlay_names=["DATA_QUALITY"],
        router_live=True, router_allows=True, router_block_reason=None,
    )
    assert r.allow is False
    assert r.gate == "overlay"


# Row 6: router_live=T, overlays_live=T, no overlays, UNCLEAR → False (router)
def test_row6_both_live_no_overlays_unclear():
    r = evaluate_entry_gates(
        is_paused=False, pause_reason=None,
        overlays_live=True, active_overlay_names=[],
        router_live=True, router_allows=False, router_block_reason="Regime UNCLEAR",
    )
    assert r.allow is False
    assert r.gate == "router"


# Row 7: router_live=T, overlays_live=T, no overlays, trending → True
def test_row7_both_live_no_overlays_trending():
    r = evaluate_entry_gates(
        is_paused=False, pause_reason=None,
        overlays_live=True, active_overlay_names=[],
        router_live=True, router_allows=True, router_block_reason=None,
    )
    assert r.allow is True


# Row 8: instrument paused → always False regardless of other flags
def test_row8_instrument_paused():
    r = evaluate_entry_gates(
        is_paused=True, pause_reason="DATA_QUALITY hard failure",
        overlays_live=True, active_overlay_names=[],
        router_live=True, router_allows=True, router_block_reason=None,
    )
    assert r.allow is False
    assert r.gate == "pause"


# Additional: overlay gate precedence over router gate
def test_overlay_gate_precedes_router_gate():
    """When both overlay and router would block, overlay blocks first."""
    r = evaluate_entry_gates(
        is_paused=False, pause_reason=None,
        overlays_live=True, active_overlay_names=["MACRO_LOCKOUT"],
        router_live=True, router_allows=False, router_block_reason="Regime UNCLEAR",
    )
    assert r.gate == "overlay"


# Additional: pause precedes all
def test_pause_precedes_all():
    r = evaluate_entry_gates(
        is_paused=True, pause_reason="TEST",
        overlays_live=True, active_overlay_names=["DATA_QUALITY"],
        router_live=True, router_allows=False, router_block_reason="UNCLEAR",
    )
    assert r.gate == "pause"


# Additional: overlays_live=False ignores overlay presence
def test_overlays_off_ignores_active_overlays():
    """When overlays flag is off, active overlays don't block entries."""
    r = evaluate_entry_gates(
        is_paused=False, pause_reason=None,
        overlays_live=False, active_overlay_names=["MACRO_LOCKOUT", "DATA_QUALITY"],
        router_live=False, router_allows=True, router_block_reason=None,
    )
    assert r.allow is True
