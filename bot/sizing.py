"""
bot/sizing.py — Risk-based position sizing.

Calculates qty from target_notional / price so every position has
roughly equal dollar exposure, regardless of share price.
"""

from bot.currency import is_pence_instrument
from bot.logger import log


def calculate_qty(instrument: dict, current_price: float,
                  default_target_notional: float = None) -> int:
    """
    Calculate position size from target notional value.

    Priority:
      1. instrument['target_notional'] (per-instrument override)
      2. default_target_notional (global setting)
      3. instrument['qty'] (fixed fallback)

    For GBP instruments, price is in pence and must be converted to
    pounds before dividing.

    Returns at least 1 share.
    """
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
