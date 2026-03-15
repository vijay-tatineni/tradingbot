"""
bot/data.py
Historical price data fetching from IBKR.
Returns pandas DataFrames with OHLCV columns.
"""

import pandas as pd
from bot.logger import log


class DataFeed:
    """
    Fetches historical OHLCV data from IBKR for a given contract.
    Returns a pandas DataFrame or None on failure.
    """

    def __init__(self, ib_conn):
        self.ib = ib_conn.ib

    def get(self, contract, days: int = 300) -> pd.DataFrame | None:
        """
        Fetch daily OHLCV bars for the given contract.

        Args:
            contract : qualified IBKR contract object
            days     : number of calendar days of history to fetch
                       (300 needed to calculate 200-period MA)

        Returns:
            pd.DataFrame with columns: date, open, high, low, close, volume
            None if data unavailable or insufficient
        """
        try:
            what_to_show = 'MIDPOINT' if contract.secType == 'CFD' else 'TRADES'
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime='',
                durationStr=f'{days} D',
                barSizeSetting='1 day',
                whatToShow=what_to_show,
                useRTH=True
            )
            if not bars or len(bars) < 50:
                log(f"  Insufficient data for {contract.symbol} ({len(bars) if bars else 0} bars)", "WARN")
                return None

            # Check bar freshness — last bar should be within 2 trading days
            last_bar_date = bars[-1].date
            if hasattr(last_bar_date, 'date'):
                last_bar_date = last_bar_date.date()
            days_stale = (pd.Timestamp.now().date() - pd.Timestamp(last_bar_date).date()).days
            if days_stale > 4:  # allow weekends + 2 trading days
                log(f"  Stale data for {contract.symbol}: last bar {last_bar_date} ({days_stale}d old)", "WARN")
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
            log(f"  Data connection error {contract.symbol}: {e}", "WARN")
            return None
        except ValueError as e:
            log(f"  Data value error {contract.symbol}: {e}", "WARN")
            return None
        except Exception as e:
            log(f"  Data error {contract.symbol}: {e}", "WARN")
            return None
