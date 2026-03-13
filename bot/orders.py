"""
bot/orders.py
Order management — place and close positions via IBKR.
All orders are market orders. Handles long-only constraint.

Returns (success, fill_price, filled_qty) tuples for full fill tracking.
Cancels timed-out orders explicitly. Rejects partial fills.
"""

from ib_insync import Order
from bot.logger import log


class FillResult:
    """Result of an order placement attempt."""
    __slots__ = ('success', 'fill_price', 'filled_qty')

    def __init__(self, success: bool = False, fill_price: float = 0.0,
                 filled_qty: float = 0.0):
        self.success    = success
        self.fill_price = fill_price
        self.filled_qty = filled_qty

    def __bool__(self):
        return self.success


class OrderManager:
    """
    Places and closes orders via IBKR.
    Respects long_only constraint from instruments.json.
    """

    FILL_TIMEOUT   = 30  # seconds to wait for fill
    CANCEL_TIMEOUT = 5   # seconds to wait for cancel confirmation

    def __init__(self, ib_conn, cfg):
        self.ib      = ib_conn.ib
        self.ib_conn = ib_conn
        self.cfg     = cfg
        self.alerts  = None  # set externally if Telegram alerts available

    def place(self, contract, action: str, qty: float,
              name: str) -> 'FillResult':
        """
        Place a market order and wait for full fill confirmation.
        Returns FillResult with actual fill price and filled qty.
        Cancels order on timeout or partial fill.
        """
        try:
            order = Order()
            order.action        = action
            order.orderType     = 'MKT'
            order.totalQuantity = abs(qty)
            order.account       = self.cfg.account
            trade = self.ib.placeOrder(contract, order)

            result = self._wait_for_fill(trade, contract.symbol, abs(qty))

            if result.success:
                log(f"  ORDER FILLED: {action} {result.filled_qty:.0f} "
                    f"{contract.symbol} ({name}) @ {result.fill_price:.4f}")
                return result
            else:
                # Cancel any unfilled/partial order still live at IBKR
                self._cancel_order(trade, contract.symbol)
                status = trade.orderStatus.status
                filled = trade.orderStatus.filled
                log(f"  ORDER NOT FILLED: {action} {contract.symbol} "
                    f"— status={status} filled={filled}/{qty} "
                    f"after {self.FILL_TIMEOUT}s (cancelled)", "ERROR")
                self._alert_fill_failure(action, contract.symbol, name,
                                         f"status={status} filled={filled}/{qty}")
                return FillResult()

        except Exception as e:
            log(f"  ORDER FAILED: {action} {contract.symbol}: {e}", "ERROR")
            self._alert_fill_failure(action, contract.symbol, name, str(e))
            return FillResult()

    def _wait_for_fill(self, trade, symbol: str,
                       expected_qty: float) -> 'FillResult':
        """
        Poll trade status until fully filled or timeout.
        Only returns success when filled == totalQuantity (no partial fills).
        """
        elapsed = 0
        while elapsed < self.FILL_TIMEOUT:
            self.ib.sleep(1)
            elapsed += 1

            status     = trade.orderStatus.status
            filled_qty = trade.orderStatus.filled
            fill_price = trade.orderStatus.avgFillPrice

            # Fully filled
            if filled_qty >= expected_qty:
                return FillResult(True, fill_price, filled_qty)

            # Terminal failure states
            if status in ('Cancelled', 'ApiCancelled', 'Inactive'):
                log(f"  ORDER {symbol}: terminal status {status}", "ERROR")
                return FillResult()

        # Timeout — check one last time
        if trade.orderStatus.filled >= expected_qty:
            return FillResult(True, trade.orderStatus.avgFillPrice,
                              trade.orderStatus.filled)

        return FillResult()

    def _cancel_order(self, trade, symbol: str) -> None:
        """Explicitly cancel a timed-out or partially filled order."""
        try:
            status = trade.orderStatus.status
            if status in ('Cancelled', 'ApiCancelled', 'Inactive', 'Filled'):
                return  # already done

            self.ib.cancelOrder(trade.order)
            # Wait for cancellation confirmation
            for _ in range(self.CANCEL_TIMEOUT):
                self.ib.sleep(1)
                if trade.orderStatus.status in ('Cancelled', 'ApiCancelled'):
                    log(f"  ORDER {symbol}: cancelled successfully")
                    return
            log(f"  ORDER {symbol}: cancel sent but not confirmed "
                f"(status={trade.orderStatus.status})", "WARN")
        except Exception as e:
            log(f"  ORDER {symbol}: cancel failed: {e}", "ERROR")

    def _alert_fill_failure(self, action: str, symbol: str,
                            name: str, detail: str) -> None:
        """Send Telegram alert on fill failure."""
        if self.alerts and hasattr(self.alerts, 'send_error'):
            self.alerts.send_error(
                f"ORDER NOT FILLED: {action} {symbol} ({name}) — {detail}"
            )

    def close(self, inst: dict, position: float) -> 'FillResult':
        """
        Close an existing position (long or short).
        Returns FillResult with actual fill price.
        """
        if position == 0:
            return FillResult()
        action = 'SELL' if position > 0 else 'BUY'
        return self.place(inst['contract'], action, abs(position), inst['name'])

    def handle_signal(self, inst: dict, signal: int,
                      confidence: str, position: float) -> tuple[str, 'FillResult']:
        """
        Execute a trade based on signal and current position.
        Respects long_only constraint.

        Returns (action_string, FillResult).
        """
        long_only = inst.get('long_only', True)
        contract  = inst['contract']
        qty       = inst['qty']
        name      = inst['name']

        if signal == 1:   # BUY signal
            if position < 0:
                close_result = self.close(inst, position)
                if not close_result:
                    return "CLOSE FAILED", FillResult()
                self.ib.sleep(1)
                position = 0
            if position == 0:
                result = self.place(contract, 'BUY', qty, name)
                if result:
                    return f"BOUGHT [{confidence}]", result
                return "BUY FAILED", FillResult()

        elif signal == -1:   # SELL signal
            if long_only:
                if position > 0:
                    result = self.close(inst, position)
                    if result:
                        return "CLOSED (long only)", result
                    return "CLOSE FAILED", FillResult()
                return "Flat (long only)", FillResult()
            else:
                if position > 0:
                    close_result = self.close(inst, position)
                    if not close_result:
                        return "CLOSE FAILED", FillResult()
                    self.ib.sleep(1)
                    position = 0
                if position == 0:
                    result = self.place(contract, 'SELL', qty, name)
                    if result:
                        return f"SHORTED [{confidence}]", result
                    return "SHORT FAILED", FillResult()

        return "--", FillResult()
