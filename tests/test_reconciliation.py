"""Tests for bot/reconciliation.py.

Deliberately free of any database: the diff is a pure function and the gate is
in-memory, so nothing here opens a path in the live tree. The one test that
needs a tracker-shaped object uses a stub, not a real PositionTracker.
"""

from dataclasses import dataclass

import pytest

from bot.reconciliation import (
    PHANTOM, QTY_MISMATCH, UNTRACKED,
    Divergence, PositionReconciler, ReconciliationGate,
    diff_positions, signed_tracked_qty,
)


@dataclass
class FakeBrokerPosition:
    symbol: str
    qty: float           # signed, as IBKR reports it
    avg_cost: float = 100.0
    currency: str = "USD"


@dataclass
class FakeTracked:
    """Shaped like PositionState for the two fields the reconciler reads."""
    qty: float
    side: str = "LONG"


class FakeCfg:
    def __init__(self, unmanaged=()):
        self.unmanaged_positions = list(unmanaged)


class FakeBroker:
    def __init__(self, positions=(), raises=None):
        self._positions = list(positions)
        self._raises = raises
        self.calls = 0

    def get_all_positions(self):
        self.calls += 1
        if self._raises:
            raise self._raises
        return list(self._positions)


class FakeTracker:
    def __init__(self, open_positions=None):
        self.open = dict(open_positions or {})


class FakeAlerts:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send(self, message):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append(message)
        return True


# ── signed_tracked_qty ───────────────────────────────────────────────

def test_signed_qty_handles_both_storage_conventions():
    # init_existing stores the broker's already-signed quantity...
    assert signed_tracked_qty(FakeTracked(qty=-3, side="SHORT")) == -3
    # ...while bot-opened positions store a magnitude plus side.
    assert signed_tracked_qty(FakeTracked(qty=3, side="SHORT")) == -3
    assert signed_tracked_qty(FakeTracked(qty=3, side="LONG")) == 3
    assert signed_tracked_qty(FakeTracked(qty=0, side="LONG")) == 0


# ── diff_positions ───────────────────────────────────────────────────

def test_agreement_produces_no_divergence():
    broker = [FakeBrokerPosition("AAPL", 3), FakeBrokerPosition("MSFT", -2)]
    tracked = {"AAPL": FakeTracked(3, "LONG"), "MSFT": FakeTracked(2, "SHORT")}
    assert diff_positions(broker, tracked) == []


def test_untracked_broker_position_is_flagged():
    """The audit's actual failure: open at the broker, absent from the tracker."""
    result = diff_positions([FakeBrokerPosition("NVDA", 2)], {})
    assert len(result) == 1
    assert result[0].kind == UNTRACKED
    assert result[0].symbol == "NVDA"
    assert result[0].broker_qty == 2
    assert result[0].tracked_qty is None
    assert "not tracked" in result[0].describe()


def test_phantom_tracked_position_is_flagged():
    result = diff_positions([], {"AAPL": FakeTracked(3, "LONG")})
    assert len(result) == 1
    assert result[0].kind == PHANTOM
    assert result[0].tracked_qty == 3
    assert result[0].broker_qty is None


def test_qty_mismatch_is_flagged_with_both_sizes():
    result = diff_positions([FakeBrokerPosition("AAPL", 5)],
                            {"AAPL": FakeTracked(3, "LONG")})
    assert len(result) == 1
    assert result[0].kind == QTY_MISMATCH
    assert result[0].broker_qty == 5
    assert result[0].tracked_qty == 3
    assert "5" in result[0].describe() and "3" in result[0].describe()


def test_sign_flip_is_a_mismatch_not_agreement():
    """A long flipped to an equal-size short must not compare equal."""
    result = diff_positions([FakeBrokerPosition("AAPL", -3)],
                            {"AAPL": FakeTracked(3, "LONG")})
    assert len(result) == 1
    assert result[0].kind == QTY_MISMATCH


def test_unmanaged_symbols_are_excluded_from_both_sides():
    """XAUUSD is a known ghost; alerting on it every cycle would be noise."""
    broker = [FakeBrokerPosition("XAUUSD", 1)]
    tracked = {"XAUUSD": FakeTracked(99, "LONG")}
    assert diff_positions(broker, tracked, unmanaged={"XAUUSD"}) == []
    assert diff_positions(broker, {}, unmanaged={"XAUUSD"}) == []
    assert diff_positions([], tracked, unmanaged={"XAUUSD"}) == []


def test_zero_quantity_broker_rows_are_ignored():
    assert diff_positions([FakeBrokerPosition("AAPL", 0)], {}) == []


def test_duplicate_broker_rows_aggregate_rather_than_overwrite():
    result = diff_positions(
        [FakeBrokerPosition("AAPL", 2), FakeBrokerPosition("AAPL", 1)], {})
    assert len(result) == 1
    assert result[0].broker_qty == 3


def test_positions_outside_active_universe_are_still_reported():
    """The existing layer1 reconcile filters to active_instruments; this must not."""
    result = diff_positions([FakeBrokerPosition("SOMETHING_ODD", 4)], {})
    assert [d.symbol for d in result] == ["SOMETHING_ODD"]


def test_tolerance_absorbs_float_noise():
    result = diff_positions([FakeBrokerPosition("AAPL", 3.0000000001)],
                            {"AAPL": FakeTracked(3.0, "LONG")})
    assert result == []


# ── ReconciliationGate ───────────────────────────────────────────────

def test_gate_reports_a_divergence_once_not_every_cycle():
    gate = ReconciliationGate()
    divergence = Divergence("AAPL", UNTRACKED, 3, None)

    new, cleared = gate.apply([divergence])
    assert [d.symbol for d in new] == ["AAPL"]

    new, cleared = gate.apply([divergence])
    assert new == []          # standing problem stays quiet
    assert gate.is_blocked("AAPL")


def test_gate_default_does_not_auto_clear():
    gate = ReconciliationGate()
    gate.apply([Divergence("AAPL", UNTRACKED, 3, None)])
    new, cleared = gate.apply([])
    assert cleared == []
    assert gate.is_blocked("AAPL"), "a clean read must not silently re-admit risk"


def test_gate_auto_clear_releases_on_agreement():
    gate = ReconciliationGate(auto_clear=True)
    gate.apply([Divergence("AAPL", UNTRACKED, 3, None)])
    new, cleared = gate.apply([])
    assert cleared == ["AAPL"]
    assert not gate.is_blocked("AAPL")


def test_gate_explicit_clear():
    gate = ReconciliationGate()
    gate.apply([Divergence("AAPL", UNTRACKED, 3, None)])
    assert gate.clear("AAPL") is True
    assert gate.clear("AAPL") is False
    assert not gate.is_blocked("AAPL")


def test_gate_blocks_only_the_divergent_symbol():
    gate = ReconciliationGate()
    gate.apply([Divergence("AAPL", UNTRACKED, 3, None)])
    assert gate.is_blocked("AAPL")
    assert not gate.is_blocked("MSFT")


# ── PositionReconciler ───────────────────────────────────────────────

def _reconciler(broker, tracker, alerts=None, cfg=None, **kw):
    return PositionReconciler(cfg or FakeCfg(), broker, tracker, alerts, **kw)


def test_reconciler_alerts_with_symbol_and_size_detail():
    alerts = FakeAlerts()
    rec = _reconciler(FakeBroker([FakeBrokerPosition("NVDA", 2)]),
                      FakeTracker(), alerts)
    rec.run(context="startup")

    assert len(alerts.sent) == 1
    message = alerts.sent[0]
    assert "NVDA" in message
    assert "+2" in message
    assert "startup" in message
    assert rec.is_blocked("NVDA")


def test_reconciler_alerts_once_for_a_standing_divergence():
    alerts = FakeAlerts()
    rec = _reconciler(FakeBroker([FakeBrokerPosition("NVDA", 2)]),
                      FakeTracker(), alerts)
    rec.run()
    rec.run()
    rec.run()
    assert len(alerts.sent) == 1


def test_reconciler_clean_book_alerts_nothing_and_blocks_nothing():
    alerts = FakeAlerts()
    rec = _reconciler(
        FakeBroker([FakeBrokerPosition("AAPL", 3)]),
        FakeTracker({"AAPL": FakeTracked(3, "LONG")}), alerts)
    assert rec.run() == []
    assert alerts.sent == []
    assert not rec.is_blocked("AAPL")


def test_broker_read_failure_retains_blocks_and_infers_nothing():
    """An unreadable book is not evidence of agreement — Phase 1's lesson."""
    alerts = FakeAlerts()
    broker = FakeBroker([FakeBrokerPosition("NVDA", 2)])
    rec = _reconciler(broker, FakeTracker(), alerts)
    rec.run()
    assert rec.is_blocked("NVDA")

    broker._raises = ConnectionError("gateway down")
    assert rec.run() == []
    assert rec.is_blocked("NVDA"), "a failed read must not release a block"


def test_broker_read_failure_does_not_invent_phantoms():
    """A raising read must not be treated as 'broker holds nothing'."""
    alerts = FakeAlerts()
    rec = _reconciler(FakeBroker(raises=TimeoutError("no data")),
                      FakeTracker({"AAPL": FakeTracked(3, "LONG")}), alerts)
    assert rec.run() == []
    assert not rec.is_blocked("AAPL")


def test_reconciler_respects_unmanaged_from_cfg():
    alerts = FakeAlerts()
    rec = _reconciler(FakeBroker([FakeBrokerPosition("XAUUSD", 1)]),
                      FakeTracker(), alerts, cfg=FakeCfg(["XAUUSD"]))
    assert rec.run() == []
    assert alerts.sent == []


def test_alert_failure_never_breaks_the_cycle():
    rec = _reconciler(FakeBroker([FakeBrokerPosition("NVDA", 2)]),
                      FakeTracker(), FakeAlerts(fail=True))
    rec.run()                      # must not raise
    assert rec.is_blocked("NVDA")


def test_reconciler_works_without_alerts_configured():
    rec = _reconciler(FakeBroker([FakeBrokerPosition("NVDA", 2)]),
                      FakeTracker(), None)
    assert len(rec.run()) == 1
    assert rec.is_blocked("NVDA")


# ── layer1 wiring ────────────────────────────────────────────────────
#
# ActiveTrading is built via object.__new__ so that __init__ -- which would
# construct a real PositionTracker against the live positions.db path -- never
# runs. Only the attributes _can_enter actually reads are populated.

class _StubReconciler:
    def __init__(self, blocked=()):
        self._blocked = set(blocked)

    def is_blocked(self, symbol):
        return symbol in self._blocked


def _entry_gate(blocked=(), open_count=0, entries_this_cycle=0):
    from types import SimpleNamespace

    from bot.layer1 import ActiveTrading

    bot = object.__new__(ActiveTrading)
    bot.reconciler = _StubReconciler(blocked)
    bot.cfg = SimpleNamespace(max_open_positions=5, max_entries_per_cycle=3)
    bot._open_count = open_count
    bot._entries_this_cycle = entries_this_cycle
    return bot


def test_can_enter_blocks_a_divergent_symbol():
    gate = _entry_gate(blocked={"AAPL"})
    assert gate._can_enter("AAPL") is False


def test_can_enter_allows_other_symbols_while_one_is_blocked():
    """The block is per-symbol: unrelated instruments keep trading."""
    gate = _entry_gate(blocked={"AAPL"})
    assert gate._can_enter("MSFT") is True


def test_can_enter_unaffected_when_nothing_diverges():
    gate = _entry_gate()
    assert gate._can_enter("AAPL") is True


def test_divergence_block_precedes_the_existing_risk_limits():
    """A divergent symbol is refused even with capacity to spare."""
    gate = _entry_gate(blocked={"AAPL"}, open_count=0, entries_this_cycle=0)
    assert gate._can_enter("AAPL") is False


def test_existing_risk_limits_still_apply():
    """The new check must not shadow the limits that were already there."""
    assert _entry_gate(open_count=5)._can_enter("AAPL") is False
    assert _entry_gate(entries_this_cycle=3)._can_enter("AAPL") is False
