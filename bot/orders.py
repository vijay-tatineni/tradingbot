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
    """Result of an order placement attempt.

    ``stop_attached`` reports whether a broker-held protective stop is working
    for the position this fill opened. It is meaningful only for entries; a
    close leaves it False.
    """
    __slots__ = ('success', 'fill_price', 'filled_qty',
                 'stop_attached', 'stop_order_id')

    def __init__(self, success: bool = False, fill_price: float = 0.0,
                 filled_qty: float = 0.0, stop_attached: bool = False,
                 stop_order_id: int = 0):
        self.success       = success
        self.fill_price    = fill_price
        self.filled_qty    = filled_qty
        self.stop_attached = stop_attached
        self.stop_order_id = stop_order_id

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
              name: str, stop_price: float = None) -> 'FillResult':
        """
        Place a market order and wait for full fill confirmation.
        Returns FillResult with actual fill price and filled qty.
        Cancels order on timeout or partial fill.

        When ``stop_price`` is given the order is submitted as a bracket:
        a MKT parent plus an attached STP child transmitted atomically, so the
        position is never briefly live at the broker without protection. Closes
        pass no stop_price and keep the original bare-MKT behaviour.
        """
        if stop_price is not None:
            return self._place_protected(contract, action, qty, name,
                                         stop_price)
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

    # ── Broker-attached protective stops ────────────────

    STOP_VERIFY_TIMEOUT = 5   # seconds to confirm the child stop is working
    LIVE_STOP_STATUSES = ('PreSubmitted', 'Submitted', 'PendingSubmit')

    def _build_bracket(self, contract, action: str, qty: float,
                       stop_price: float):
        """MKT parent + attached STP child, transmitted atomically.

        The parent carries transmit=False so IBKR holds it until the child
        arrives; the child's transmit=True releases both at once. That is what
        makes the entry and its protection a single atomic submission -- the
        position can never be live at the broker without a working stop.
        """
        closing = 'SELL' if action.upper() == 'BUY' else 'BUY'

        parent = Order()
        parent.action        = action
        parent.orderType     = 'MKT'
        parent.totalQuantity = abs(qty)
        parent.account       = self.cfg.account
        parent.orderId       = self.ib.client.getReqId()
        parent.transmit      = False

        child = Order()
        child.action        = closing
        child.orderType     = 'STP'
        child.totalQuantity = abs(qty)
        child.auxPrice      = float(stop_price)
        child.account       = self.cfg.account
        child.orderId       = self.ib.client.getReqId()
        child.parentId      = parent.orderId
        child.transmit      = True
        return parent, child

    def _stop_is_working(self, trade) -> bool:
        """True once the broker acknowledges the stop as a live working order."""
        for _ in range(self.STOP_VERIFY_TIMEOUT):
            status = trade.orderStatus.status
            if status in self.LIVE_STOP_STATUSES:
                return True
            if status in ('Cancelled', 'ApiCancelled', 'Inactive', 'Filled'):
                return False
            self.ib.sleep(1)
        return trade.orderStatus.status in self.LIVE_STOP_STATUSES

    def _attach_standalone_stop(self, contract, entry_action: str, qty: float,
                                stop_price: float):
        """Single retry of the attach, as a standalone STP after a parent fill."""
        closing = 'SELL' if entry_action.upper() == 'BUY' else 'BUY'
        stop = Order()
        stop.action        = closing
        stop.orderType     = 'STP'
        stop.totalQuantity = abs(qty)
        stop.auxPrice      = float(stop_price)
        stop.account       = self.cfg.account
        stop.transmit      = True
        trade = self.ib.placeOrder(contract, stop)
        return trade if self._stop_is_working(trade) else None

    def _place_protected(self, contract, action: str, qty: float,
                         name: str, stop_price: float) -> 'FillResult':
        """Entry with an atomically-attached protective stop.

        Spec invariant: no unprotected position persists. If the parent fills
        but the stop does not become a working order, the attach is retried
        exactly once; if it still fails, the position is flattened immediately
        and an alert raised. An entry that cannot be protected is an entry the
        system refuses to hold.
        """
        symbol = contract.symbol
        try:
            parent, child = self._build_bracket(contract, action, qty,
                                                stop_price)
            parent_trade = self.ib.placeOrder(contract, parent)
            stop_trade   = self.ib.placeOrder(contract, child)

            result = self._wait_for_fill(parent_trade, symbol, abs(qty))

            if not result.success:
                # Never filled: tear the whole bracket down.
                self._cancel_order(stop_trade, symbol)
                self._cancel_order(parent_trade, symbol)
                status = parent_trade.orderStatus.status
                filled = parent_trade.orderStatus.filled
                log(f"  ORDER NOT FILLED: {action} {symbol} — status={status} "
                    f"filled={filled}/{qty} (bracket cancelled)", "ERROR")
                self._alert_fill_failure(action, symbol, name,
                                         f"status={status} filled={filled}/{qty}")
                return FillResult()

            log(f"  ORDER FILLED: {action} {result.filled_qty:.0f} {symbol} "
                f"({name}) @ {result.fill_price:.4f}")

            if self._stop_is_working(stop_trade):
                log(f"  STOP ATTACHED: {symbol} @ {stop_price:.4f} "
                    f"(order {child.orderId})")
                return FillResult(True, result.fill_price, result.filled_qty,
                                  True, child.orderId)

            # Filled but unprotected — one retry, then flatten.
            log(f"  STOP NOT WORKING: {symbol} "
                f"(status={stop_trade.orderStatus.status}) — retrying attach",
                "WARN")
            retry = self._attach_standalone_stop(contract, action,
                                                 result.filled_qty, stop_price)
            if retry is not None:
                log(f"  STOP ATTACHED on retry: {symbol} @ {stop_price:.4f}")
                return FillResult(True, result.fill_price, result.filled_qty,
                                  True, retry.order.orderId)

            return self._flatten_unprotected(contract, action, result, name)

        except Exception as e:
            log(f"  PROTECTED ORDER FAILED: {action} {symbol}: {e}", "ERROR")
            self._alert_fill_failure(action, symbol, name, str(e))
            return FillResult()

    def _flatten_unprotected(self, contract, entry_action: str,
                             fill: 'FillResult', name: str) -> 'FillResult':
        """Last resort: close a position we could not protect, and alert."""
        symbol  = contract.symbol
        closing = 'SELL' if entry_action.upper() == 'BUY' else 'BUY'
        log(f"  UNPROTECTED POSITION {symbol} — flattening immediately "
            f"(stop could not be attached after retry)", "ERROR")

        undo = self.place(contract, closing, fill.filled_qty, name)

        if undo:
            detail = (f"{symbol}: entry filled but no protective stop could be "
                      f"attached after one retry. Position flattened "
                      f"({closing} {fill.filled_qty:.0f} @ {undo.fill_price:.4f}).")
            log(f"  {symbol} flattened after failed stop attach", "ERROR")
        else:
            detail = (f"{symbol}: entry filled, protective stop FAILED, and the "
                      f"flattening order ALSO failed. POSITION IS OPEN AND "
                      f"UNPROTECTED — manual intervention required.")
            log(f"  {symbol} FLATTEN FAILED — unprotected position open",
                "ERROR")

        if self.alerts and hasattr(self.alerts, 'send_error'):
            self.alerts.send_error(detail)
        return FillResult()

    def working_stop_trades(self, symbol: str = None) -> list:
        """Live STP orders at the broker, optionally filtered to one symbol.

        Used both to cancel a position's stop on exit and to detect orphans.
        """
        found = []
        try:
            for trade in self.ib.openTrades():
                if getattr(trade.order, 'orderType', '') != 'STP':
                    continue
                if trade.orderStatus.status not in self.LIVE_STOP_STATUSES:
                    continue
                if symbol and trade.contract.symbol != symbol:
                    continue
                found.append(trade)
        except Exception as e:
            log(f"  [Stops] could not list working stops: {e}", "WARN")
        return found

    def cancel_stops_for(self, symbol: str) -> int:
        """Cancel every working stop for a symbol. Returns how many were cancelled.

        Mandatory before a synthetic exit: the broker stop is never ratcheted
        and never expires on its own, so a close that left it working would
        leave a naked stop able to open a NEW position in the opposite
        direction the next time price touched it.
        """
        cancelled = 0
        for trade in self.working_stop_trades(symbol):
            self._cancel_order(trade, symbol)
            cancelled += 1
        if cancelled:
            log(f"  [Stops] cancelled {cancelled} working stop(s) for {symbol}")
        return cancelled

    def close(self, inst: dict, position: float) -> 'FillResult':
        """
        Close an existing position (long or short).
        Returns FillResult with actual fill price.

        Cancels any broker-held protective stop first (spec: every synthetic
        exit must cancel the attached stop, so no naked working stop survives
        a closed position).
        """
        if position == 0:
            return FillResult()
        action = 'SELL' if position > 0 else 'BUY'
        try:
            self.cancel_stops_for(inst['contract'].symbol)
        except Exception as e:
            log(f"  [Stops] cancel-before-close failed for "
                f"{inst.get('name', '?')}: {e}", "WARN")
        return self.place(inst['contract'], action, abs(position), inst['name'])

    def handle_signal(self, inst: dict, signal: int, confidence: str,
                      position: float,
                      stop_price: float = None) -> tuple[str, 'FillResult']:
        """
        Execute a trade based on signal and current position.
        Respects long_only constraint.

        ``stop_price`` is applied to *entries* only. Closes deliberately pass
        no stop: a closing order must never carry protection of its own, and
        the stop belonging to the position being closed is cancelled inside
        ``close()``.

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
                result = self.place(contract, 'BUY', qty, name,
                                    stop_price=stop_price)
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
                    result = self.place(contract, 'SELL', qty, name,
                                        stop_price=stop_price)
                    if result:
                        return f"SHORTED [{confidence}]", result
                    return "SHORT FAILED", FillResult()

        return "--", FillResult()
