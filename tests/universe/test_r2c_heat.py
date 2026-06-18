"""R2C P3-5 — inherited / open-book portfolio heat.

post_trade_heat = existing open-position risk + open-order/pending-intent risk + proposed risk,
compared against MAX_PORTFOLIO_HEAT × base-currency equity. Every freshness / completeness /
equity failure blocks the NEW entry with a deterministic fail-closed reason code. Broker-free
fixtures only; the gate never forces liquidation.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

from bot.universe.models import Reason
from bot.universe.portfolio_heat import PositionRisk, evaluate_heat
from tests.universe._fixtures import fresh_snapshot, pos_risk

NOW = datetime(2026, 6, 18, 12, 0, 0)
TD = date(2026, 6, 18)
# limit = MAX_PORTFOLIO_HEAT (0.025) × equity 1,000,000 = 25,000 base.


def _heat(snapshot, proposed, **kw):
    return evaluate_heat(snapshot, proposed, base_currency="USD", evaluation_time=NOW,
                         evaluation_date=TD, **kw)


def _snap(**kw):
    return fresh_snapshot(evaluation_time=NOW, trading_date=TD, **kw)


# ── components counted ─────────────────────────────────────────────────
def test_existing_open_positions_counted():
    r = _heat(_snap(positions=[pos_risk("10000")]), "5000")
    assert r.ok and r.existing_position_heat_base == Decimal("10000")
    assert r.post_trade_heat_base == Decimal("15000")


def test_open_orders_counted():
    r = _heat(_snap(orders=[pos_risk("8000")]), "1000")
    assert r.ok and r.open_order_heat_base == Decimal("8000")
    assert r.existing_heat_base == Decimal("8000")


def test_pending_intents_and_positions_both_counted():
    r = _heat(_snap(positions=[pos_risk("6000")], orders=[pos_risk("4000")]), "2000")
    assert r.ok and r.existing_heat_base == Decimal("10000")        # 6000 + 4000
    assert r.post_trade_heat_base == Decimal("12000")               # + proposed 2000


def test_proposed_trade_added_to_heat():
    base = _heat(_snap(positions=[pos_risk("5000")]), "0")
    with_new = _heat(_snap(positions=[pos_risk("5000")]), "3000")
    assert with_new.post_trade_heat_base - base.post_trade_heat_base == Decimal("3000")


# ── limit gates ─────────────────────────────────────────────────────────
def test_post_trade_heat_over_limit_blocks():
    # existing 20000 (< 25000 limit) + proposed 10000 = 30000 > limit → portfolio_heat_exceeded.
    r = _heat(_snap(positions=[pos_risk("20000")]), "10000")
    assert not r.ok and r.reason == Reason.PORTFOLIO_HEAT_EXCEEDED


def test_inherited_book_alone_over_limit_blocks():
    # the inherited book (26000) already exceeds the limit → open_book_heat_exceeded.
    r = _heat(_snap(positions=[pos_risk("26000")]), "0")
    assert not r.ok and r.reason == Reason.OPEN_BOOK_HEAT_EXCEEDED


def test_heat_exactly_at_limit_passes():
    r = _heat(_snap(positions=[pos_risk("20000")]), "5000")        # 25000 == limit
    assert r.ok and r.post_trade_heat_base == Decimal("25000")


# ── snapshot freshness / presence ───────────────────────────────────────
def test_missing_snapshot_blocks():
    r = _heat(None, "1000")
    assert not r.ok and r.reason == Reason.PORTFOLIO_SNAPSHOT_MISSING


def test_stale_snapshot_blocks():
    stale = fresh_snapshot(evaluation_time=NOW - timedelta(seconds=901), trading_date=TD)
    r = _heat(stale, "1000")
    assert not r.ok and r.reason == Reason.PORTFOLIO_SNAPSHOT_STALE


def test_snapshot_date_mismatch_blocks():
    r = _heat(fresh_snapshot(evaluation_time=NOW, trading_date=date(2026, 6, 17)), "1000")
    assert not r.ok and r.reason == Reason.PORTFOLIO_SNAPSHOT_DATE_MISMATCH


def test_open_order_older_than_15_minutes_blocks():
    old_order = pos_risk("100", observed_at=NOW - timedelta(seconds=901))
    r = _heat(_snap(orders=[old_order]), "1000")
    assert not r.ok and r.reason == Reason.PORTFOLIO_OPEN_ORDER_SNAPSHOT_STALE


def test_fresh_open_order_within_15_minutes_passes():
    ok_order = pos_risk("100", observed_at=NOW - timedelta(seconds=899))
    r = _heat(_snap(orders=[ok_order]), "1000")
    assert r.ok


# ── equity gates ─────────────────────────────────────────────────────────
def test_missing_equity_blocks_when_needed():
    r = _heat(_snap(equity=None), "1000")
    assert not r.ok and r.reason == Reason.PORTFOLIO_EQUITY_MISSING


def test_invalid_equity_blocks():
    for bad in ("0", "-100"):
        r = _heat(_snap(equity=bad), "1000")
        assert not r.ok and r.reason == Reason.PORTFOLIO_EQUITY_INVALID


def test_stale_equity_blocks():
    r = _heat(_snap(equity="1000000", equity_as_of=NOW - timedelta(seconds=901)), "1000")
    assert not r.ok and r.reason == Reason.PORTFOLIO_EQUITY_STALE


def test_absolute_limit_does_not_require_equity():
    # an absolute base-currency limit needs no equity; existing 10000 + 2000 <= 20000.
    r = _heat(_snap(equity=None, positions=[pos_risk("10000")]), "2000", absolute_limit_base="20000")
    assert r.ok and r.limit_base == Decimal("20000")


# ── currency / identity completeness ─────────────────────────────────────
def test_currency_incomplete_item_blocks():
    bad = PositionRisk(currency=None, normalized_risk_base="5000", instrument_uid="x")
    r = _heat(_snap(positions=[bad]), "1000")
    assert not r.ok and r.reason == Reason.PORTFOLIO_CURRENCY_UNRESOLVED


def test_snapshot_base_currency_mismatch_blocks():
    r = _heat(fresh_snapshot(evaluation_time=NOW, trading_date=TD, base_currency="EUR"), "1000")
    assert not r.ok and r.reason == Reason.PORTFOLIO_CURRENCY_UNRESOLVED


def test_identity_incomplete_item_blocks_when_it_affects_heat():
    # a contributing (risk > 0) item with NO identity → identity unresolved.
    bad = PositionRisk(currency="USD", normalized_risk_base="5000",
                       instrument_uid=None, canonical_instrument_id=None)
    r = _heat(_snap(positions=[bad]), "1000")
    assert not r.ok and r.reason == Reason.PORTFOLIO_IDENTITY_UNRESOLVED


def test_zero_risk_item_without_identity_does_not_block():
    # an item that contributes NO heat need not carry identity (it does not affect heat).
    benign = PositionRisk(currency="USD", normalized_risk_base="0",
                          instrument_uid=None, canonical_instrument_id=None)
    r = _heat(_snap(positions=[benign]), "1000")
    assert r.ok
