"""
bot/bar_schedule.py — Bar-close boundary detection.

Determines whether a bar just closed based on actual IBKR bar boundaries
observed in backtest.db. Used by layer1 to implement two-tier stop evaluation:
- Tier 1 (every cycle): Emergency hard stop
- Tier 2 (bar close only): Trailing stop + take profit

Actual 4hr bar START timestamps from IBKR (useRTH=True):
  LSE:  09:00, 13:00, 17:00 London time  -> bars close at 13:00, 17:00, ~16:30
  EUR:  09:00, 13:00, 17:00 CET          -> bars close at 13:00, 17:00, ~17:30
  US:   09:30, 12:00 Eastern             -> bars close at 12:00, 16:00

Daily bars close at market close for each exchange.
"""

import datetime
import pytz

LONDON_TZ = pytz.timezone('Europe/London')
NEW_YORK_TZ = pytz.timezone('America/New_York')
PARIS_TZ = pytz.timezone('Europe/Paris')

# 4hr bar CLOSE times in local exchange time (hour, minute)
# Derived from actual IBKR bar timestamps in backtest.db
LSE_4HR_CLOSES = [(13, 0), (17, 0)]
EUR_4HR_CLOSES = [(13, 0), (17, 0)]
US_4HR_CLOSES = [(12, 0), (16, 0)]

# Daily bar close = market close
LSE_DAILY_CLOSE = (16, 30)
EUR_DAILY_CLOSE = (17, 30)
US_DAILY_CLOSE = (16, 0)

# Window in minutes after a bar close during which is_bar_close returns True.
# Must be >= cycle interval (1 min) to guarantee we catch it.
WINDOW_MINUTES = 5


def _minutes_since_boundary(now_local, hour, minute):
    """Return minutes elapsed since the given (hour, minute) today, or None if in the future."""
    boundary = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta = (now_local - boundary).total_seconds()
    if delta < 0:
        return None
    return delta / 60


def is_bar_close(timeframe: str, inst: dict) -> bool:
    """
    Check if we're within WINDOW_MINUTES after a bar close boundary.

    Args:
        timeframe: '4hr' or 'daily'
        inst: instrument dict with 'market', 'currency' keys

    Returns:
        True if a bar just closed (within the detection window).
    """
    now_utc = datetime.datetime.now(pytz.utc)
    market = inst.get('market', '')
    currency = inst.get('currency', 'USD')

    if market == 'LSE' or currency == 'GBP':
        return _check_boundaries(now_utc, LONDON_TZ, timeframe,
                                 LSE_4HR_CLOSES, LSE_DAILY_CLOSE)
    elif currency == 'EUR':
        return _check_boundaries(now_utc, PARIS_TZ, timeframe,
                                 EUR_4HR_CLOSES, EUR_DAILY_CLOSE)
    else:
        # US / default
        return _check_boundaries(now_utc, NEW_YORK_TZ, timeframe,
                                 US_4HR_CLOSES, US_DAILY_CLOSE)


def next_bar_close_str(timeframe: str, inst: dict) -> str:
    """Return a human-readable string of the next bar close time (for logging)."""
    now_utc = datetime.datetime.now(pytz.utc)
    market = inst.get('market', '')
    currency = inst.get('currency', 'USD')

    if market == 'LSE' or currency == 'GBP':
        tz = LONDON_TZ
        closes = LSE_4HR_CLOSES if timeframe == '4hr' else [LSE_DAILY_CLOSE]
    elif currency == 'EUR':
        tz = PARIS_TZ
        closes = EUR_4HR_CLOSES if timeframe == '4hr' else [EUR_DAILY_CLOSE]
    else:
        tz = NEW_YORK_TZ
        closes = US_4HR_CLOSES if timeframe == '4hr' else [US_DAILY_CLOSE]

    now_local = now_utc.astimezone(tz)
    for h, m in sorted(closes):
        boundary = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
        if boundary > now_local:
            return boundary.strftime('%H:%M %Z')

    # All boundaries passed today — next is first boundary tomorrow
    h, m = sorted(closes)[0]
    return f"{h:02d}:{m:02d} (tomorrow)"


def _check_boundaries(now_utc, tz, timeframe, four_hr_closes, daily_close):
    """Check if current time is within WINDOW_MINUTES after any bar close boundary."""
    now_local = now_utc.astimezone(tz)

    if timeframe == 'daily':
        boundaries = [daily_close]
    else:
        boundaries = four_hr_closes

    for h, m in boundaries:
        mins = _minutes_since_boundary(now_local, h, m)
        if mins is not None and mins <= WINDOW_MINUTES:
            return True

    return False
