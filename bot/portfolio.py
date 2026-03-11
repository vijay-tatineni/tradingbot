"""
bot/portfolio.py
Portfolio state — open positions, average costs, unrealised P&L.
All position data comes from IBKR, not calculated locally.
"""

from dataclasses import dataclass
from typing import Optional
from bot.logger import log


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
                    avg_cost = round(p.avgCost * 100, 4) if currency == 'GBP' else round(p.avgCost, 4)
                    unreal   = round((current_price - avg_cost) * abs(qty), 2) if avg_cost > 0 and current_price > 0 else 0
                    pct      = round(((current_price - avg_cost) / avg_cost) * 100, 2) if avg_cost > 0 else 0
                    return PositionInfo(
                        symbol=symbol, qty=qty, avg_cost=avg_cost,
                        currency=currency, price=current_price,
                        unreal_pnl=unreal, pnl_pct=pct
                    )
        except Exception as e:
            log(f"  Position info error {symbol}: {e}", "WARN")

        return PositionInfo(symbol=symbol, qty=0, avg_cost=0, currency='USD')

    def get_total_pnl(self) -> float:
        """Return total unrealised P&L across all positions."""
        try:
            for v in self.ib.accountValues(self.cfg.account):
                if v.tag == 'UnrealizedPnL' and v.currency == 'USD':
                    return float(v.value)
        except Exception as e:
            log(f"  P&L error: {e}", "WARN")
        return 0.0

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
