"""
Entry gate logic — §14.1-14.2 of CLAUDE_STRATEGY_SPEC_v3.

Three independent entry gates, evaluated in order:
  1. instrument_pause_registry.is_paused()
  2. overlay-active (gated by enable_event_overlays_live)
  3. router (gated by enable_router_live)

§14.2 truth table:
| router_live | overlays_live | overlays | UNCLEAR | paused | → allow |
|-------------|---------------|----------|---------|--------|---------|
| F           | F             | —        | —       | F      | True    |
| F           | T             | T        | —       | F      | False   |
| F           | T             | F        | —       | F      | True    |
| T           | F             | —        | T       | F      | False   |
| T           | T             | T        | F       | F      | False   |
| T           | T             | F        | T       | F      | False   |
| T           | T             | F        | F       | F      | True    |
| —           | —             | —        | —       | T      | False   |
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EntryGateResult:
    allow: bool
    block_reason: Optional[str]
    gate: Optional[str]  # which gate blocked: "pause", "overlay", "router", None


def evaluate_entry_gates(
    is_paused: bool,
    pause_reason: Optional[str],
    overlays_live: bool,
    active_overlay_names: list,
    router_live: bool,
    router_allows: bool,
    router_block_reason: Optional[str],
) -> EntryGateResult:
    """Evaluate the three independent entry gates in order.

    Returns EntryGateResult with allow=True if all gates pass.
    """
    # Gate 1: instrument pause registry
    if is_paused:
        return EntryGateResult(
            allow=False,
            block_reason=f"Instrument paused: {pause_reason}",
            gate="pause",
        )

    # Gate 2: overlay-active (only if enable_event_overlays_live)
    if overlays_live and len(active_overlay_names) > 0:
        return EntryGateResult(
            allow=False,
            block_reason=f"Overlay active: {active_overlay_names}",
            gate="overlay",
        )

    # Gate 3: router (only if enable_router_live)
    if router_live and not router_allows:
        return EntryGateResult(
            allow=False,
            block_reason=router_block_reason,
            gate="router",
        )

    return EntryGateResult(allow=True, block_reason=None, gate=None)
