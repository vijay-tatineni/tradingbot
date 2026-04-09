"""
bot/brokers/ibkr.py — IBKR broker adapter.

Wraps the existing IBKR modules (connection.py, data.py, orders.py,
portfolio.py) into the BaseBroker interface. Delegates all logic to
the existing battle-tested code — no reimplementation.
"""

from typing import Optional

import pandas as pd

from bot.brokers.base import BaseBroker, BrokerPosition, FillResult, PositionInfo
from bot.config import Config
from bot.connection import IBConnection
from bot.data import DataFeed
from bot.orders import OrderManager, FillResult as IBKRFillResult
from bot.portfolio import Portfolio, PositionInfo as IBKRPositionInfo
from bot.logger import log


class IBKRBroker(BaseBroker):
    """
    IBKR broker adapter. Wraps existing modules by delegation.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._conn = IBConnection(cfg)
        self._feed = DataFeed(self._conn)
        self._orders = OrderManager(self._conn, cfg)
        self._portfolio = Portfolio(self._conn, cfg)

    # ── Connection ────────────────────────────────────────────

    def connect(self) -> None:
        self._conn.connect()

    def disconnect(self) -> None:
        self._conn.ib.disconnect()

    def reconnect(self) -> None:
        self._conn.reconnect()

    def is_connected(self) -> bool:
        return self._conn.ib.isConnected()

    def sleep(self, seconds: float) -> None:
        self._conn.sleep(seconds)

    def qualify_contracts(self, instruments: list[dict]) -> list[dict]:
        return self._conn.qualify_contracts(instruments)

    # ── Market Data ───────────────────────────────────────────

    def fetch_bars(self, contract, days: int = 300,
                   bar_size: str = '1 day') -> Optional[pd.DataFrame]:
        return self._feed.get(contract, days=days, bar_size=bar_size)

    def fetch_price_snapshot(self, contract) -> Optional[float]:
        """1-minute price snapshot (used by Layer 3 silver scalper)."""
        try:
            bars = self._conn.ib.reqHistoricalData(
                contract,
                endDateTime='',
                durationStr='120 S',
                barSizeSetting='1 min',
                whatToShow='TRADES',
                useRTH=True,
                timeout=15,
            )
            if bars:
                return bars[-1].close
            return None
        except Exception as e:
            log(f"  Price snapshot error: {e}", "WARN")
            return None

    # ── Order Execution ───────────────────────────────────────

    def place_order(self, contract, action: str, qty: float,
                    name: str) -> FillResult:
        result = self._orders.place(contract, action, qty, name)
        return self._convert_fill(result)

    def close_position(self, inst: dict, position: float) -> FillResult:
        result = self._orders.close(inst, position)
        return self._convert_fill(result)

    def handle_signal(self, inst: dict, signal: int,
                      confidence: str, position: float) -> tuple[str, FillResult]:
        action, result = self._orders.handle_signal(
            inst, signal, confidence, position)
        return action, self._convert_fill(result)

    def set_alerts(self, alerts) -> None:
        self._orders.alerts = alerts

    # ── Portfolio ─────────────────────────────────────────────

    def get_position(self, symbol: str) -> float:
        return self._portfolio.get_position(symbol)

    def get_position_info(self, symbol: str,
                          current_price: float = 0) -> PositionInfo:
        info = self._portfolio.get_position_info(symbol, current_price)
        return self._convert_position_info(info)

    def get_total_pnl(self) -> float:
        return self._portfolio.get_total_pnl()

    def get_all_positions(self) -> list[BrokerPosition]:
        raw_positions = self._portfolio.get_all_positions()
        return [
            BrokerPosition(
                symbol=p.contract.symbol,
                qty=p.position,
                avg_cost=p.avgCost,
                currency=getattr(p.contract, 'currency', 'USD'),
                contract=p.contract,
            )
            for p in raw_positions
        ]

    def get_all_position_info(self) -> list[PositionInfo]:
        infos = self._portfolio.get_all_position_info()
        return [self._convert_position_info(info) for info in infos]

    def is_emergency_stop(self, total_pnl: float) -> bool:
        return self._portfolio.is_emergency_stop(total_pnl)

    # ── Conversion helpers ────────────────────────────────────

    @staticmethod
    def _convert_fill(ibkr_result: IBKRFillResult) -> FillResult:
        """Convert IBKR FillResult to broker-agnostic FillResult."""
        return FillResult(
            success=ibkr_result.success,
            fill_price=ibkr_result.fill_price,
            filled_qty=ibkr_result.filled_qty,
        )

    @staticmethod
    def _convert_position_info(ibkr_info: IBKRPositionInfo) -> PositionInfo:
        """Convert IBKR PositionInfo to broker-agnostic PositionInfo."""
        return PositionInfo(
            symbol=ibkr_info.symbol,
            qty=ibkr_info.qty,
            avg_cost=ibkr_info.avg_cost,
            currency=ibkr_info.currency,
            price=ibkr_info.price,
            unreal_pnl=ibkr_info.unreal_pnl,
            pnl_pct=ibkr_info.pnl_pct,
        )
