"""Dynamic Universe R2C — inherited / open-book portfolio heat (P3-5).

A dynamic-universe NEW entry must account for ALL existing portfolio risk — open positions
AND open orders / pending intents — before it is accepted. The pre-R2C contention check only
accumulated heat WITHIN a single shadow run and ignored the inherited book (the P3-5 defect).
This module computes post-trade heat against an INJECTED, authoritative
``PortfolioRiskProvider`` snapshot, fail-closed, in deterministic ``Decimal`` arithmetic.

    post_trade_heat_base = existing_open_position_risk
                         + open_order / pending_intent_risk
                         + proposed_new_trade_risk           (all normalized to base currency)

compared against a limit expressed as a percentage of base-currency equity
(``MAX_PORTFOLIO_HEAT`` × equity_base) or an explicit absolute base-currency limit.

Design rules (all FAIL-CLOSED):
  * The snapshot is INJECTED (a fixture / non-broker materialised view). This module makes NO
    broker/IBKR/IG/EODHD/live-market call.
  * Freshness is enforced against an INJECTED ``evaluation_time`` (never the wall clock):
      - the daily snapshot date must equal the evaluation trading date;
      - the snapshot / open-order observation age must be ≤ 15 min (order-intent freshness);
      - equity (when a percentage limit needs it) must be present, positive, and ≤ 15 min old.
  * A missing / stale / date-mismatched / currency-incomplete / identity-incomplete snapshot,
    or missing/stale/invalid equity, BLOCKS the new entry with a deterministic reason code.
  * Heat gates apply to NEW ENTRIES ONLY. This module NEVER forces liquidation and NEVER alters
    an existing position — it only decides whether one more hypothetical entry is admissible.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_CEILING
from typing import Optional, Protocol, runtime_checkable

from bot.universe import params
from bot.universe.models import Reason
from bot.universe.sizing import to_decimal

_MONEY_Q = Decimal("0.00000001")


def _money_up(d: Decimal) -> Decimal:
    return d.quantize(_MONEY_Q, rounding=ROUND_CEILING)


def _norm_ccy(c) -> Optional[str]:
    if c is None:
        return None
    s = str(c).strip().upper()
    return s or None


def _as_date(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v.strip())
        except ValueError:
            return None
    return None


def _age_seconds(as_of: datetime, now: datetime) -> Optional[float]:
    try:
        return (now - as_of).total_seconds()
    except TypeError:
        return None


# ── snapshot model ────────────────────────────────────────────────────
@dataclass(frozen=True)
class PositionRisk:
    """One existing open position OR one open order / pending intent contributing heat.

    ``normalized_risk_base`` / ``normalized_notional_base`` are the AUTHORITATIVE base-currency
    values from the provider (the provider, which owns the multi-currency book, is responsible
    for the FX normalization of its own positions). Raw fields (quantity/side/prices/stop/
    currency) are carried for the audit trail and completeness checks. ``observed_at`` lets an
    open-order/pending-intent carry its own freshness timestamp (≤ 15 min)."""
    currency: Optional[str] = None
    normalized_risk_base: object = None
    normalized_notional_base: object = None
    quantity: object = None
    side: Optional[str] = None
    entry_price: object = None
    mark_price: object = None
    stop_price: object = None
    stop_distance: object = None
    instrument_uid: Optional[str] = None
    listing_uid: Optional[str] = None
    canonical_instrument_id: Optional[str] = None
    symbol: Optional[str] = None
    observed_at: Optional[datetime] = None


@dataclass(frozen=True)
class PortfolioRiskSnapshot:
    """Authoritative, broker-free, read-only portfolio risk observation (task §3)."""
    base_currency: Optional[str] = None
    open_positions: tuple = ()
    open_orders: tuple = ()                      # open orders / pending intents
    equity_base: object = None
    equity_as_of: Optional[datetime] = None
    snapshot_date: object = None                 # daily snapshot trading date
    snapshot_timestamp: Optional[datetime] = None
    source: Optional[str] = None


@runtime_checkable
class PortfolioRiskProvider(Protocol):
    """Broker-free, INJECTED authoritative portfolio-risk seam (task §3).

    Implementations MUST NOT call a broker (IBKR/IG), EODHD, a live market-data API, or read a
    live broker session. They return a pre-materialised ``PortfolioRiskSnapshot``. Implementations
    may raise; the gate treats any exception as ``portfolio_snapshot_missing`` (fail-closed)."""
    def snapshot(self, *, evaluation_time: datetime) -> PortfolioRiskSnapshot:
        ...


# ── heat result ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class HeatResult:
    """Post-trade heat decision. On success ``ok`` is True with the base-currency heat
    components and the limit; on failure ``ok`` is False with a deterministic fail-closed code."""
    ok: bool
    reason: Optional[str] = None
    existing_position_heat_base: Optional[Decimal] = None
    open_order_heat_base: Optional[Decimal] = None
    existing_heat_base: Optional[Decimal] = None          # positions + open orders (inherited book)
    proposed_risk_base: Optional[Decimal] = None
    post_trade_heat_base: Optional[Decimal] = None
    limit_base: Optional[Decimal] = None
    equity_base: Optional[Decimal] = None
    base_currency: Optional[str] = None


def _sum_risk(items, base_currency) -> tuple:
    """Sum normalized base-currency risk across snapshot items, fail-closed.

    Returns (total:Decimal, reason_or_none). A contributing item with an unresolved currency or
    an absent/non-finite/negative normalized base risk → ``portfolio_currency_unresolved``; an
    item that contributes risk but carries no instrument identity → ``portfolio_identity_unresolved``
    (identity matters only when it affects heat)."""
    total = Decimal(0)
    for it in items:
        ccy = _norm_ccy(getattr(it, "currency", None))
        risk = to_decimal(getattr(it, "normalized_risk_base", None))
        if ccy is None:
            return total, Reason.PORTFOLIO_CURRENCY_UNRESOLVED
        if risk is None or risk < 0:
            # normalization not resolvable for a contributing item → fail closed.
            return total, Reason.PORTFOLIO_CURRENCY_UNRESOLVED
        if risk > 0:
            iuid = getattr(it, "instrument_uid", None)
            cid = getattr(it, "canonical_instrument_id", None)
            if not iuid and not cid:
                return total, Reason.PORTFOLIO_IDENTITY_UNRESOLVED
        total += risk
    return total, None


def validate_snapshot(
    snapshot: Optional[PortfolioRiskSnapshot],
    *,
    base_currency,
    evaluation_time: Optional[datetime],
    evaluation_date,
    require_equity: bool = True,
    max_snapshot_age_seconds: int = params.MAX_PORTFOLIO_SNAPSHOT_AGE_SECONDS,
    max_open_order_age_seconds: int = params.MAX_OPEN_ORDER_SNAPSHOT_AGE_SECONDS,
    max_equity_age_seconds: int = params.MAX_EQUITY_AGE_SECONDS,
) -> Optional[str]:
    """Validate snapshot presence / freshness / currency / equity. Returns None when usable,
    else a deterministic fail-closed reason code. Pure (no side effects, no provider call)."""
    if snapshot is None:
        return Reason.PORTFOLIO_SNAPSHOT_MISSING
    base = _norm_ccy(base_currency)
    snap_base = _norm_ccy(getattr(snapshot, "base_currency", None))
    if base is None or snap_base is None:
        return Reason.PORTFOLIO_CURRENCY_UNRESOLVED
    if snap_base != base:
        return Reason.PORTFOLIO_CURRENCY_UNRESOLVED

    # daily snapshot date must match the evaluation trading date.
    snap_date = _as_date(getattr(snapshot, "snapshot_date", None))
    eval_date = _as_date(evaluation_date)
    if snap_date is None or eval_date is None or snap_date != eval_date:
        return Reason.PORTFOLIO_SNAPSHOT_DATE_MISMATCH

    # entry-time snapshot freshness (≤ window; never future).
    ts = getattr(snapshot, "snapshot_timestamp", None)
    if not isinstance(ts, datetime) or not isinstance(evaluation_time, datetime):
        return Reason.PORTFOLIO_SNAPSHOT_STALE
    age = _age_seconds(ts, evaluation_time)
    if age is None or age < 0 or age > int(max_snapshot_age_seconds):
        return Reason.PORTFOLIO_SNAPSHOT_STALE

    # open-order / pending-intent freshness (each may carry its own observed_at).
    for it in getattr(snapshot, "open_orders", ()) or ():
        oa = getattr(it, "observed_at", None)
        if oa is None:
            continue                                     # falls under the snapshot-level window
        if not isinstance(oa, datetime):
            return Reason.PORTFOLIO_OPEN_ORDER_SNAPSHOT_STALE
        oage = _age_seconds(oa, evaluation_time)
        if oage is None or oage < 0 or oage > int(max_open_order_age_seconds):
            return Reason.PORTFOLIO_OPEN_ORDER_SNAPSHOT_STALE

    # equity (needed for a percentage-of-equity limit).
    if require_equity:
        eq = to_decimal(getattr(snapshot, "equity_base", None))
        if eq is None:
            return Reason.PORTFOLIO_EQUITY_MISSING
        if eq <= 0:
            return Reason.PORTFOLIO_EQUITY_INVALID
        eq_as_of = getattr(snapshot, "equity_as_of", None)
        if eq_as_of is not None:
            if not isinstance(eq_as_of, datetime):
                return Reason.PORTFOLIO_EQUITY_STALE
            eage = _age_seconds(eq_as_of, evaluation_time)
            if eage is None or eage < 0 or eage > int(max_equity_age_seconds):
                return Reason.PORTFOLIO_EQUITY_STALE
    return None


def evaluate_heat(
    snapshot: Optional[PortfolioRiskSnapshot],
    proposed_risk_base,
    *,
    base_currency,
    evaluation_time: Optional[datetime],
    evaluation_date,
    max_portfolio_heat_pct: object = params.MAX_PORTFOLIO_HEAT,
    absolute_limit_base: object = None,
    **freshness,
) -> HeatResult:
    """Compute post-trade heat and gate one hypothetical NEW entry, fail-closed.

    The limit is ``max_portfolio_heat_pct × equity_base`` unless ``absolute_limit_base`` is
    given (then equity is not required). If the inherited book (positions + open orders) ALREADY
    exceeds the limit → ``open_book_heat_exceeded``; if adding the proposed trade crosses it →
    ``portfolio_heat_exceeded``."""
    base = _norm_ccy(base_currency)
    use_pct = absolute_limit_base is None
    reason = validate_snapshot(
        snapshot, base_currency=base, evaluation_time=evaluation_time,
        evaluation_date=evaluation_date, require_equity=use_pct, **freshness)
    if reason is not None:
        return HeatResult(ok=False, reason=reason, base_currency=base)

    proposed = to_decimal(proposed_risk_base)
    if proposed is None or proposed < 0:
        return HeatResult(ok=False, reason=Reason.SIZING_QUANTITY_INVALID, base_currency=base)

    pos_heat, r1 = _sum_risk(getattr(snapshot, "open_positions", ()) or (), base)
    if r1 is not None:
        return HeatResult(ok=False, reason=r1, base_currency=base)
    order_heat, r2 = _sum_risk(getattr(snapshot, "open_orders", ()) or (), base)
    if r2 is not None:
        return HeatResult(ok=False, reason=r2, base_currency=base)

    existing = pos_heat + order_heat
    post = existing + proposed

    if use_pct:
        equity = to_decimal(getattr(snapshot, "equity_base", None))
        pct = to_decimal(max_portfolio_heat_pct)
        if pct is None or pct <= 0:
            return HeatResult(ok=False, reason=Reason.PORTFOLIO_HEAT_EXCEEDED, base_currency=base)
        limit = equity * pct
        equity_out = _money_up(equity)
    else:
        limit = to_decimal(absolute_limit_base)
        equity_out = None
        if limit is None or limit < 0:
            return HeatResult(ok=False, reason=Reason.PORTFOLIO_HEAT_EXCEEDED, base_currency=base)

    common = dict(
        existing_position_heat_base=_money_up(pos_heat),
        open_order_heat_base=_money_up(order_heat),
        existing_heat_base=_money_up(existing),
        proposed_risk_base=_money_up(proposed),
        post_trade_heat_base=_money_up(post),
        limit_base=_money_up(limit), equity_base=equity_out, base_currency=base)

    # conservative comparison: compare rounded-UP heat against the limit (rounding can only
    # tighten the gate). The inherited book alone over the limit is a distinct, stronger signal.
    if _money_up(existing) > _money_up(limit):
        return HeatResult(ok=False, reason=Reason.OPEN_BOOK_HEAT_EXCEEDED, **common)
    if _money_up(post) > _money_up(limit):
        return HeatResult(ok=False, reason=Reason.PORTFOLIO_HEAT_EXCEEDED, **common)
    return HeatResult(ok=True, **common)
