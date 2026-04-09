"""
bot/data.py
Historical price data fetching from IBKR.
Returns pandas DataFrames with OHLCV columns.
"""

import pandas as pd
from bot.logger import log

# Timeout skip list: instruments that time out repeatedly get skipped
# Key: symbol, Value: {'consecutive_timeouts': int, 'skip_remaining': int}
_timeout_tracker: dict[str, dict] = {}

TIMEOUT_SECONDS = 15
MAX_CONSECUTIVE_TIMEOUTS = 3
SKIP_CYCLES = 10


class DataFeed:
    """
    Fetches historical OHLCV data from IBKR for a given contract.
    Returns a pandas DataFrame or None on failure.
    """

    def __init__(self, ib_conn):
        self.ib = ib_conn.ib

    def get(self, contract, days: int = 300,
            bar_size: str = '1 day') -> pd.DataFrame | None:
        """
        Fetch OHLCV bars for the given contract.

        Args:
            contract : qualified IBKR contract object
            days     : number of calendar days of history to fetch
                       (300 needed to calculate 200-period MA)
            bar_size : IBKR bar size — '1 day' or '4 hours'

        Returns:
            pd.DataFrame with columns: date, open, high, low, close, volume
            None if data unavailable or insufficient
        """
        symbol = contract.symbol

        # Check skip list
        if symbol in _timeout_tracker:
            entry = _timeout_tracker[symbol]
            if entry['skip_remaining'] > 0:
                entry['skip_remaining'] -= 1
                remaining = entry['skip_remaining']
                log(f"  [{symbol}] Skipping — timed out {MAX_CONSECUTIVE_TIMEOUTS} consecutive times, "
                    f"retry in {remaining} cycles", "WARN")
                return None

        try:
            what_to_show = 'MIDPOINT' if contract.secType == 'CFD' else 'TRADES'
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime='',
                durationStr=f'{days} D',
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=True,
                timeout=TIMEOUT_SECONDS
            )
            if not bars or len(bars) < 50:
                log(f"  Insufficient data for {symbol} ({len(bars) if bars else 0} bars)", "WARN")
                return None

            # Success — reset timeout tracker
            _timeout_tracker.pop(symbol, None)

            # Check bar freshness — last bar should be within 2 trading days
            last_bar_date = bars[-1].date
            if hasattr(last_bar_date, 'date'):
                last_bar_date = last_bar_date.date()
            days_stale = (pd.Timestamp.now().date() - pd.Timestamp(last_bar_date).date()).days
            if days_stale > 4:  # allow weekends + 2 trading days
                log(f"  Stale data for {symbol}: last bar {last_bar_date} ({days_stale}d old)", "WARN")
                return None

            return pd.DataFrame([{
                'date':   b.date,
                'open':   b.open,
                'high':   b.high,
                'low':    b.low,
                'close':  b.close,
                'volume': b.volume,
            } for b in bars])

        except (ConnectionError, OSError, TimeoutError) as e:
            self._record_timeout(symbol, e)
            return None
        except ValueError as e:
            log(f"  Data value error {symbol}: {e}", "WARN")
            return None
        except Exception as e:
            if 'timeout' in str(e).lower():
                self._record_timeout(symbol, e)
            else:
                log(f"  Data error {symbol}: {e}", "WARN")
            return None

    @staticmethod
    def _record_timeout(symbol: str, error):
        """Track consecutive timeouts; add to skip list after MAX_CONSECUTIVE_TIMEOUTS."""
        entry = _timeout_tracker.setdefault(symbol, {'consecutive_timeouts': 0, 'skip_remaining': 0})
        entry['consecutive_timeouts'] += 1
        count = entry['consecutive_timeouts']
        if count >= MAX_CONSECUTIVE_TIMEOUTS:
            entry['skip_remaining'] = SKIP_CYCLES
            entry['consecutive_timeouts'] = 0
            log(f"  [{symbol}] Timed out {MAX_CONSECUTIVE_TIMEOUTS} consecutive times — "
                f"skipping for {SKIP_CYCLES} cycles", "WARN")
        else:
            log(f"  [{symbol}] Data timeout ({count}/{MAX_CONSECUTIVE_TIMEOUTS}): {error}", "WARN")
