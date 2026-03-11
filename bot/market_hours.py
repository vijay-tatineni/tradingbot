"""
bot/market_hours.py
Market hours logic — supports LSE, US SMART, and 24/7 CFDs.
Uses 'market' field from instruments.json if present,
falls back to currency (GBP = LSE hours, USD = US hours).
"""

import datetime


class MarketHours:
    """
    Determines whether a market is currently open.

    Priority for hours lookup:
      1. inst['market'] field  (explicit — set 'LSE' for UK stocks)
      2. inst['currency']      (GBP = LSE, USD = US SMART)
      3. inst['sec_type']      (CFD = always open)
    """

    LSE_OPEN   = (8,  0)
    LSE_CLOSE  = (16, 30)
    US_OPEN    = (14, 30)
    US_CLOSE   = (21, 0)

    def is_open(self, inst: dict) -> bool:
        """Return True if this instrument's market is currently open."""
        if inst['sec_type'] == 'CFD':
            return True

        now     = datetime.datetime.now(datetime.timezone.utc)
        weekday = now.weekday()
        if weekday >= 5:   # Saturday=5, Sunday=6
            return False

        h, m = now.hour, now.minute
        market   = inst.get('market', '')
        currency = inst.get('currency', 'USD')

        if market == 'LSE' or currency == 'GBP':
            return self.LSE_OPEN <= (h, m) < self.LSE_CLOSE

        # Default: US SMART hours
        return self.US_OPEN <= (h, m) < self.US_CLOSE

    def status(self, inst: dict) -> str:
        """Return human-readable market status string."""
        if inst['sec_type'] == 'CFD':
            return '24/7'
        return 'OPEN' if self.is_open(inst) else 'CLOSED'

    def lse_open(self) -> bool:
        """Is LSE currently open?"""
        return self.is_open({'sec_type': 'STK', 'currency': 'GBP', 'market': 'LSE'})

    def us_open(self) -> bool:
        """Is US market currently open?"""
        return self.is_open({'sec_type': 'STK', 'currency': 'USD'})
