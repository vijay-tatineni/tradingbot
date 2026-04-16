"""
Pre-order validation gate.

This is the LAST line of defence before money leaves the account.
Every order must pass ALL checks or it is rejected with a log message.
No exceptions. No overrides.
"""
import logging
from bot.currency import is_pence_instrument

logger = logging.getLogger("order_validator")


class OrderValidationError(Exception):
    """Raised when an order fails validation."""
    pass


def validate_order(symbol: str, qty: int, price: float, direction: str,
                   currency: str, settings: dict, open_positions: int,
                   daily_pnl: float, weekly_pnl: float) -> None:
    """
    Validate an order before placement. Raises OrderValidationError
    if any check fails.

    This function should be called in layer1.py immediately before
    broker.place_order(). If it raises, the order is NOT placed.
    """
    errors = []

    # 1. Qty must be positive and within limits
    max_qty = settings.get("max_qty_per_order", 500)
    if qty <= 0:
        errors.append(f"qty must be positive, got {qty}")
    if qty > max_qty:
        errors.append(f"qty {qty} exceeds max_qty {max_qty}")

    # 2. Notional value check
    notional_price = price / 100 if is_pence_instrument(currency) else price
    notional = qty * notional_price
    max_notional = settings.get("max_notional_per_order", 5000)
    if notional > max_notional:
        errors.append(f"notional ${notional:.0f} exceeds max ${max_notional}")

    # 3. Price must be positive
    if price <= 0:
        errors.append(f"price must be positive, got {price}")

    # 4. Portfolio position limit
    max_positions = settings.get("max_open_positions", 5)
    if open_positions >= max_positions:
        errors.append(f"already at max positions ({open_positions}/{max_positions})")

    # 5. Daily loss limit
    daily_limit = settings.get("daily_loss_limit", 500)
    if daily_pnl <= -daily_limit:
        errors.append(f"daily loss limit reached (${daily_pnl:.2f} / -${daily_limit})")

    # 6. Weekly loss limit
    weekly_limit = settings.get("weekly_loss_limit", 1500)
    if weekly_pnl <= -weekly_limit:
        errors.append(f"weekly loss limit reached (${weekly_pnl:.2f} / -${weekly_limit})")

    if errors:
        msg = f"[{symbol}] ORDER REJECTED: {'; '.join(errors)}"
        logger.error(msg)
        raise OrderValidationError(msg)

    # Log successful validation
    logger.info(f"[{symbol}] Order validated: {direction} {qty} @ {price} "
                f"(notional: ${notional:.0f}, positions: {open_positions}/{max_positions})")
