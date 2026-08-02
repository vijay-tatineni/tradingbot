"""Dynamic Universe — BLOCKER-W1: broker-free completed-bar provider boundary.

The shadow scheduler/evaluator must learn whether an instrument's COMPLETED daily (or other
timeframe) bar is safely available WITHOUT calling a broker. The legacy live path
(``main.py:_regime_bars_fetcher`` → ``broker.fetch_bars``) is a broker call and MUST NOT be used
by the shadow path. This module defines the abstract, INJECTED provider boundary the shadow path
depends on instead — plus a minimal immutable snapshot carrying the proof of availability.

Hard constraints (enforced by import-isolation tests):
  * broker-free — this module imports NO broker / IBKR / IG / EODHD / live market-data API and
    never constructs a live client. A concrete provider is supplied by injection only.
  * fail-closed — a missing provider, a provider exception, a malformed result, a
    date/timeframe mismatch, an unavailable bar, or a stale/incomplete bar ALL resolve to
    ``available=False`` with a stable reason. The boundary NEVER raises and NEVER reports a bar
    available unless the provider explicitly proves a fresh, complete, matching completed bar.

This module fetches NO live data, touches NO production database, and is a pure boundary: it is
never wired into a runtime startup here (Gate C prerequisites only).
"""
import logging
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger("universe.bar_provider")

# Stable fail-closed reason codes (non-sensitive; safe to log/persist as evidence).
BAR_PROVIDER_MISSING = "bar_provider_missing"
BAR_PROVIDER_ERROR = "bar_provider_error"
BAR_PROVIDER_MALFORMED = "bar_provider_malformed"
BAR_DATE_MISMATCH = "bar_date_mismatch"
BAR_TIMEFRAME_MISMATCH = "bar_timeframe_mismatch"
BAR_UNAVAILABLE = "bar_unavailable"
BAR_INCOMPLETE = "bar_incomplete"           # available but missing end-time / source / version
BAR_STALE = "bar_stale"                     # bar does not cover the requested trading_date

DEFAULT_TIMEFRAME = "1d"


@dataclass(frozen=True)
class CompletedBarSnapshot:
    """Immutable answer to "is the completed bar available, and what proves it?".

    Identity (``instrument_uid`` / ``listing_uid``) is the R2A-1 canonical identity, never a
    ticker. ``available`` is the ONLY field the caller acts on for go/no-go; the proof fields
    (``bar_end_time`` / ``source`` / ``version``) MUST be present for an ``available=True`` answer
    to survive validation (see ``safe_completed_bar``). ``reason`` carries the stable fail-closed
    code when ``available`` is False."""
    trading_date: str
    timeframe: str
    available: bool
    instrument_uid: Optional[str] = None
    listing_uid: Optional[str] = None
    bar_end_time: Optional[str] = None      # ISO timestamp/date proving the completed session
    source: Optional[str] = None            # non-sensitive provider/source label
    version: Optional[str] = None           # deterministic version/hash of the bar snapshot
    reason: Optional[str] = None            # stable reason when not available

    @property
    def is_available(self) -> bool:
        return bool(self.available)


@runtime_checkable
class CompletedBarProvider(Protocol):
    """Broker-free completed-bar availability boundary (INJECTED).

    Implementations answer from an already-materialized, broker-free source (a test fixture, an
    approved offline/cached daily-bar snapshot store) ONLY. They MUST NOT import or call a broker
    / IBKR / IG / EODHD / live market-data API, and MUST NOT fetch live data or read a production
    database. The concrete live/cached implementation is a SEPARATE, explicitly-approved tranche;
    this boundary is all the shadow scheduler/evaluator is permitted to depend on."""

    def completed_bar(self, *, record: dict, trading_date: str,
                      timeframe: str) -> CompletedBarSnapshot:
        ...


def _not_available(record: Optional[dict], trading_date: str, timeframe: str,
                   reason: str) -> CompletedBarSnapshot:
    rec = record or {}
    return CompletedBarSnapshot(
        trading_date=trading_date, timeframe=timeframe, available=False,
        instrument_uid=rec.get("instrument_uid"), listing_uid=rec.get("listing_uid"),
        reason=reason)


def safe_completed_bar(provider: Optional[CompletedBarProvider], *, record: dict,
                       trading_date: str,
                       timeframe: str = DEFAULT_TIMEFRAME) -> CompletedBarSnapshot:
    """Fail-closed completed-bar availability check.

    Returns a CompletedBarSnapshot that is ``available=True`` ONLY when the injected provider
    explicitly returns a well-formed snapshot for the requested ``trading_date``/``timeframe``
    that is marked available AND carries full proof (``bar_end_time`` + ``source`` + ``version``)
    AND whose ``bar_end_time`` covers the requested ``trading_date`` (not stale). Every other
    outcome — no provider, an exception, a malformed/mismatched result, an unavailable bar, or a
    stale/incomplete bar — resolves to ``available=False`` with a stable reason. NEVER raises."""
    if provider is None:
        return _not_available(record, trading_date, timeframe, BAR_PROVIDER_MISSING)
    try:
        res = provider.completed_bar(record=record, trading_date=trading_date,
                                     timeframe=timeframe)
    except Exception:
        logger.warning("completed-bar provider raised for %s %s/%s — treating as unavailable",
                       (record or {}).get("canonical_instrument_id"), trading_date, timeframe)
        return _not_available(record, trading_date, timeframe, BAR_PROVIDER_ERROR)
    if not isinstance(res, CompletedBarSnapshot):
        return _not_available(record, trading_date, timeframe, BAR_PROVIDER_MALFORMED)
    if str(res.trading_date) != str(trading_date):
        return _not_available(record, trading_date, timeframe, BAR_DATE_MISMATCH)
    if str(res.timeframe) != str(timeframe):
        return _not_available(record, trading_date, timeframe, BAR_TIMEFRAME_MISMATCH)
    if not res.available:
        # Preserve the provider's reason if any, else stamp a stable one.
        return res if res.reason else _not_available(record, trading_date, timeframe,
                                                      BAR_UNAVAILABLE)
    # available=True must carry full proof, else fail closed (incomplete).
    if not res.bar_end_time or not res.source or not res.version:
        return _not_available(record, trading_date, timeframe, BAR_INCOMPLETE)
    # the completed bar must cover the requested session date, else it is stale/mismatched.
    if str(res.bar_end_time)[:10] != str(trading_date):
        return _not_available(record, trading_date, timeframe, BAR_STALE)
    return res


def as_bar_available_fn(provider: Optional[CompletedBarProvider],
                        timeframe: str = DEFAULT_TIMEFRAME):
    """Adapt a CompletedBarProvider into the scheduler's ``bar_available_fn`` callable
    (``Callable[[dict, str], bool]``) — fully fail-closed via ``safe_completed_bar``. This is the
    ONLY coupling between the existing ``DailyUniverseScheduler`` and the W1 boundary; the
    scheduler still depends on a plain callable, never on a broker client."""
    def _fn(record: dict, trading_date: str) -> bool:
        return safe_completed_bar(provider, record=record, trading_date=trading_date,
                                  timeframe=timeframe).is_available
    return _fn
