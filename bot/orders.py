"""
bot/orders.py
Order management — place and close positions via IBKR.
All orders are market orders. Handles long-only constraint.
"""

from ib_insync import Order
from bot.logger import log


class OrderManager:
    """
    Places and closes orders via IBKR.
    Respects long_only constraint from instruments.json.
    """

    def __init__(self, ib_conn, cfg):
        self.ib  = ib_conn.ib
        self.cfg = cfg

    def place(self, contract, action: str, qty: float, name: str) -> bool:
        """
        Place a market order.
        Returns True if order was accepted, False on failure.
        """
        try:
            order = Order()
            order.action        = action
            order.orderType     = 'MKT'
            order.totalQuantity = abs(qty)
            order.account       = self.cfg.account
            trade = self.ib.placeOrder(contract, order)
            self.ib.sleep(2)
            log(f"  ORDER: {action} {abs(qty):.0f} {contract.symbol} ({name}) "
                f"→ {trade.orderStatus.status}")
            return True
        except Exception as e:
            log(f"  ORDER FAILED: {action} {contract.symbol}: {e}", "ERROR")
            return False

    def close(self, inst: dict, position: float) -> bool:
        """
        Close an existing position (long or short).
        Returns True if order placed.
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
                self.close(inst, position)
                self.ib.sleep(1)
                position = 0
            if position == 0:
                self.place(contract, 'BUY', qty, name)
                return f"BOUGHT [{confidence}]"

        elif signal == -1:   # SELL signal
            if long_only:
                if position > 0:
                    self.close(inst, position)
                    return "CLOSED (long only)"
                return "Flat (long only)"
            else:
                if position > 0:
                    self.close(inst, position)
                    self.ib.sleep(1)
                    position = 0
                if position == 0:
                    self.place(contract, 'SELL', qty, name)
                    return f"SHORTED [{confidence}]"

        return "--"
