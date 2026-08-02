"""Dynamic Universe R2C — FX-normalized position sizing (BLOCKER-S).

All dynamic-universe sizing/risk/notional is normalized into the ACCOUNT BASE CURRENCY
before comparison to limits. USD is NEVER assumed; a cross-currency instrument is NEVER
sized with an implicit 1.0 FX rate. This is a DISTINCT concern from the USD-eligibility
normalization in ``bot.universe.fx`` (which converts price/ADV20 to a fixed USD threshold
currency for structural eligibility). Sizing here targets the configurable account base
currency and uses deterministic ``decimal.Decimal`` arithmetic — no float in the money path.

Design rules (all FAIL-CLOSED):
  * The to-base FX rate is INJECTED via a ``BaseFxRateProvider`` (a fixture / pre-materialised
    snapshot). This module makes NO broker, IBKR, IG, EODHD, or external-API call.
  * Same-currency instrument → FX rate is exactly Decimal(1) (no provider call).
  * Cross-currency instrument → a fresh, valid, currency-matched rate MUST be obtained from
    the provider, or the new entry is BLOCKED with a deterministic reason code. We never fall
    back to 1.0 and never default the base currency to USD.
  * FX freshness is wall-clock-SECONDS based against an INJECTED ``evaluation_time`` (order-intent
    time): a rate older than ``max_fx_rate_age_seconds`` is stale; a missing/zero/negative/
    non-finite/future rate is invalid; a returned rate whose currencies disagree with the
    request is a mismatch.
  * Quantity is floored (ROUND_DOWN — conservative, never over-sizes); risk/notional are
    rounded the conservative direction (UP) for the limit comparisons, so rounding can only
    make the gate stricter, never laxer.

FX convention: ``rate`` is BASE units per 1 unit of the INSTRUMENT currency
(e.g. instrument GBP, base USD → fx_pair 'GBPUSD', rate 1.25 → 1 GBP = 1.25 USD).
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_DOWN
from typing import Optional, Protocol, runtime_checkable

from bot.universe import params
from bot.universe.models import Reason

# Conservative quantization scale for reported base-currency money values (8 dp). Comparisons
# against limits use the rounded-UP value so rounding can only tighten the gate.
_MONEY_Q = Decimal("0.00000001")


# ── deterministic Decimal helpers ─────────────────────────────────────
def to_decimal(value) -> Optional[Decimal]:
    """Convert an int/float/str/Decimal to a finite Decimal, else None.

    Floats are routed through ``str`` (never ``Decimal(float)``) so a value like 0.1 does not
    acquire binary-float drift in the money path. Non-finite (NaN/inf) → None (fail-closed)."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, bool):          # bool is an int subclass; never a money value
        return None
    elif isinstance(value, (int,)):
        d = Decimal(value)
    elif isinstance(value, float):
        d = Decimal(str(value))
    elif isinstance(value, str):
        try:
            d = Decimal(value.strip())
        except (InvalidOperation, ValueError):
            return None
    else:
        return None
    if not d.is_finite():
        return None
    return d


def _money_up(d: Decimal) -> Decimal:
    """Quantize a base-currency amount UP (conservative for limit comparisons)."""
    return d.quantize(_MONEY_Q, rounding=ROUND_CEILING)


def _floor_int(d: Decimal) -> int:
    """Floor a Decimal to an int (ROUND_DOWN — conservative share count)."""
    return int(d.to_integral_value(rounding=ROUND_DOWN))


# ── injected, broker-free to-base FX seam ─────────────────────────────
@dataclass(frozen=True)
class FxRate:
    """A to-BASE FX rate and the provenance needed to enforce freshness / mismatch.

    ``rate`` = BASE units per 1 unit of ``instrument_currency``. ``as_of`` is the rate's
    timestamp (fx_rate_timestamp); the caller validates it against an injected evaluation_time.
    ``source`` (fx_rate_source) is recorded for the audit trail."""
    instrument_currency: str
    base_currency: str
    rate: Decimal
    as_of: Optional[datetime]
    source: Optional[str] = None

    @property
    def fx_pair(self) -> str:
        return f"{self.instrument_currency}{self.base_currency}"


@runtime_checkable
class BaseFxRateProvider(Protocol):
    """Broker-free, INJECTED to-base FX seam.

    Implementations MUST NOT call a broker (IBKR/IG), EODHD, or any external FX API. They
    return a pre-materialised ``FxRate`` (rate in BASE per 1 instrument unit) or ``None`` when
    no acceptable rate exists. Implementations may raise; the resolver treats any exception as
    ``fx_provider_unavailable`` (fail-closed)."""
    def get_rate(self, *, instrument_currency: str, base_currency: str,
                 evaluation_time: datetime) -> Optional[FxRate]:
        ...


@dataclass(frozen=True)
class FxResolution:
    """Result of resolving a to-base FX rate. On success ``ok`` is True and ``rate`` is a
    positive Decimal (BASE per 1 instrument unit) with provenance; on failure ``ok`` is False
    and ``reason`` is a deterministic fail-closed code (``rate`` is None — never substituted)."""
    ok: bool
    reason: Optional[str] = None
    instrument_currency: Optional[str] = None
    base_currency: Optional[str] = None
    rate: Optional[Decimal] = None
    fx_pair: Optional[str] = None
    as_of: Optional[datetime] = None
    source: Optional[str] = None


def _norm_ccy(c) -> Optional[str]:
    if c is None:
        return None
    s = str(c).strip().upper()
    return s or None


def resolve_to_base_fx(
    *,
    instrument_currency,
    base_currency,
    evaluation_time: Optional[datetime],
    fx_provider: Optional[BaseFxRateProvider],
    max_fx_rate_age_seconds: int = params.MAX_FX_RATE_AGE_SECONDS,
) -> FxResolution:
    """Resolve the to-base FX multiplier (BASE per 1 instrument unit), fail-closed.

    same currency → Decimal(1) (no provider call); cross-currency → a fresh, valid,
    currency-matched provider rate, else a fail-closed reason. Never returns a default 1.0
    fallback for a cross-currency pair and never assumes USD."""
    base = _norm_ccy(base_currency)
    inst = _norm_ccy(instrument_currency)
    if base is None or inst is None:
        return FxResolution(ok=False, reason=Reason.SIZING_CURRENCY_UNRESOLVED,
                            instrument_currency=inst, base_currency=base)

    if inst == base:
        return FxResolution(ok=True, instrument_currency=inst, base_currency=base,
                            rate=Decimal(1), fx_pair=f"{inst}{base}",
                            as_of=evaluation_time, source="same_currency")

    if fx_provider is None:
        return FxResolution(ok=False, reason=Reason.FX_PROVIDER_UNAVAILABLE,
                            instrument_currency=inst, base_currency=base)
    try:
        quote = fx_provider.get_rate(instrument_currency=inst, base_currency=base,
                                     evaluation_time=evaluation_time)
    except Exception:
        return FxResolution(ok=False, reason=Reason.FX_PROVIDER_UNAVAILABLE,
                            instrument_currency=inst, base_currency=base)
    if quote is None:
        return FxResolution(ok=False, reason=Reason.FX_RATE_MISSING,
                            instrument_currency=inst, base_currency=base)

    q_inst = _norm_ccy(getattr(quote, "instrument_currency", None))
    q_base = _norm_ccy(getattr(quote, "base_currency", None))
    if q_inst != inst or q_base != base:
        return FxResolution(ok=False, reason=Reason.FX_CURRENCY_MISMATCH,
                            instrument_currency=inst, base_currency=base,
                            source=getattr(quote, "source", None))

    rate = to_decimal(getattr(quote, "rate", None))
    as_of = getattr(quote, "as_of", None)
    source = getattr(quote, "source", None)
    if rate is None or rate <= 0:
        return FxResolution(ok=False, reason=Reason.FX_RATE_INVALID,
                            instrument_currency=inst, base_currency=base,
                            fx_pair=f"{inst}{base}", as_of=as_of, source=source)
    # freshness: need a timestamp; never a FUTURE rate; not older than the window.
    if not isinstance(as_of, datetime) or not isinstance(evaluation_time, datetime):
        return FxResolution(ok=False, reason=Reason.FX_RATE_INVALID,
                            instrument_currency=inst, base_currency=base,
                            fx_pair=f"{inst}{base}", as_of=as_of if isinstance(as_of, datetime) else None,
                            source=source)
    age = _age_seconds(as_of, evaluation_time)
    if age is None or age < 0:                                   # future-dated / tz-incomparable
        return FxResolution(ok=False, reason=Reason.FX_RATE_INVALID,
                            instrument_currency=inst, base_currency=base,
                            fx_pair=f"{inst}{base}", as_of=as_of, source=source)
    if age > int(max_fx_rate_age_seconds):
        return FxResolution(ok=False, reason=Reason.FX_RATE_STALE,
                            instrument_currency=inst, base_currency=base,
                            fx_pair=f"{inst}{base}", as_of=as_of, source=source)
    return FxResolution(ok=True, instrument_currency=inst, base_currency=base,
                        rate=rate, fx_pair=f"{inst}{base}", as_of=as_of, source=source)


def _age_seconds(as_of: datetime, now: datetime) -> Optional[float]:
    """Seconds between ``as_of`` and ``now`` (positive when as_of is in the past). Returns None
    if the two datetimes are not comparable (one tz-aware, one naive)."""
    try:
        return (now - as_of).total_seconds()
    except TypeError:
        return None


# ── sizing inputs / result ────────────────────────────────────────────
@dataclass(frozen=True)
class SizingInputs:
    """Inputs for a single hypothetical NEW-entry sizing decision. Prices are in the
    INSTRUMENT (listing) currency, major unit; equity is in the BASE currency."""
    base_currency: Optional[str]
    instrument_currency: Optional[str]
    entry_price: object                       # instrument currency
    equity_base: object                       # base currency
    stop_price: object = None                 # instrument currency (long: < entry)
    stop_distance: object = None              # instrument currency (entry - stop), if no stop_price
    risk_per_trade_pct: object = params.RISK_PER_TRADE
    max_notional_pct: object = params.MAX_NOTIONAL_PCT
    side: str = "LONG"
    canonical_instrument_id: Optional[str] = None
    instrument_uid: Optional[str] = None
    listing_uid: Optional[str] = None


@dataclass(frozen=True)
class SizingResult:
    """FX-normalized sizing outcome. On success ``ok`` is True with a positive integer ``qty``
    and base-currency ``risk_base`` / ``notional_base``; on failure ``ok`` is False and
    ``reason`` is a deterministic fail-closed code (qty/risk/notional stay None)."""
    ok: bool
    reason: Optional[str] = None
    qty: Optional[int] = None
    risk_base: Optional[Decimal] = None
    notional_base: Optional[Decimal] = None
    # provenance / intermediate values (recorded for the audit trail)
    base_currency: Optional[str] = None
    instrument_currency: Optional[str] = None
    fx_rate: Optional[Decimal] = None
    fx_pair: Optional[str] = None
    fx_source: Optional[str] = None
    entry_price: Optional[Decimal] = None
    stop_distance: Optional[Decimal] = None
    risk_budget_base: Optional[Decimal] = None
    notional_cap_base: Optional[Decimal] = None


def size_position(
    inputs: SizingInputs,
    *,
    evaluation_time: Optional[datetime] = None,
    fx_provider: Optional[BaseFxRateProvider] = None,
    max_fx_rate_age_seconds: int = params.MAX_FX_RATE_AGE_SECONDS,
    fx_resolution: Optional[FxResolution] = None,
) -> SizingResult:
    """Compute an FX-normalized hypothetical NEW-entry size, fail-closed.

    Either inject a pre-resolved ``fx_resolution`` or pass ``fx_provider`` + ``evaluation_time``
    so this resolves the rate itself. Every required input (entry price, stop, currencies,
    equity, fresh/same-currency FX) must be present and valid; a positive integer quantity must
    fit within BOTH the base-currency per-trade risk limit and the base-currency notional cap.
    """
    base = _norm_ccy(inputs.base_currency)
    inst = _norm_ccy(inputs.instrument_currency)
    if base is None or inst is None:
        return SizingResult(ok=False, reason=Reason.SIZING_CURRENCY_UNRESOLVED,
                            base_currency=base, instrument_currency=inst)

    entry = to_decimal(inputs.entry_price)
    if entry is None or entry <= 0:
        return SizingResult(ok=False, reason=Reason.SIZING_PRICE_MISSING,
                            base_currency=base, instrument_currency=inst)

    # stop: explicit stop_distance wins; else derive from entry - stop_price (long).
    stop_dist = to_decimal(inputs.stop_distance)
    if stop_dist is None:
        sp = to_decimal(inputs.stop_price)
        if sp is not None:
            stop_dist = entry - sp
    if stop_dist is None or stop_dist <= 0:
        return SizingResult(ok=False, reason=Reason.SIZING_STOP_MISSING,
                            base_currency=base, instrument_currency=inst,
                            entry_price=entry)

    equity = to_decimal(inputs.equity_base)
    risk_pct = to_decimal(inputs.risk_per_trade_pct)
    notional_pct = to_decimal(inputs.max_notional_pct)
    if equity is None or equity <= 0 or risk_pct is None or risk_pct <= 0 \
            or notional_pct is None or notional_pct <= 0:
        return SizingResult(ok=False, reason=Reason.SIZING_INPUT_MISSING,
                            base_currency=base, instrument_currency=inst,
                            entry_price=entry, stop_distance=stop_dist)

    # resolve to-base FX (same-currency → 1; cross-currency → fresh provider rate).
    fx = fx_resolution if fx_resolution is not None else resolve_to_base_fx(
        instrument_currency=inst, base_currency=base, evaluation_time=evaluation_time,
        fx_provider=fx_provider, max_fx_rate_age_seconds=max_fx_rate_age_seconds)
    if not fx.ok:
        return SizingResult(ok=False, reason=fx.reason,
                            base_currency=base, instrument_currency=inst,
                            entry_price=entry, stop_distance=stop_dist)
    rate = fx.rate

    # normalize into base currency.
    entry_base = entry * rate
    stop_dist_base = stop_dist * rate
    risk_budget_base = equity * risk_pct
    notional_cap_base = equity * notional_pct

    # quantity must fit BOTH the per-trade risk budget and the notional cap (floored).
    qty_risk = _floor_int(risk_budget_base / stop_dist_base)
    qty_notional = _floor_int(notional_cap_base / entry_base)
    common = dict(base_currency=base, instrument_currency=inst, fx_rate=rate,
                  fx_pair=fx.fx_pair, fx_source=fx.source, entry_price=entry,
                  stop_distance=stop_dist, risk_budget_base=_money_up(risk_budget_base),
                  notional_cap_base=_money_up(notional_cap_base))
    if qty_risk < 1:
        return SizingResult(ok=False, reason=Reason.SIZING_RISK_EXCEEDS_LIMIT, **common)
    if qty_notional < 1:
        return SizingResult(ok=False, reason=Reason.SIZING_NOTIONAL_EXCEEDS_LIMIT, **common)
    qty = min(qty_risk, qty_notional)
    if qty < 1:
        return SizingResult(ok=False, reason=Reason.SIZING_QUANTITY_INVALID, **common)

    risk_base = _money_up(Decimal(qty) * stop_dist_base)
    notional_base = _money_up(Decimal(qty) * entry_base)
    return SizingResult(ok=True, qty=qty, risk_base=risk_base, notional_base=notional_base,
                        **common)
