"""
bot/sizing.py — Position sizing.

Two modes:

* **Fixed-fractional risk sizing** (default). Size so that a move to the
  protective stop costs a fixed fraction of live account equity::

      risk_capital  = equity x risk_fraction
      stop_distance = entry_price x (trail_stop_pct / 100)     per share
      qty           = floor(risk_capital / stop_distance)

  The stop distance is the **synthetic trailing stop** -- the primary exit
  while the bot is alive -- not the wider broker-held emergency stop. Sizing
  against the emergency level would systematically undersize every position
  relative to the exit that actually fires.

* **Equal-notional** (legacy, ``target_notional / price``). Retained only as
  the fallback for when live equity cannot be read, because a stale or assumed
  equity figure is worse than an honestly different sizing model.

Equity is read live from the broker at sizing time and is never hardcoded.
Phase 1 is the standing argument: the account's baseline moved from ~GBP 1.01M
to GBP 250,000 overnight without warning, and any embedded constant would have
mis-sized every position afterwards while looking perfectly healthy.
"""

from bot.currency import is_pence_instrument
from bot.logger import log

# Fraction of equity risked per position when nothing else is configured.
DEFAULT_RISK_FRACTION = 0.01          # 1%


def resolve_risk_fraction(instrument: dict, settings: dict = None) -> float:
    """Fraction of equity to risk: per-instrument override, else global."""
    if instrument.get('risk_fraction') is not None:
        return float(instrument['risk_fraction'])
    if settings and settings.get('risk_fraction') is not None:
        return float(settings['risk_fraction'])
    return DEFAULT_RISK_FRACTION


def native_price(current_price: float, currency: str) -> float:
    """Quote price converted to whole currency units (pence -> pounds)."""
    if is_pence_instrument(currency):
        return current_price / 100.0
    return current_price


def calculate_risk_qty(equity: float, risk_fraction: float,
                       current_price: float, trail_stop_pct: float,
                       currency: str = 'USD', fx_to_base: float = 1.0,
                       max_qty: int = None,
                       max_notional: float = None) -> int:
    """Fixed-fractional size against the synthetic trail-stop distance.

        risk_capital  = equity x risk_fraction          (account base ccy)
        stop_distance = price x trail_stop_pct / 100    (per share, native)
        qty           = floor(risk_capital / (stop_distance x fx_to_base))

    ``fx_to_base`` converts one unit of the instrument's currency into the
    account's base currency, so the risk budget and the loss-per-share are
    compared in the same money. It defaults to 1.0 (same currency).

    Returns **0** when even one share would exceed the risk budget. That is
    deliberate: the pre-order validation gate rejects a non-positive qty, so
    the position is simply not taken. Rounding up to 1 share would silently
    breach the very limit this function exists to enforce.

    ``max_qty`` / ``max_notional`` mirror the pre-order validation gate, so
    sizing produces an order the gate will accept rather than one it rejects.
    """
    if equity <= 0 or risk_fraction <= 0:
        return 0
    if current_price <= 0 or trail_stop_pct <= 0:
        return 0

    price = native_price(current_price, currency)
    stop_distance = price * (trail_stop_pct / 100.0)
    if stop_distance <= 0:
        return 0

    risk_capital = equity * risk_fraction
    qty = int(risk_capital / (stop_distance * fx_to_base))

    if max_qty is not None and qty > max_qty:
        qty = int(max_qty)
    if max_notional is not None and price > 0:
        affordable = int(max_notional / price)
        if qty > affordable:
            qty = affordable

    return max(0, qty)


def calculate_qty(instrument: dict, current_price: float,
                  default_target_notional: float = None,
                  equity: float = None, settings: dict = None) -> int:
    """
    Calculate position size from target notional value.

    Priority:
      1. instrument['target_notional'] (per-instrument override)
      2. default_target_notional (global setting)
      3. instrument['qty'] (fixed fallback)

    For GBP instruments, price is in pence and must be converted to
    pounds before dividing.

    Returns at least 1 share.

    When ``equity`` is supplied (read live from the broker), fixed-fractional
    risk sizing is used instead and this notional path becomes the fallback.
    """
    symbol = instrument.get('symbol', '?')
    settings = settings or {}

    if equity is not None and equity > 0:
        risk_fraction = resolve_risk_fraction(instrument, settings)
        trail_stop_pct = float(instrument.get('trail_stop_pct', 2.0))
        qty = calculate_risk_qty(
            equity, risk_fraction, current_price, trail_stop_pct,
            instrument.get('currency', 'USD'),
            fx_to_base=float(instrument.get('_fx_to_base', 1.0)),
            max_qty=settings.get('max_qty_per_order'),
            max_notional=settings.get('max_notional_per_order'),
        )
        price = native_price(current_price, instrument.get('currency', 'USD'))
        log(f"  [{symbol}] Risk sizing: equity={equity:.2f} x "
            f"{risk_fraction:.2%} / (stop {trail_stop_pct:g}% of "
            f"{price:.4f}) = qty {qty}")
        if qty <= 0:
            log(f"  [{symbol}] Risk budget too small for one share — "
                f"no position taken", "WARN")
        return qty

    log(f"  [{symbol}] No live equity available — falling back to "
        f"equal-notional sizing", "WARN")

    target = instrument.get('target_notional')
    if target is None:
        target = default_target_notional

    if target is None:
        # No target notional configured — use fixed qty
        return instrument.get('qty', 1)

    price = current_price
    currency = instrument.get('currency', 'USD')
    if is_pence_instrument(currency):
        price = price / 100.0  # pence to pounds

    if price <= 0:
        log(f"  [{instrument.get('symbol', '?')}] Price {price} <= 0, "
            f"using fixed qty {instrument.get('qty', 1)}")
        return instrument.get('qty', 1)

    qty = int(target / price)
    qty = max(1, qty)

    log(f"  [{instrument.get('symbol', '?')}] Sizing: "
        f"target=${target} / price={current_price:.4f} = qty {qty}")
    return qty
