"""Dynamic Universe R2C — combined FX-normalized sizing + open-book heat NEW-entry gate.

Ties ``bot.universe.sizing`` (BLOCKER-S) and ``bot.universe.portfolio_heat`` (P3-5) into one
fail-closed decision for a single hypothetical NEW entry, and produces the deterministic
evidence fields for the optional schema-v7 ``risk_evaluation`` audit row.

Gate order (each step fail-closed; first failure short-circuits):
    1. FX-normalized sizing (entry/stop/currencies/equity/fresh-or-same-currency FX → qty)
    2. authoritative portfolio snapshot fetch (broker-free, injected)
    3. inherited/open-book post-trade heat (existing positions + open orders + proposed risk)

This is a NEW-ENTRY admissibility decision ONLY: it never forces liquidation, never alters an
existing position, and never calls a broker/provider beyond the two injected, broker-free seams.
It is consulted only by the DEFAULT-OFF evaluator gate; with the feature/flag off it is never
reached and neither provider is called.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from bot.universe import params
from bot.universe.models import Reason
from bot.universe.portfolio_heat import (
    HeatResult, PortfolioRiskProvider, PortfolioRiskSnapshot, evaluate_heat,
)
from bot.universe.sizing import (
    BaseFxRateProvider, SizingInputs, SizingResult, size_position,
)


@dataclass(frozen=True)
class RiskDecision:
    """Outcome of the combined sizing+heat gate. ``ok`` True → the new entry is admissible at
    ``qty`` shares; False → ``reason`` is a deterministic fail-closed code. All money values are
    base-currency Decimals; the fields double as the schema-v7 ``risk_evaluation`` evidence."""
    ok: bool
    reason: Optional[str] = None
    decision: str = "BLOCK"                       # "ALLOW" | "BLOCK"
    qty: Optional[int] = None
    canonical_instrument_id: Optional[str] = None
    instrument_uid: Optional[str] = None
    listing_uid: Optional[str] = None
    base_currency: Optional[str] = None
    instrument_currency: Optional[str] = None
    fx_rate: Optional[Decimal] = None
    fx_pair: Optional[str] = None
    fx_source: Optional[str] = None
    proposed_risk_base: Optional[Decimal] = None
    proposed_notional_base: Optional[Decimal] = None
    existing_heat_base: Optional[Decimal] = None
    post_trade_heat_base: Optional[Decimal] = None
    limit_base: Optional[Decimal] = None
    sizing: Optional[SizingResult] = None
    heat: Optional[HeatResult] = None


def _fetch_snapshot(provider: Optional[PortfolioRiskProvider],
                    evaluation_time) -> Optional[PortfolioRiskSnapshot]:
    """Fetch a snapshot from the injected provider, fail-closed. No provider / any exception
    → None (the heat gate then blocks with portfolio_snapshot_missing). Never calls a broker."""
    if provider is None:
        return None
    try:
        return provider.snapshot(evaluation_time=evaluation_time)
    except Exception:
        return None


def evaluate_entry_risk(
    inputs: SizingInputs,
    *,
    evaluation_time: Optional[datetime],
    evaluation_date,
    fx_provider: Optional[BaseFxRateProvider] = None,
    portfolio_provider: Optional[PortfolioRiskProvider] = None,
    max_fx_rate_age_seconds: int = params.MAX_FX_RATE_AGE_SECONDS,
    max_portfolio_heat_pct: object = params.MAX_PORTFOLIO_HEAT,
    absolute_limit_base: object = None,
) -> RiskDecision:
    """Run the full FX-normalized sizing + inherited/open-book heat gate for one NEW entry."""
    base = inputs.base_currency
    inst = inputs.instrument_currency

    # ── 1. FX-normalized sizing ───────────────────────────────────────
    sizing = size_position(inputs, evaluation_time=evaluation_time, fx_provider=fx_provider,
                           max_fx_rate_age_seconds=max_fx_rate_age_seconds)
    if not sizing.ok:
        return RiskDecision(
            ok=False, reason=sizing.reason, canonical_instrument_id=inputs.canonical_instrument_id,
            instrument_uid=inputs.instrument_uid, listing_uid=inputs.listing_uid,
            base_currency=sizing.base_currency or base, instrument_currency=sizing.instrument_currency or inst,
            fx_rate=sizing.fx_rate, fx_pair=sizing.fx_pair, fx_source=sizing.fx_source,
            sizing=sizing)

    # ── 2. authoritative portfolio snapshot (broker-free, injected) ───
    snapshot = _fetch_snapshot(portfolio_provider, evaluation_time)

    # ── 3. inherited / open-book post-trade heat ──────────────────────
    heat = evaluate_heat(
        snapshot, sizing.risk_base, base_currency=sizing.base_currency,
        evaluation_time=evaluation_time, evaluation_date=evaluation_date,
        max_portfolio_heat_pct=max_portfolio_heat_pct, absolute_limit_base=absolute_limit_base)

    decision_common = dict(
        canonical_instrument_id=inputs.canonical_instrument_id,
        instrument_uid=inputs.instrument_uid, listing_uid=inputs.listing_uid,
        base_currency=sizing.base_currency, instrument_currency=sizing.instrument_currency,
        fx_rate=sizing.fx_rate, fx_pair=sizing.fx_pair, fx_source=sizing.fx_source,
        qty=sizing.qty, proposed_risk_base=sizing.risk_base,
        proposed_notional_base=sizing.notional_base,
        existing_heat_base=heat.existing_heat_base, post_trade_heat_base=heat.post_trade_heat_base,
        limit_base=heat.limit_base, sizing=sizing, heat=heat)

    if not heat.ok:
        return RiskDecision(ok=False, reason=heat.reason, decision="BLOCK", **decision_common)
    return RiskDecision(ok=True, reason=Reason.ELIGIBLE, decision="ALLOW", **decision_common)
