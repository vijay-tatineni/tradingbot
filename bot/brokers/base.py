"""
bot/brokers/base.py — Abstract broker interface.

Defines the contract that all broker adapters must implement.
Derived from the actual IBKR calls made by layer1, layer2, layer3,
position_tracker, dashboard, and main.py.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd


# ── Broker-agnostic data types ────────────────────────────────

@dataclass
class BrokerPosition:
    """
    A position as returned by get_all_positions().
    Used by layer1._reconcile_with_ibkr() and _close_all().
    """
    symbol: str
    qty: float
    avg_cost: float
    currency: str
    contract: Any = None  # broker-specific handle for order placement


@dataclass
class FillResult:
    """Result of an order placement attempt.

    ``stop_attached`` reports whether a broker-held protective stop is working
    for the position this fill opened. Meaningful for entries only.
    """
    success: bool = False
    fill_price: float = 0.0
    filled_qty: float = 0.0
    stop_attached: bool = False
    stop_order_id: int = 0

    def __bool__(self):
        return self.success


@dataclass
class PositionInfo:
    """Full position data for one instrument."""
    symbol: str
    qty: float
    avg_cost: float
    currency: str
    price: float = 0.0
    unreal_pnl: float = 0.0
    pnl_pct: float = 0.0


# ── Abstract broker interface ─────────────────────────────────

class BaseBroker(ABC):
    """
    Abstract interface for all broker adapters.

    Every method here corresponds to an actual broker call made somewhere
    in the bot's codebase (layers, main.py, etc.). A new broker adapter
    implements this interface and the bot works without changes.
    """

    # ── Connection ────────────────────────────────────────────

    @abstractmethod
    def connect(self) -> None:
        """Connect to the broker. Retry on failure."""

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect gracefully."""

    @abstractmethod
    def reconnect(self) -> None:
        """Reconnect after a dropped connection."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connection is alive."""

    @abstractmethod
    def sleep(self, seconds: float) -> None:
        """
        Sleep while keeping the connection alive.
        IBKR needs ib.sleep() to process events; other brokers
        may use time.sleep() or their own event loop.
        """

    @abstractmethod
    def qualify_contracts(self, instruments: list[dict]) -> list[dict]:
        """
        Validate and qualify contract specifications.
        Returns only successfully qualified instruments with a
        broker-specific 'contract' field added to each dict.
        """

    # ── Market Data ───────────────────────────────────────────

    @abstractmethod
    def fetch_bars(self, contract, days: int = 300,
                   bar_size: str = '1 day') -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV bars for the given contract.

        Args:
            contract: qualified broker contract handle
            days: calendar days of history (300 needed for MA200)
            bar_size: '1 day', '4 hours', etc.

        Returns:
            DataFrame with columns: date, open, high, low, close, volume
            None if data unavailable or insufficient
        """

    @abstractmethod
    def fetch_price_snapshot(self, contract) -> Optional[float]:
        """
        Get the latest price for a contract (1-minute snapshot).
        Used by Layer 3 silver scalper for intraday pricing.
        Returns the last close price, or None on failure.
        """

    # ── Order Execution ───────────────────────────────────────

    @abstractmethod
    def place_order(self, contract, action: str, qty: float,
                    name: str, stop_price: float = None) -> FillResult:
        """
        Place a market order and wait for fill confirmation.

        Args:
            contract: qualified broker contract handle
            action: 'BUY' or 'SELL'
            qty: number of shares/contracts
            name: instrument display name (for logging)
            stop_price: when given, submit the entry with a broker-attached
                protective stop at this level, atomically. Implementations must
                guarantee the spec invariant that no unprotected position
                persists: if the entry fills but the stop cannot be made to
                work after one retry, flatten the position and alert.

        Returns:
            FillResult with fill details
        """

    @abstractmethod
    def close_position(self, inst: dict, position: float) -> FillResult:
        """
        Close an existing position.

        Args:
            inst: instrument dict (must have 'contract' and 'name')
            position: current position size (sign determines direction)

        Returns:
            FillResult with fill details
        """

    @abstractmethod
    def handle_signal(self, inst: dict, signal: int, confidence: str,
                      position: float,
                      stop_price: float = None) -> tuple[str, FillResult]:
        """
        Execute a trade based on signal and current position.
        Respects long_only constraint from instrument config.

        Args:
            inst: instrument dict with contract, qty, name, long_only
            signal: 1 (BUY), -1 (SELL), 0 (HOLD)
            confidence: 'HIGH', 'MEDIUM', 'LOW'
            position: current position size

        Returns:
            (action_string, FillResult) tuple
        """

    def set_alerts(self, alerts) -> None:
        """Wire up alert handler (e.g. Telegram) for order failures."""

    # ── Broker-held protective stops ──────────────────────────
    #
    # Concrete no-op defaults rather than abstract methods: a broker that
    # cannot introspect its working orders still satisfies the interface, and
    # the startup check degrades to "cannot verify" instead of breaking. Both
    # are overridden where the broker does support it.

    def get_account_equity(self):
        """Live account equity in the account's base currency, or None.

        None means "could not read", never "assume a number": risk sizing must
        fall back to a visibly different model rather than size against a
        stale or invented equity figure.
        """
        return None

    def working_stop_symbols(self) -> set:
        """Symbols that currently have a live broker-held protective stop."""
        return set()

    def supports_stop_introspection(self) -> bool:
        """Whether working_stop_symbols() reflects reality for this broker.

        False means the startup check must report "unverifiable" rather than
        concluding that every position is unprotected.
        """
        return False

    def cancel_stops_for_symbol(self, symbol: str) -> int:
        """Cancel working protective stops for a symbol; return the count."""
        return 0

    # ── Portfolio ─────────────────────────────────────────────

    @abstractmethod
    def get_position(self, symbol: str) -> float:
        """Return current position size for a symbol (0 if none)."""

    @abstractmethod
    def get_position_info(self, symbol: str,
                          current_price: float = 0) -> PositionInfo:
        """Return full position info including avg cost and P&L."""

    @abstractmethod
    def get_total_pnl(self) -> float:
        """Return total unrealised P&L across all positions (in USD)."""

    @abstractmethod
    def get_all_positions(self) -> list[BrokerPosition]:
        """
        Return all open positions as BrokerPosition objects.
        Used by layer1._reconcile_with_ibkr() and _close_all().
        """

    @abstractmethod
    def get_all_position_info(self) -> list[PositionInfo]:
        """Return all open positions as PositionInfo objects (for dashboard)."""

    @abstractmethod
    def is_emergency_stop(self, total_pnl: float) -> bool:
        """Return True if portfolio loss limit has been hit."""
