"""Fixed-fractional risk sizing (PR D).

Formula under test::

    risk_capital  = equity x risk_fraction              (account base ccy)
    stop_distance = price x trail_stop_pct / 100        (per share, native)
    qty           = floor(risk_capital / (stop_distance x fx_to_base))

The stop distance is the **synthetic trailing stop** -- the primary exit while
the bot is alive -- not the wider broker-held emergency stop from PR B. Sizing
against the emergency level would systematically undersize every position
relative to the exit that actually fires.

Pure arithmetic; no broker, no database.
"""

import pytest

from bot.order_validator import OrderValidationError, validate_order
from bot.sizing import (
    DEFAULT_RISK_FRACTION, calculate_qty, calculate_risk_qty, native_price,
    resolve_risk_fraction,
)

# Phase 1 baseline, the figure Phase 3 starts from.
EQUITY = 250_000.0


# ── worked examples ──────────────────────────────────────────────────
#
# equity 250,000 @ 1% = 2,500 of risk capital per position.
#
#  #  currency  price     trail%  stop/share  fx     expected qty
#  1  USD       100.00     5.0     5.00       1.00   500
#  2  USD       100.00     1.0     1.00       1.00   2500
#  3  USD       307.64     8.0    24.61       1.00   101
#  4  GBP(pence) 12000.0   5.0     6.00 GBP   1.00   416
#  5  USD       100.00     5.0     5.00 USD   0.79   632

WORKED_EXAMPLES = [
    ("plain USD, 5% stop", "USD", 100.00, 5.0, 1.00, 500),
    ("tighter 1% stop sizes up", "USD", 100.00, 1.0, 1.00, 2500),
    ("AAPL-like at 8%", "USD", 307.64, 8.0, 1.00, 101),
    ("LSE pence instrument", "GBP", 12000.0, 5.0, 1.00, 416),
    ("USD priced, GBP account", "USD", 100.00, 5.0, 0.79, 632),
]


@pytest.mark.parametrize("label,currency,price,trail,fx,expected",
                         WORKED_EXAMPLES)
def test_worked_example(label, currency, price, trail, fx, expected):
    qty = calculate_risk_qty(EQUITY, 0.01, price, trail, currency,
                             fx_to_base=fx)
    assert qty == expected, label


def test_the_formula_is_what_the_docstring_says():
    """Recompute independently rather than trusting the implementation."""
    equity, risk, price, trail = 250_000.0, 0.01, 100.0, 5.0
    expected = int((equity * risk) / (price * trail / 100))
    assert calculate_risk_qty(equity, risk, price, trail, "USD") == expected


def test_realised_loss_at_the_trail_stop_is_the_risk_fraction():
    """End-to-end: the position really does lose ~1% at the trail stop."""
    price, trail = 100.0, 5.0
    qty = calculate_risk_qty(EQUITY, 0.01, price, trail, "USD")
    loss = qty * (price * trail / 100)
    assert loss == pytest.approx(EQUITY * 0.01, rel=0.01)


# ── the PR B interaction: what if only the emergency stop fires ──────

@pytest.mark.parametrize("trail,emergency,multiple", [
    (5.0, 10.0, 2.0),      # SGLN / BARC shape
    (1.0, 5.0, 5.0),       # ANTO shape — the dangerous one
    (1.5, 5.0, 10.0 / 3),  # SHEL shape
])
def test_loss_if_only_the_broker_emergency_stop_fires(trail, emergency,
                                                      multiple):
    """Sized on the trail, stopped at the emergency level = a bigger loss.

    The broker-held stop from PR B sits at the emergency level, which is wider
    than the trail the position was sized against. If the synthetic layer is
    dead (process/host death) and only the broker stop fires, the realised loss
    scales by emergency_pct / trail_pct.
    """
    price = 100.0
    qty = calculate_risk_qty(EQUITY, 0.01, price, trail, "USD")

    loss_at_trail = qty * (price * trail / 100)
    loss_at_emergency = qty * (price * emergency / 100)

    assert loss_at_emergency / loss_at_trail == pytest.approx(multiple)
    assert loss_at_emergency == pytest.approx(
        EQUITY * 0.01 * multiple, rel=0.01)


# ── risk budget is a hard limit ──────────────────────────────────────

def test_returns_zero_rather_than_breaching_the_budget():
    """One share would cost more than the whole risk budget."""
    qty = calculate_risk_qty(1_000.0, 0.01, 1_000.0, 5.0, "USD")
    assert qty == 0


def test_zero_qty_is_rejected_by_the_pre_order_gate():
    """Returning 0 is safe because the gate refuses a non-positive qty."""
    with pytest.raises(OrderValidationError):
        validate_order("AAPL", 0, 100.0, "BUY", "USD", {}, 0, 0.0, 0.0)


@pytest.mark.parametrize("equity,risk,price,trail", [
    (0.0, 0.01, 100.0, 5.0),
    (-1.0, 0.01, 100.0, 5.0),
    (250_000.0, 0.0, 100.0, 5.0),
    (250_000.0, 0.01, 0.0, 5.0),
    (250_000.0, 0.01, 100.0, 0.0),
])
def test_degenerate_inputs_size_nothing(equity, risk, price, trail):
    assert calculate_risk_qty(equity, risk, price, trail, "USD") == 0


# ── respects the pre-order validation gate ───────────────────────────

def test_qty_is_capped_to_the_gate_max_qty():
    qty = calculate_risk_qty(EQUITY, 0.01, 100.0, 5.0, "USD", max_qty=100)
    assert qty == 100


def test_qty_is_capped_to_the_gate_max_notional():
    qty = calculate_risk_qty(EQUITY, 0.01, 100.0, 5.0, "USD",
                             max_notional=5_000)
    assert qty == 50


def test_a_capped_size_passes_the_gate_it_was_capped_for():
    settings = {"max_qty_per_order": 500, "max_notional_per_order": 5_000}
    qty = calculate_risk_qty(
        EQUITY, 0.01, 100.0, 5.0, "USD",
        max_qty=settings["max_qty_per_order"],
        max_notional=settings["max_notional_per_order"])
    validate_order("AAPL", qty, 100.0, "BUY", "USD", settings, 0, 0.0, 0.0)


# ── currency handling ────────────────────────────────────────────────

def test_pence_instruments_are_converted_before_sizing():
    assert native_price(12000.0, "GBP") == 120.0
    assert native_price(120.0, "USD") == 120.0


def test_fx_conversion_changes_size_in_the_right_direction():
    """A weaker instrument currency buys more shares per unit of base risk."""
    strong = calculate_risk_qty(EQUITY, 0.01, 100.0, 5.0, "USD", fx_to_base=1.0)
    weak = calculate_risk_qty(EQUITY, 0.01, 100.0, 5.0, "USD", fx_to_base=0.5)
    assert weak > strong


# ── risk fraction resolution ─────────────────────────────────────────

def test_risk_fraction_precedence():
    assert resolve_risk_fraction({}, {}) == DEFAULT_RISK_FRACTION
    assert resolve_risk_fraction({}, {"risk_fraction": 0.02}) == 0.02
    assert resolve_risk_fraction({"risk_fraction": 0.005},
                                 {"risk_fraction": 0.02}) == 0.005


# ── calculate_qty dispatch ───────────────────────────────────────────

def test_live_equity_selects_risk_sizing():
    inst = {"symbol": "AAPL", "currency": "USD", "trail_stop_pct": 5.0}
    assert calculate_qty(inst, 100.0, 1000.0, equity=EQUITY) == 500


def test_no_equity_is_the_backtest_path_not_a_live_fallback():
    """backtest/simulator.py has no broker to ask, so it sizes by notional.

    In *live* trading this branch is unreachable: layer1 blocks new entries
    outright when equity cannot be read (see the entry-gate tests below), so
    equal-notional never silently substitutes for risk sizing.
    """
    inst = {"symbol": "AAPL", "currency": "USD", "trail_stop_pct": 5.0}
    assert calculate_qty(inst, 100.0, 1000.0, equity=None) == 10


def test_equal_notional_behaviour_is_unchanged_for_the_simulator():
    """Regression guard: backtests must keep sizing exactly as before."""
    inst = {"symbol": "AAPL", "currency": "USD"}
    assert calculate_qty(inst, 250.0, 1000.0) == 4
    assert calculate_qty({"symbol": "X", "qty": 7}, 100.0, None) == 7


# ── live entry gate when equity cannot be read ───────────────────────
#
# ActiveTrading is built via object.__new__ so __init__ -- which would
# construct a real PositionTracker against the live positions.db path -- never
# runs. Only the attributes _can_enter reads are populated.

class _StubReconciler:
    def is_blocked(self, symbol):
        return False


def _entry_gate(equity_unavailable):
    from types import SimpleNamespace

    from bot.layer1 import ActiveTrading

    bot = object.__new__(ActiveTrading)
    bot.reconciler = _StubReconciler()
    bot.cfg = SimpleNamespace(max_open_positions=5, max_entries_per_cycle=3)
    bot._open_count = 0
    bot._entries_this_cycle = 0
    bot._equity_unavailable = equity_unavailable
    return bot


def test_unreadable_equity_blocks_new_entries():
    """No fallback model: an unreadable account admits no new risk."""
    assert _entry_gate(True)._can_enter("AAPL") is False


def test_readable_equity_leaves_entries_alone():
    assert _entry_gate(False)._can_enter("AAPL") is True


def test_equity_block_applies_to_every_symbol():
    """Unlike PR A's per-symbol divergence block, this one is account-wide."""
    gate = _entry_gate(True)
    assert all(gate._can_enter(s) is False for s in ("AAPL", "MSFT", "BARC"))


def test_entry_gate_defaults_to_permitting_when_flag_absent():
    """A partially-built object must not accidentally block all trading."""
    gate = _entry_gate(False)
    del gate._equity_unavailable
    assert gate._can_enter("AAPL") is True
