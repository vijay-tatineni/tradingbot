"""Dynamic Universe v1 — currency normalisation for eligibility (P2-2).

Eligibility thresholds are USD-denominated (``MIN_PRICE_USD`` = $10-equivalent,
``MIN_ADV20_USD`` = $20M-equivalent). Instruments quote in their own local currency
and price unit (USD dollars, GBP pounds, GBX pence, EUR euros, …). Comparing a local
value directly against a USD threshold is wrong for any non-USD instrument, so this
module converts *local* price / 20-day average dollar-volume into *USD-normalised*
values before the threshold predicate runs.

Design rules (all FAIL-CLOSED — see ``normalize_to_usd``):
  * The FX rate is INJECTED via an ``FxRateProvider`` (a fixture / pre-materialised
    table). This module makes NO broker, IBKR, IG, EODHD, or external-API call.
  * USD is the only currency whose rate is assumed (1.0); every other currency MUST
    obtain a validated rate from the provider, or eligibility is blocked.
  * Pence (GBX) is GBP ÷ 100, applied BEFORE the GBP→USD rate. GBX is expressed as
    ``currency='GBP', price_unit='GBX'`` — the only unambiguous spelling. The literal
    token ``'GBX'`` in the *currency* field is rejected as ambiguous (could it be
    GBP-with-a-default-unit? a 100× error risk), so it never silently mis-converts.
  * Any missing / zero / negative / NaN / infinite / future / stale rate, unknown
    currency, unknown price unit, or non-finite normalised result blocks eligibility
    with a deterministic reason code. We never fall back to the local value.

The conversion is the SAME multiplier for price and ADV20 (both are
``price-unit × shares``); share volume itself is never scaled.
"""
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Optional, Protocol, runtime_checkable

from bot.universe.models import Reason

# Currencies whose to-USD rate this layer can validate (USD assumed 1.0; others via
# the injected provider). An unrecognised currency fails closed (currency_unknown).
SUPPORTED_CURRENCIES = frozenset({"USD", "GBP", "EUR"})

# Quote units. MAJOR = the currency's major unit (USD dollars, GBP pounds, EUR euros).
# GBX = pence = GBP ÷ 100 (LSE convention). Only valid with currency='GBP'.
PRICE_UNITS = frozenset({"MAJOR", "GBX"})
_UNIT_FACTOR = {"MAJOR": 1.0, "GBX": 0.01}   # local-unit → major-unit multiplier

# Accepted FX staleness (calendar days between the rate's effective FX session and the
# instrument's completed trading date). The rate must be effective ON the trading date,
# or on a PRIOR valid FX session no more than this many days earlier (covers a weekend
# plus a single FX-market holiday: Fri rate used for a Mon trading date = 3 days). A
# rate effective AFTER the trading date (a future rate) is always rejected.
MAX_FX_STALENESS_DAYS = 4


@dataclass(frozen=True)
class FxQuote:
    """A to-USD FX rate and the FX session it is effective for.

    ``rate`` = USD per 1 *major* unit of the source currency (e.g. GBPUSD for GBP).
    ``as_of`` = the completed FX session the rate belongs to. The caller validates it
    against the instrument's trading date for freshness; this dataclass carries no
    policy. (The interface returns this richer value rather than a bare float because
    FX freshness cannot be enforced without the effective date — see P2-2.)
    """
    rate: float
    as_of: date


@runtime_checkable
class FxRateProvider(Protocol):
    """Broker-free, INJECTED to-USD FX rate seam.

    Implementations MUST NOT call a broker (IBKR/IG), EODHD, or any external FX API.
    They return a pre-materialised ``FxQuote`` from a fixture / snapshot / cached table,
    or ``None`` when no acceptable rate exists for the requested currency/date.
    """
    def get_to_usd_rate(self, currency: str, trading_date: date) -> Optional[FxQuote]:
        ...


@dataclass(frozen=True)
class Normalization:
    """Result of normalising a local price/ADV20 into USD.

    On success ``ok`` is True and the USD-normalised values + FX provenance are set.
    On failure ``ok`` is False and ``reason`` is a deterministic fail-closed code; the
    USD fields stay None so a caller can never accidentally use an unconverted value.
    """
    ok: bool
    reason: Optional[str] = None
    # provenance (always populated when resolvable, for the shadow audit record)
    currency: Optional[str] = None
    price_unit: Optional[str] = None
    price_local: Optional[float] = None
    adv20_local: Optional[float] = None
    fx_to_usd: Optional[float] = None
    fx_effective_date: Optional[date] = None
    # normalised outputs (only set when ok)
    price_usd: Optional[float] = None
    adv20_usd: Optional[float] = None


def _fail(reason: str, **prov) -> Normalization:
    return Normalization(ok=False, reason=reason, **prov)


def _to_date(value):
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def normalize_to_usd(
    *,
    price_local,
    adv20_local,
    currency,
    price_unit,
    trading_date,
    fx_provider: Optional[FxRateProvider],
    max_staleness_days: int = MAX_FX_STALENESS_DAYS,
) -> Normalization:
    """Convert a local price / 20-day dollar-volume to USD-normalised values.

    Returns a ``Normalization``. Every failure mode is fail-closed with a reason code
    in ``BLOCKING_REASONS``; the local value is never substituted for the USD value.
    """
    cur = (currency or "").strip().upper()
    unit_in = (price_unit or "").strip().upper() or None

    # 1. Currency must be a known ISO code. 'GBX' supplied as a *currency* is the
    #    pence/pounds ambiguity (it must be spelled currency='GBP', price_unit='GBX').
    if cur == "GBX":
        return _fail(Reason.GBX_GBP_UNIT_AMBIGUOUS, price_unit=unit_in)
    if cur not in SUPPORTED_CURRENCIES:
        return _fail(Reason.CURRENCY_UNKNOWN, currency=cur or None, price_unit=unit_in)

    # 2. Price unit. USD is unambiguously a major-unit quote (no pence convention here);
    #    an explicit non-MAJOR unit on USD is rejected rather than guessed.
    if cur == "USD":
        if unit_in is not None and unit_in != "MAJOR":
            return _fail(Reason.PRICE_UNIT_UNKNOWN, currency=cur, price_unit=unit_in)
        unit = "MAJOR"
    else:
        if unit_in is None or unit_in not in PRICE_UNITS:
            return _fail(Reason.PRICE_UNIT_UNKNOWN, currency=cur, price_unit=unit_in)
        # GBX (pence) is a GBP-only unit. GBX on any other currency is ambiguous.
        if unit_in == "GBX" and cur != "GBP":
            return _fail(Reason.GBX_GBP_UNIT_AMBIGUOUS, currency=cur, price_unit=unit_in)
        unit = unit_in
    unit_factor = _UNIT_FACTOR[unit]

    prov = dict(currency=cur, price_unit=unit,
                price_local=_as_float(price_local), adv20_local=_as_float(adv20_local))

    # 3. Local inputs must be finite. Price must be strictly positive; ADV20 may be 0
    #    (illiquid) but never negative/NaN/inf.
    p_local = _as_float(price_local)
    a_local = _as_float(adv20_local)
    if p_local is None or not isfinite(p_local) or p_local <= 0:
        return _fail(Reason.NORMALIZED_PRICE_INVALID, **prov)
    if a_local is None or not isfinite(a_local) or a_local < 0:
        return _fail(Reason.NORMALIZED_ADV20_INVALID, **prov)

    # 4. FX rate to USD (USD assumed 1.0 — no provider call; others must be validated).
    td = _to_date(trading_date)
    if cur == "USD":
        rate, eff = 1.0, td
    else:
        if fx_provider is None:
            return _fail(Reason.FX_CONVERSION_UNAVAILABLE, **prov)
        try:
            quote = fx_provider.get_to_usd_rate(cur, td)
        except Exception:
            return _fail(Reason.FX_CONVERSION_UNAVAILABLE, **prov)
        if quote is None:
            return _fail(Reason.FX_CONVERSION_UNAVAILABLE, **prov)
        rate = _as_float(getattr(quote, "rate", None))
        eff = _to_date(getattr(quote, "as_of", None))
        # rate validity: reject missing/NaN/inf/zero/negative
        if rate is None or not isfinite(rate) or rate <= 0:
            return _fail(Reason.FX_RATE_INVALID, fx_to_usd=rate, **prov)
        # freshness: need an effective date; never a FUTURE rate; not older than window
        if eff is None or td is None:
            return _fail(Reason.FX_RATE_INVALID, fx_to_usd=rate, **prov)
        if eff > td:
            return _fail(Reason.FX_RATE_INVALID, fx_to_usd=rate, fx_effective_date=eff, **prov)
        if (td - eff).days > int(max_staleness_days):
            return _fail(Reason.FX_RATE_STALE, fx_to_usd=rate, fx_effective_date=eff, **prov)

    # 5. Normalise. One multiplier for both price and ADV20 (price-unit → USD); volume
    #    (share count) is never scaled.
    m = unit_factor * rate
    price_usd = p_local * m
    adv20_usd = a_local * m
    if not isfinite(price_usd) or price_usd <= 0:
        return _fail(Reason.NORMALIZED_PRICE_INVALID, fx_to_usd=rate, fx_effective_date=eff, **prov)
    if not isfinite(adv20_usd) or adv20_usd < 0:
        return _fail(Reason.NORMALIZED_ADV20_INVALID, fx_to_usd=rate, fx_effective_date=eff, **prov)

    return Normalization(
        ok=True, reason=None,
        currency=cur, price_unit=unit, price_local=p_local, adv20_local=a_local,
        fx_to_usd=rate, fx_effective_date=eff,
        price_usd=price_usd, adv20_usd=adv20_usd,
    )


def _as_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
