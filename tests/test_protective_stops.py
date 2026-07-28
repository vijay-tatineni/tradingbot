"""Tests for broker-attached protective stops (PR B).

No database and no network: OrderManager is driven through a fake ib_insync
surface, so nothing here opens a live-tree path.
"""

from types import SimpleNamespace

import pytest

from bot.orders import FillResult, OrderManager
from bot.protective_stops import (
    compute_stop_price, describe, resolve_emergency_stop_pct,
    round_to_tick, stop_order_action,
)


# ── level computation ────────────────────────────────────────────────

def test_emergency_pct_defaults_to_double_the_trail():
    assert resolve_emergency_stop_pct({"trail_stop_pct": 2.0}) == 4.0
    assert resolve_emergency_stop_pct({}) == 4.0   # trail defaults to 2.0


def test_emergency_pct_honours_an_explicit_override():
    """24 configured instruments set this explicitly; the spec's
    'trail_stop_pct * 2' is only the default."""
    inst = {"trail_stop_pct": 5.0, "emergency_stop_pct": 10.0}
    assert resolve_emergency_stop_pct(inst) == 10.0
    # ANTO-shaped: doubling would give 2.0, the real emergency level is 5.0
    assert resolve_emergency_stop_pct(
        {"trail_stop_pct": 1.0, "emergency_stop_pct": 5.0}) == 5.0


def test_stop_price_sits_below_entry_for_a_long():
    assert compute_stop_price(100.0, "LONG", 10.0) == pytest.approx(90.0)


def test_stop_price_sits_above_entry_for_a_short():
    assert compute_stop_price(100.0, "SHORT", 10.0) == pytest.approx(110.0)


def test_stop_price_matches_check_emergency_stop_arithmetic():
    """Broker stop and synthetic emergency stop must describe one price."""
    entry, pct = 307.64, 8.0
    assert compute_stop_price(entry, "LONG", pct) == pytest.approx(
        entry * (1 - pct / 100))
    assert compute_stop_price(entry, "SHORT", pct) == pytest.approx(
        entry * (1 + pct / 100))


def test_stop_price_rejects_nonsense_inputs():
    with pytest.raises(ValueError):
        compute_stop_price(0, "LONG", 10)
    with pytest.raises(ValueError):
        compute_stop_price(100, "LONG", 0)


def test_closing_action_is_the_opposite_of_the_position():
    assert stop_order_action("LONG") == "SELL"
    assert stop_order_action("SHORT") == "BUY"


def test_round_to_tick_snaps_and_tolerates_missing_tick():
    assert round_to_tick(90.123, 0.01) == pytest.approx(90.12)
    assert round_to_tick(90.123, 0) == 90.123


def test_describe_mentions_symbol_and_level():
    text = describe("AAPL", "LONG", 100.0, 8.0, 92.0)
    assert "AAPL" in text and "92.0000" in text


# ── fake ib_insync surface ───────────────────────────────────────────

class FakeOrderStatus:
    def __init__(self, status="PreSubmitted", filled=0.0, avg=0.0):
        self.status = status
        self.filled = filled
        self.avgFillPrice = avg


class FakeTrade:
    def __init__(self, contract, order, status="PreSubmitted",
                 filled=0.0, avg=0.0):
        self.contract = contract
        self.order = order
        self.orderStatus = FakeOrderStatus(status, filled, avg)


class FakeClient:
    def __init__(self):
        self._next = 1000

    def getReqId(self):
        self._next += 1
        return self._next


class FakeIB:
    """Records placed orders and hands back scripted trade states."""

    def __init__(self, fill_price=100.0, parent_filled=True,
                 stop_status="PreSubmitted", retry_status=None):
        self.client = FakeClient()
        self.placed = []
        self.cancelled = []
        self.open_trades = []
        self.fill_price = fill_price
        self.parent_filled = parent_filled
        self.stop_status = stop_status
        self.retry_status = retry_status
        self._stop_count = 0

    def placeOrder(self, contract, order):
        self.placed.append((contract, order))
        if order.orderType == "MKT":
            filled = order.totalQuantity if self.parent_filled else 0.0
            status = "Filled" if self.parent_filled else "Submitted"
            return FakeTrade(contract, order, status, filled, self.fill_price)
        self._stop_count += 1
        status = self.stop_status
        if self._stop_count > 1 and self.retry_status is not None:
            status = self.retry_status
        return FakeTrade(contract, order, status)

    def cancelOrder(self, order):
        self.cancelled.append(order)

    def sleep(self, _seconds):
        return None

    def openTrades(self):
        return list(self.open_trades)


def _manager(ib):
    cfg = SimpleNamespace(account="DUQ141950")
    return OrderManager(SimpleNamespace(ib=ib), cfg)


CONTRACT = SimpleNamespace(symbol="AAPL")


# ── bracket construction ─────────────────────────────────────────────

def test_entry_without_stop_price_stays_a_bare_market_order():
    """Back-compat: closes and legacy callers must be unaffected."""
    ib = FakeIB()
    result = _manager(ib).place(CONTRACT, "BUY", 3, "Apple")
    assert result.success
    assert len(ib.placed) == 1
    assert ib.placed[0][1].orderType == "MKT"
    assert result.stop_attached is False


def test_entry_with_stop_submits_parent_and_child_atomically():
    ib = FakeIB()
    result = _manager(ib).place(CONTRACT, "BUY", 3, "Apple", stop_price=92.0)

    assert result.success and result.stop_attached
    assert len(ib.placed) == 2
    parent, child = ib.placed[0][1], ib.placed[1][1]

    # The parent is held until the child arrives; the child releases both.
    assert parent.transmit is False
    assert child.transmit is True
    assert child.parentId == parent.orderId
    assert child.orderType == "STP"
    assert child.auxPrice == pytest.approx(92.0)
    assert child.totalQuantity == 3


def test_child_stop_closes_the_position_it_protects():
    ib = FakeIB()
    _manager(ib).place(CONTRACT, "BUY", 3, "Apple", stop_price=92.0)
    assert ib.placed[1][1].action == "SELL"

    ib2 = FakeIB()
    _manager(ib2).place(CONTRACT, "SELL", 3, "Apple", stop_price=108.0)
    assert ib2.placed[1][1].action == "BUY"


def test_unfilled_parent_tears_down_the_whole_bracket():
    ib = FakeIB(parent_filled=False)
    result = _manager(ib).place(CONTRACT, "BUY", 3, "Apple", stop_price=92.0)
    assert not result.success
    assert len(ib.cancelled) == 2, "both parent and child must be cancelled"


# ── attach-failure invariant ─────────────────────────────────────────

def test_dead_stop_is_retried_once_and_succeeds():
    ib = FakeIB(stop_status="Cancelled", retry_status="Submitted")
    result = _manager(ib).place(CONTRACT, "BUY", 3, "Apple", stop_price=92.0)
    assert result.success and result.stop_attached
    stops = [o for _, o in ib.placed if o.orderType == "STP"]
    assert len(stops) == 2, "exactly one retry"


def test_position_is_flattened_when_the_stop_cannot_be_attached():
    """Spec invariant: no unprotected position persists."""
    alerts = SimpleNamespace(sent=[])
    alerts.send_error = alerts.sent.append

    ib = FakeIB(stop_status="Cancelled", retry_status="Cancelled")
    manager = _manager(ib)
    manager.alerts = alerts
    result = manager.place(CONTRACT, "BUY", 3, "Apple", stop_price=92.0)

    # The entry is reported as failed, because the position was undone.
    assert not result.success
    closing = [o for _, o in ib.placed
               if o.orderType == "MKT" and o.action == "SELL"]
    assert len(closing) == 1, "position must be flattened"
    assert closing[0].totalQuantity == 3
    assert alerts.sent and "flattened" in alerts.sent[0].lower()


def test_failed_flatten_escalates_to_manual_intervention():
    """Worst case: open, unprotected, and we could not close it."""
    alerts = SimpleNamespace(sent=[])
    alerts.send_error = alerts.sent.append

    class StubbornIB(FakeIB):
        def placeOrder(self, contract, order):
            trade = super().placeOrder(contract, order)
            if order.orderType == "MKT" and order.action == "SELL":
                trade.orderStatus.status = "Submitted"
                trade.orderStatus.filled = 0.0     # flatten never fills
            return trade

    ib = StubbornIB(stop_status="Cancelled", retry_status="Cancelled")
    manager = _manager(ib)
    manager.alerts = alerts
    manager.place(CONTRACT, "BUY", 3, "Apple", stop_price=92.0)

    assert any("UNPROTECTED" in m and "manual" in m.lower()
               for m in alerts.sent)


# ── cancel-on-exit ───────────────────────────────────────────────────

def _working_stop(symbol="AAPL"):
    order = SimpleNamespace(orderType="STP", orderId=77, action="SELL")
    return FakeTrade(SimpleNamespace(symbol=symbol), order, "Submitted")


def test_close_cancels_the_working_stop_first():
    """A stop left working after a close can OPEN a new position."""
    ib = FakeIB()
    ib.open_trades = [_working_stop("AAPL")]
    manager = _manager(ib)
    manager.close({"contract": CONTRACT, "name": "Apple"}, 3)

    assert len(ib.cancelled) == 1
    assert ib.cancelled[0].orderType == "STP"


def test_close_only_cancels_stops_for_its_own_symbol():
    ib = FakeIB()
    ib.open_trades = [_working_stop("AAPL"), _working_stop("MSFT")]
    manager = _manager(ib)
    manager.close({"contract": CONTRACT, "name": "Apple"}, 3)
    assert len(ib.cancelled) == 1


def test_closing_order_never_carries_a_stop_of_its_own():
    ib = FakeIB()
    manager = _manager(ib)
    manager.close({"contract": CONTRACT, "name": "Apple"}, 3)
    assert all(o.orderType == "MKT" for _, o in ib.placed)


def test_working_stop_trades_ignores_non_stop_and_dead_orders():
    ib = FakeIB()
    dead = _working_stop("AAPL")
    dead.orderStatus.status = "Cancelled"
    mkt = FakeTrade(SimpleNamespace(symbol="AAPL"),
                    SimpleNamespace(orderType="MKT"), "Submitted")
    ib.open_trades = [dead, mkt, _working_stop("AAPL")]
    assert len(_manager(ib).working_stop_trades("AAPL")) == 1


def test_handle_signal_passes_the_stop_to_entries_but_not_closes():
    ib = FakeIB()
    manager = _manager(ib)
    inst = {"contract": CONTRACT, "qty": 3, "name": "Apple",
            "long_only": True}
    action, result = manager.handle_signal(inst, 1, "HIGH", 0, stop_price=92.0)
    assert "BOUGHT" in action
    assert result.stop_attached
    assert any(o.orderType == "STP" for _, o in ib.placed)


# ── startup protective-stop check (wired into PR A's reconciler) ─────

from bot.reconciliation import PositionReconciler          # noqa: E402


class StopBroker:
    def __init__(self, stops=(), positions=(), supports=True, raises=None):
        self._stops = set(stops)
        self._positions = list(positions)
        self._supports = supports
        self._raises = raises

    def supports_stop_introspection(self):
        return self._supports

    def working_stop_symbols(self):
        if self._raises:
            raise self._raises
        return set(self._stops)

    def get_all_positions(self):
        if self._raises:
            raise self._raises
        return [SimpleNamespace(symbol=s, qty=q) for s, q in self._positions]


class StopAlerts:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


def _stop_reconciler(broker, tracked=(), alerts=None, unmanaged=()):
    cfg = SimpleNamespace(unmanaged_positions=list(unmanaged))
    tracker = SimpleNamespace(open={s: SimpleNamespace(qty=1, side="LONG")
                                    for s in tracked})
    return PositionReconciler(cfg, broker, tracker, alerts)


def test_tracked_position_without_a_broker_stop_is_reported():
    alerts = StopAlerts()
    broker = StopBroker(stops=(), positions=[("AAPL", 3)])
    result = _stop_reconciler(broker, ["AAPL"], alerts).check_protective_stops()

    assert result["unprotected"] == ["AAPL"]
    assert result["verified"] is True
    assert alerts.sent and "no broker-held stop" in alerts.sent[0]


def test_protected_position_is_silent():
    alerts = StopAlerts()
    broker = StopBroker(stops=["AAPL"], positions=[("AAPL", 3)])
    result = _stop_reconciler(broker, ["AAPL"], alerts).check_protective_stops()
    assert result["unprotected"] == []
    assert alerts.sent == []


def test_orphaned_stop_is_reported():
    """A stop with no position can OPEN one — how a flatten becomes a reversal."""
    alerts = StopAlerts()
    broker = StopBroker(stops=["NVDA"], positions=[])
    result = _stop_reconciler(broker, [], alerts).check_protective_stops()
    assert result["orphaned"] == ["NVDA"]
    assert "orphaned" in alerts.sent[0]


def test_stop_check_reports_once_not_every_cycle():
    alerts = StopAlerts()
    broker = StopBroker(stops=(), positions=[("AAPL", 3)])
    rec = _stop_reconciler(broker, ["AAPL"], alerts)
    rec.check_protective_stops()
    rec.check_protective_stops()
    rec.check_protective_stops()
    assert len(alerts.sent) == 1


def test_unsupported_introspection_is_unverified_not_unprotected():
    """Absence of evidence must not be reported as evidence of absence."""
    alerts = StopAlerts()
    broker = StopBroker(supports=False)
    result = _stop_reconciler(broker, ["AAPL"], alerts).check_protective_stops()
    assert result["verified"] is False
    assert result["unprotected"] == []
    assert alerts.sent == []


def test_broker_error_during_stop_check_is_unverified():
    alerts = StopAlerts()
    broker = StopBroker(raises=ConnectionError("gateway down"))
    result = _stop_reconciler(broker, ["AAPL"], alerts).check_protective_stops()
    assert result["verified"] is False
    assert result["unprotected"] == []


def test_unmanaged_symbols_are_exempt_from_the_stop_check():
    alerts = StopAlerts()
    broker = StopBroker(stops=["XAUUSD"], positions=[])
    rec = _stop_reconciler(broker, ["XAUUSD"], alerts, unmanaged=["XAUUSD"])
    result = rec.check_protective_stops()
    assert result["unprotected"] == []
    assert result["orphaned"] == []
    assert alerts.sent == []
