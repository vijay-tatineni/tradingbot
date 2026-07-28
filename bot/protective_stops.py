"""bot/protective_stops.py — level computation for broker-attached stops.

Pure arithmetic, no broker and no I/O, so the level that decides whether a
position is protected can be tested exhaustively.

**Why the broker stop sits at the *emergency* level.** The synthetic trailing
stop remains the primary exit while the bot is alive, complete with its
confirmation logic. The broker-held stop is a catastrophe backstop for process
or host death. Attaching at the trail level would let the broker fire without
the bot's confirmation logic and would change strategy behaviour; attaching at
the wider emergency level cannot, because by the time price reaches it the
synthetic layer has already had its chance to act.

**Units.** Stop levels are computed as a percentage of the entry reference
price and are therefore expressed in whatever units that reference price uses
-- market quote units, which is the same space IBKR uses for order prices.
No pence/pounds conversion belongs here. The ×100 relationship that
``ibkr_avg_cost_to_market`` exists to reconcile applies to ``avgCost``, a
currency-denominated cost basis, not to quotes or order prices. Confirmed
against the live gateway: ``reqContractDetails`` reports ``priceMagnifier=100``
for LSE/GBP contracts and ``1`` for US/USD, i.e. LSE quotes are magnified
(pence) relative to the currency unit, and order prices share the quote space.
"""

from __future__ import annotations

LONG = "LONG"
SHORT = "SHORT"


def resolve_emergency_stop_pct(inst: dict) -> float:
    """The instrument's tier-1 emergency stop percentage.

    Mirrors ``layer1._process_instrument`` exactly::

        emergency_stop_pct = inst.get('emergency_stop_pct', trail_stop_pct * 2)

    The spec glosses this level as "trail_stop_pct * 2", but that is only the
    *default*: 24 configured instruments set ``emergency_stop_pct`` explicitly
    (5.0, 8.0, 10.0 …). Hardcoding the doubling would place the broker stop at
    a different level than the bot's own emergency stop for every one of them,
    so the override must be honoured.
    """
    trail_stop_pct = float(inst.get("trail_stop_pct", 2.0))
    return float(inst.get("emergency_stop_pct", trail_stop_pct * 2))


def compute_stop_price(reference_price: float, side: str, pct: float) -> float:
    """Protective stop level for a position.

    Same arithmetic as ``PositionTracker.check_emergency_stop``, so the broker
    stop and the synthetic emergency stop describe the same price:

        LONG  -> reference * (1 - pct/100)   (stop below entry)
        SHORT -> reference * (1 + pct/100)   (stop above entry)

    ``reference_price`` is the entry reference in market quote units.
    """
    if reference_price <= 0:
        raise ValueError(f"reference_price must be positive, got {reference_price}")
    if pct <= 0:
        raise ValueError(f"stop pct must be positive, got {pct}")

    if str(side).upper() == SHORT:
        return reference_price * (1 + pct / 100.0)
    return reference_price * (1 - pct / 100.0)


def stop_order_action(side: str) -> str:
    """Closing action for a protective stop: SELL a long, BUY back a short."""
    return "BUY" if str(side).upper() == SHORT else "SELL"


def round_to_tick(price: float, min_tick: float) -> float:
    """Snap a stop level to the contract's tick grid.

    A stop rejected for an invalid price increment is, under the spec's
    attach-failure invariant, a position that gets flattened -- so rounding
    defensively here avoids throwing away good entries on a formatting detail.
    Rounds *away* from the position (down for a long's stop, and the caller
    passes an already-correct side-specific level), preserving the protective
    intent rather than tightening it.
    """
    if not min_tick or min_tick <= 0:
        return price
    steps = round(price / min_tick)
    return round(steps * min_tick, 10)


def describe(symbol: str, side: str, reference_price: float,
             pct: float, stop: float) -> str:
    """One-line human description, used in logs and alerts."""
    return (f"{symbol} {side} ref={reference_price:.4f} "
            f"emergency={pct:g}% stop={stop:.4f}")
