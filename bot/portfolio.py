"""
bot/portfolio.py
Portfolio state — open positions, average costs, unrealised P&L.
All position data comes from IBKR, not calculated locally.
"""

from dataclasses import dataclass
from typing import Optional
from bot.logger import log
from bot.currency import is_pence_instrument, pounds_to_pence, convert_pnl_to_base


@dataclass
class PositionInfo:
    """Full position data for one instrument."""
    symbol:      str
    qty:         float
    avg_cost:    float
    currency:    str
    price:       float   = 0.0
    unreal_pnl:  float   = 0.0  # (current_price - avg_cost) * qty
    pnl_pct:     float   = 0.0  # % change from avg cost


class Portfolio:
    """
    Reads live portfolio data from IBKR.
    Provides position lookups and P&L calculations.
    """

    def __init__(self, ib_conn, cfg):
        self.ib      = ib_conn.ib
        self.cfg     = cfg

    def get_position(self, symbol: str) -> float:
        """Return current position size for a symbol (0 if none)."""
        try:
            for p in self.ib.positions(self.cfg.account):
                if p.contract.symbol == symbol:
                    return p.position
        except (ConnectionError, OSError) as e:
            log(f"  Position connection error {symbol}: {e}", "WARN")
        except Exception as e:
            log(f"  Position error {symbol}: {e}", "WARN")
        return 0

    def get_position_info(self, symbol: str, current_price: float = 0) -> PositionInfo:
        """
        Return full position info including avg cost and P&L.
        This is what populates the Entry Price and P&L columns on the dashboard.
        """
        try:
            for p in self.ib.positions(self.cfg.account):
                if p.contract.symbol == symbol:
                    qty      = p.position
                    currency = getattr(p.contract, 'currency', 'USD')
                    # IBKR returns avgCost in GBP pounds; LSE prices in pence
                    avg_cost = round(pounds_to_pence(p.avgCost), 4) if is_pence_instrument(currency) else round(p.avgCost, 4)
                    # Correct P&L for both long and short positions
                    if avg_cost > 0 and current_price > 0:
                        if qty > 0:  # long
                            unreal = round((current_price - avg_cost) * qty, 2)
                            pct    = round(((current_price - avg_cost) / avg_cost) * 100, 2)
                        elif qty < 0:  # short
                            unreal = round((avg_cost - current_price) * abs(qty), 2)
                            pct    = round(((avg_cost - current_price) / avg_cost) * 100, 2)
                        else:
                            unreal, pct = 0, 0
                        # GBP: avg_cost and price are both in pence, so P&L is
                        # in pence too — convert to pounds for display
                        if is_pence_instrument(currency):
                            unreal = round(convert_pnl_to_base(unreal, currency), 2)
                    else:
                        unreal, pct = 0, 0
                    return PositionInfo(
                        symbol=symbol, qty=qty, avg_cost=avg_cost,
                        currency=currency, price=current_price,
                        unreal_pnl=unreal, pnl_pct=pct
                    )
        except Exception as e:
            log(f"  Position info error {symbol}: {e}", "WARN")

        return PositionInfo(symbol=symbol, qty=0, avg_cost=0, currency='USD')

    def get_total_pnl(self) -> float:
        """
        Return total unrealised P&L across all positions in all currencies.
        Converts GBP and EUR to USD using live IBKR FX rates.
        Falls back to hardcoded rates if FX lookup fails.
        Unmanaged positions (e.g. XAUUSD) are included in the total.
        """
        try:
            pnl_by_currency = {}
            for v in self.ib.accountValues(self.cfg.account):
                if v.tag == 'UnrealizedPnL' and v.currency:
                    pnl_by_currency[v.currency] = float(v.value)

            if not pnl_by_currency:
                return 0.0

            usd_total = pnl_by_currency.get('USD', 0.0)

            # Convert non-USD P&L to USD
            for currency, pnl in pnl_by_currency.items():
                if currency == 'USD' or currency == 'BASE' or pnl == 0:
                    continue
                fx_rate = self._get_fx_rate(currency)
                usd_total += pnl * fx_rate

            # Note: unmanaged positions (e.g. XAUUSD) are included in
            # account-level P&L but excluded from reconciliation and
            # emergency stop close-all.  We skip market data requests
            # for them to avoid IBKR error 321 on CFDs without data.

            return round(usd_total, 2)
        except Exception as e:
            log(f"  P&L error: {e}", "WARN")
        return 0.0

    def _get_fx_rate(self, currency: str) -> float:
        """Get FX rate to convert currency to USD. Returns 1.0 for USD."""
        if currency == 'USD':
            return 1.0
        # Fallback rates (approximate) if IBKR lookup fails
        fallback = {'GBP': 1.27, 'EUR': 1.08, 'CHF': 1.12, 'JPY': 0.0067}
        try:
            for v in self.ib.accountValues(self.cfg.account):
                # IBKR provides ExchangeRate tag per currency
                if v.tag == 'ExchangeRate' and v.currency == currency:
                    rate = float(v.value)
                    if rate > 0:
                        return rate
        except Exception:
            pass
        return fallback.get(currency, 1.0)

    def get_all_positions(self) -> list:
        """Return all open positions as raw IBKR position objects."""
        try:
            return self.ib.positions(self.cfg.account)
        except Exception:
            return []

    def get_all_position_info(self) -> list[PositionInfo]:
        """Return all open positions as PositionInfo objects for the dashboard."""
        result = []
        try:
            for p in self.ib.positions(self.cfg.account):
                currency = getattr(p.contract, 'currency', 'USD')
                avg_cost = round(p.avgCost * 100, 4) if currency == 'GBP' else round(p.avgCost, 4)
                result.append(PositionInfo(
                    symbol   = p.contract.symbol,
                    qty      = p.position,
                    avg_cost = avg_cost,
                    currency = currency,
                ))
        except Exception as e:
            log(f"  All positions error: {e}", "WARN")
        return result

    def is_emergency_stop(self, total_pnl: float) -> bool:
        """Return True if portfolio loss limit has been hit."""
        return total_pnl < -self.cfg.portfolio_loss_limit
