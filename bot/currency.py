"""
Currency conversion utilities.

GBP instruments on IBKR and IG report prices in pence (1/100 of a pound).
All internal P&L calculations should be in pounds/dollars, not pence.
"""

# Instruments that quote in pence (GBP)
GBP_PENCE_CURRENCIES = {"GBP"}


def is_pence_instrument(currency: str) -> bool:
    """Check if this instrument quotes in pence."""
    return currency.upper() in GBP_PENCE_CURRENCIES


def pence_to_pounds(value: float) -> float:
    """Convert pence to pounds. 1560p -> £15.60"""
    return value / 100.0


def pounds_to_pence(value: float) -> float:
    """Convert pounds to pence. £15.60 -> 1560p"""
    return value * 100.0


def convert_pnl_to_base(pnl_raw: float, currency: str) -> float:
    """
    Convert raw P&L to base currency.
    For GBP instruments: divides by 100 (pence -> pounds).
    For everything else: returns as-is.
    """
    if is_pence_instrument(currency):
        return pence_to_pounds(pnl_raw)
    return pnl_raw
