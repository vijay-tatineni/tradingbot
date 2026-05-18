"""
Regime router — §9.8 of CLAUDE_STRATEGY_SPEC_v3.

Deterministic mapping from smoothed regime + overlay state + flags
to engine selection and entry permission.

Routing table (v2 unambiguous):
| Effective regime | Overlays? | mean_reversion_live | Engine                   | allow_entries | block_reason               |
|------------------|-----------|---------------------|--------------------------|---------------|----------------------------|
| TRENDING         | No        | —                   | TripleConfirmationEngine | True          | None                       |
| TRENDING         | Yes       | —                   | TripleConfirmationEngine | False         | "Overlay active: …"        |
| RANGING          | No        | True                | MeanReversionEngine      | True          | None                       |
| RANGING          | No        | False               | NoOpEngine               | False         | "Mean-reversion not live"  |
| RANGING          | Yes       | True                | MeanReversionEngine      | False         | "Overlay active: …"        |
| RANGING          | Yes       | False               | NoOpEngine               | False         | "Mean-reversion not live…" |
| UNCLEAR          | —         | —                   | NoOpEngine               | False         | "Regime UNCLEAR"           |

Invariant: selected_engine == "NoOpEngine" ⇒ allow_new_entries == False
"""
from datetime import datetime, timezone
from typing import Optional

from bot.regime.models import SmoothedRegimeState, RoutingDecision

OverlayCheck = dict  # Will be replaced with proper type in PR2


def route(smoothed: SmoothedRegimeState,
          active_overlays: list,
          flags: dict) -> RoutingDecision:
    """Route an instrument to a strategy engine based on regime and overlays.

    Args:
        smoothed: Current smoothed regime state for the instrument.
        active_overlays: List of active overlay checks (dicts with overlay_name, reason).
        flags: Dict of feature flag values.

    Returns:
        RoutingDecision with engine selection and entry permission.
    """
    regime = smoothed.effective_regime
    has_overlays = len(active_overlays) > 0
    mean_reversion_live = flags.get("enable_mean_reversion_live", False)

    overlay_names = [o.get("overlay_name", str(o)) if isinstance(o, dict)
                     else getattr(o, "overlay_name", str(o))
                     for o in active_overlays]
    overlay_reasons = ", ".join(overlay_names)

    if regime == "UNCLEAR":
        engine = "NoOpEngine"
        allow = False
        reason = "Regime UNCLEAR"
    elif regime == "TRENDING":
        engine = "TripleConfirmationEngine"
        if has_overlays:
            allow = False
            reason = f"Overlay active: {overlay_reasons}"
        else:
            allow = True
            reason = None
    elif regime == "RANGING":
        if mean_reversion_live:
            engine = "MeanReversionEngine"
            if has_overlays:
                allow = False
                reason = f"Overlay active: {overlay_reasons}"
            else:
                allow = True
                reason = None
        else:
            engine = "NoOpEngine"
            if has_overlays:
                allow = False
                reason = f"Mean-reversion not enabled live; overlay also active: {overlay_reasons}"
            else:
                allow = False
                reason = "Mean-reversion not enabled live"
    else:
        engine = "NoOpEngine"
        allow = False
        reason = f"Unknown regime: {regime}"

    # Invariant: NoOpEngine never allows new entries
    assert not (engine == "NoOpEngine" and allow), \
        "Routing invariant violated: NoOpEngine must never allow new entries"

    earliest_expiry = None
    if has_overlays:
        expiries = []
        for o in active_overlays:
            exp = o.get("expires_at") if isinstance(o, dict) else getattr(o, "expires_at", None)
            if exp is not None:
                expiries.append(exp)
        if expiries:
            earliest_expiry = min(expiries)

    return RoutingDecision(
        instrument=smoothed.instrument,
        decided_at=datetime.now(timezone.utc),
        effective_regime=regime,
        selected_engine=engine,
        allow_new_entries=allow,
        active_overlays=overlay_names,
        overlay_expires_at=earliest_expiry,
        block_reason=reason,
        flag_snapshot=flags,
    )
