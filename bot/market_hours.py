"""
bot/market_hours.py
Market hours logic — supports LSE, US SMART, and 24/7 CFDs.
Uses pytz for proper DST handling (BST/GMT for LSE, EDT/EST for US).
"""

import datetime
import pytz
import holidays


class MarketHours:
    """
    Determines whether a market is currently open.
    Uses actual exchange timezones so DST shifts are handled automatically.

    LSE: 08:00-16:30 Europe/London  (handles GMT/BST)
    US:  09:30-16:00 America/New_York (handles EST/EDT)
    EUR: 09:00-17:30 Europe/Paris   (handles CET/CEST)
    """

    LONDON_TZ   = pytz.timezone('Europe/London')
    NEW_YORK_TZ = pytz.timezone('America/New_York')

    # LSE hours in London local time
    LSE_OPEN  = (8, 0)
    LSE_CLOSE = (16, 30)

    # US hours in New York local time
    US_OPEN  = (9, 30)
    US_CLOSE = (16, 0)

    # EUR hours in CET (Europe/Paris) local time — XETRA/Euronext 09:00-17:30
    PARIS_TZ  = pytz.timezone('Europe/Paris')
    EUR_OPEN  = (9, 0)
    EUR_CLOSE = (17, 30)

    # Holiday calendars
    UK_HOLIDAYS = holidays.UK()
    US_HOLIDAYS = holidays.US()
    FR_HOLIDAYS = holidays.France()

    def __init__(self):
        self._holiday_name: str = ''

    def is_holiday(self, inst: dict) -> tuple[bool, str]:
        """Check if today is a holiday for this instrument's market.

        Returns (is_holiday, holiday_name) using the local date for
        the relevant market timezone.
        """
        now_utc = datetime.datetime.now(pytz.utc)
        market   = inst.get('market', '')
        currency = inst.get('currency', 'USD')

        if market == 'LSE' or currency == 'GBP':
            local_date = now_utc.astimezone(self.LONDON_TZ).date()
            name = self.UK_HOLIDAYS.get(local_date, '')
            return (bool(name), name)

        if currency == 'EUR':
            local_date = now_utc.astimezone(self.PARIS_TZ).date()
            name = self.FR_HOLIDAYS.get(local_date, '')
            return (bool(name), name)

        # Default: US
        local_date = now_utc.astimezone(self.NEW_YORK_TZ).date()
        name = self.US_HOLIDAYS.get(local_date, '')
        return (bool(name), name)

    def is_open(self, inst: dict) -> bool:
        """Return True if this instrument's market is currently open."""
        if inst['sec_type'] == 'CFD':
            return True

        now_utc = datetime.datetime.now(pytz.utc)
        weekday = now_utc.weekday()
        if weekday >= 5:   # Saturday=5, Sunday=6
            return False

        market   = inst.get('market', '')
        currency = inst.get('currency', 'USD')

        # Holiday check using local dates for each market
        if market == 'LSE' or currency == 'GBP':
            local_date = now_utc.astimezone(self.LONDON_TZ).date()
            hol_name = self.UK_HOLIDAYS.get(local_date, '')
            if hol_name:
                self._holiday_name = hol_name
                return False
            london = now_utc.astimezone(self.LONDON_TZ)
            h, m = london.hour, london.minute
            return self.LSE_OPEN <= (h, m) < self.LSE_CLOSE

        if currency == 'EUR':
            local_date = now_utc.astimezone(self.PARIS_TZ).date()
            hol_name = self.FR_HOLIDAYS.get(local_date, '')
            if hol_name:
                self._holiday_name = hol_name
                return False
            paris = now_utc.astimezone(self.PARIS_TZ)
            h, m = paris.hour, paris.minute
            return self.EUR_OPEN <= (h, m) < self.EUR_CLOSE

        # Default: US SMART hours in New York time
        local_date = now_utc.astimezone(self.NEW_YORK_TZ).date()
        hol_name = self.US_HOLIDAYS.get(local_date, '')
        if hol_name:
            self._holiday_name = hol_name
            return False
        ny = now_utc.astimezone(self.NEW_YORK_TZ)
        h, m = ny.hour, ny.minute
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
