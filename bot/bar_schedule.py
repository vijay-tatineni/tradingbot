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


def _minutes_until_boundary(now_local, hour, minute):
    """Return minutes remaining until (hour, minute) today, or None if passed."""
    boundary = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta = (boundary - now_local).total_seconds()
    if delta < 0:
        return None
    return delta / 60


def exchange_schedule(inst: dict):
    """(tz, 4hr close times, daily close time) for an instrument's exchange."""
    market = inst.get('market', '')
    currency = inst.get('currency', 'USD')
    if market == 'LSE' or currency == 'GBP':
        return LONDON_TZ, LSE_4HR_CLOSES, LSE_DAILY_CLOSE
    if currency == 'EUR':
        return PARIS_TZ, EUR_4HR_CLOSES, EUR_DAILY_CLOSE
    return NEW_YORK_TZ, US_4HR_CLOSES, US_DAILY_CLOSE


def has_unreachable_4hr_boundary(inst: dict) -> bool:
    """True when a 4hr bar closes at or after this exchange's market close.

    US:  4hr closes 12:00 / 16:00, market closes 16:00 -> 16:00 unreachable.
    LSE: 4hr closes 13:00 / 17:00, market closes 16:30 -> 17:00 unreachable
         (a full 30 minutes after trading has ended).
    EUR: 4hr closes 13:00 / 17:00, market closes 17:30 -> both reachable.
    """
    _tz, four_hr_closes, daily_close = exchange_schedule(inst)
    return any(boundary >= daily_close for boundary in four_hr_closes)


def is_bar_close(timeframe: str, inst: dict, now_utc=None) -> bool:
    """
    Check if we're within WINDOW_MINUTES after a bar close boundary.

    Args:
        timeframe: '4hr' or 'daily'
        inst: instrument dict with 'market', 'currency' keys
        now_utc: injectable clock for testing; defaults to now

    Returns:
        True if a bar just closed (within the detection window).
    """
    now_utc = now_utc or datetime.datetime.now(pytz.utc)
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


def should_evaluate_tier2(timeframe: str, inst: dict, now_utc=None) -> bool:
    """Whether Tier-2 (trailing stop + take profit) should evaluate this cycle.

    **Daily-timeframe instruments evaluate every cycle.** Not a preference --
    the bar-close window is unreachable for them:

      * ``is_bar_close('daily', ...)`` is true only for WINDOW_MINUTES after the
        daily close, which *is* the market close (US 16:00 ET, LSE 16:30).
      * ``layer1._process_instrument`` returns early when the market is closed,
        so a cycle never runs during that window.

    The two conditions never overlap, so Tier-2 for daily names evaluated
    *never* -- trailing stops and take-profits were dead code for every
    daily-timeframe instrument. Evaluating each cycle turns an unbounded dead
    band into one cycle interval.

    4hr instruments keep the post-close window for boundaries that fall inside
    market hours (US 12:00, LSE 13:00). For a boundary at or after the market
    close -- US 16:00, LSE 17:00 -- the post-close window is unreachable for
    exactly the same reason, so Tier-2 evaluates in the window immediately
    *before* the close instead: the last moment the bar is still observable.
    Without that, US and LSE 4hr names got one evaluation per day rather than
    two, and the final bar of every session was never acted on.
    """
    if timeframe == 'daily':
        return True
    if is_bar_close(timeframe, inst, now_utc):
        return True

    # Pre-close fallback for a 4hr boundary that the post-close window can
    # never observe.
    if not has_unreachable_4hr_boundary(inst):
        return False

    tz, _four_hr_closes, daily_close = exchange_schedule(inst)
    now_local = (now_utc or datetime.datetime.now(pytz.utc)).astimezone(tz)
    remaining = _minutes_until_boundary(now_local, *daily_close)
    return remaining is not None and 0 < remaining <= WINDOW_MINUTES


def next_bar_close_str(timeframe: str, inst: dict, now_utc=None) -> str:
    """Return a human-readable string of the next bar close time (for logging)."""
    now_utc = now_utc or datetime.datetime.now(pytz.utc)
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
