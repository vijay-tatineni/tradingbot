"""
bot/orders.py
Order management — place and close positions via IBKR.
All orders are market orders. Handles long-only constraint.
Waits for fill confirmation before returning.
"""

from ib_insync import Order
from bot.logger import log


class OrderManager:
    """
    Places and closes orders via IBKR.
    Respects long_only constraint from instruments.json.
    """

    FILL_TIMEOUT = 30  # seconds to wait for fill

    def __init__(self, ib_conn, cfg):
        self.ib      = ib_conn.ib
        self.ib_conn = ib_conn
        self.cfg     = cfg
        self.alerts  = None  # set externally if Telegram alerts available

    def place(self, contract, action: str, qty: float, name: str) -> bool:
        """
        Place a market order and wait for fill confirmation.
        Returns True only when order is filled.
        """
        try:
            order = Order()
            order.action        = action
            order.orderType     = 'MKT'
            order.totalQuantity = abs(qty)
            order.account       = self.cfg.account
            trade = self.ib.placeOrder(contract, order)

            # Wait up to FILL_TIMEOUT seconds for fill
            filled = self._wait_for_fill(trade, contract.symbol)

            if filled:
                fill_price = trade.orderStatus.avgFillPrice
                log(f"  ORDER FILLED: {action} {abs(qty):.0f} {contract.symbol} "
                    f"({name}) @ {fill_price:.4f}")
                return True
            else:
                status = trade.orderStatus.status
                log(f"  ORDER NOT FILLED: {action} {contract.symbol} "
                    f"— status={status} after {self.FILL_TIMEOUT}s", "ERROR")
                self._alert_fill_failure(action, contract.symbol, name, status)
                return False

        except Exception as e:
            log(f"  ORDER FAILED: {action} {contract.symbol}: {e}", "ERROR")
            self._alert_fill_failure(action, contract.symbol, name, str(e))
            return False

    def _wait_for_fill(self, trade, symbol: str) -> bool:
        """Poll trade status until filled or timeout."""
        elapsed = 0
        while elapsed < self.FILL_TIMEOUT:
            self.ib.sleep(1)
            elapsed += 1

            status = trade.orderStatus.status
            if trade.orderStatus.filled > 0:
                return True
            if status in ('Cancelled', 'ApiCancelled', 'Inactive'):
                log(f"  ORDER {symbol}: terminal status {status}", "ERROR")
                return False

        return trade.orderStatus.filled > 0

    def _alert_fill_failure(self, action: str, symbol: str,
                            name: str, detail: str) -> None:
        """Send Telegram alert on fill failure."""
        if self.alerts and hasattr(self.alerts, 'send_error'):
            self.alerts.send_error(
                f"ORDER NOT FILLED: {action} {symbol} ({name}) — {detail}"
            )

    def close(self, inst: dict, position: float) -> bool:
        """
        Close an existing position (long or short).
        Returns True if order filled.
        """
        if position == 0:
            return False
        action = 'SELL' if position > 0 else 'BUY'
        return self.place(inst['contract'], action, abs(position), inst['name'])

    def handle_signal(self, inst: dict, signal: int,
                      confidence: str, position: float) -> str:
        """
        Execute a trade based on signal and current position.
        Respects long_only constraint.

        Returns action string describing what was done.
        """
        long_only = inst.get('long_only', True)
        contract  = inst['contract']
        qty       = inst['qty']
        name      = inst['name']

        if signal == 1:   # BUY signal
            if position < 0:
                if not self.close(inst, position):
                    return "CLOSE FAILED"
                self.ib.sleep(1)
                position = 0
            if position == 0:
                if self.place(contract, 'BUY', qty, name):
                    return f"BOUGHT [{confidence}]"
                return "BUY FAILED"

        elif signal == -1:   # SELL signal
            if long_only:
                if position > 0:
                    if self.close(inst, position):
                        return "CLOSED (long only)"
                    return "CLOSE FAILED"
                return "Flat (long only)"
            else:
                if position > 0:
                    if not self.close(inst, position):
                        return "CLOSE FAILED"
                    self.ib.sleep(1)
                    position = 0
                if position == 0:
                    if self.place(contract, 'SELL', qty, name):
                        return f"SHORTED [{confidence}]"
                    return "SHORT FAILED"

        return "--"
