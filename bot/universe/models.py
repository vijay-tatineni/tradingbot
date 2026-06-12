"""Dynamic Universe v1 — state model, reason codes, and small data carriers.

Broker-neutral. The POSITION_OPEN / EXIT_ONLY states describe a *hypothetical*
shadow position tracked by the evaluator's own ledger — they NEVER read or manage
live positions.db. "Exit management continues" in EXIT_ONLY means the hypothetical
ATR-trail / trend-break calculation keeps running on the synthetic position; no
broker call is made.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional, Protocol, runtime_checkable


class State(str, Enum):
    HARD_DISABLED = "HARD_DISABLED"
    DATA_INELIGIBLE = "DATA_INELIGIBLE"
    WATCHLIST = "WATCHLIST"
    ENTRY_ELIGIBLE = "ENTRY_ELIGIBLE"
    POSITION_OPEN = "POSITION_OPEN"
    EXIT_ONLY = "EXIT_ONLY"
    COOLDOWN = "COOLDOWN"
    ADMIN_PAUSED = "ADMIN_PAUSED"


class PositionStatus(str, Enum):
    """Read-only operational position status fed to the shadow evaluator via an
    INJECTED provider (see PositionSnapshotProvider). It carries ONLY the operational
    fact needed to drive universe-state transitions — never prices, P&L, or a ledger,
    and the provider must never call a broker.

    UNKNOWN is the fail-safe value: it means the position state could not be
    determined, NOT that the instrument is flat. The evaluator treats it
    conservatively (no new entry, no forced liquidation).

    POSITION_EXITED is the DURABLE exit status (P3-9): it represents an authoritatively
    closed position and should carry durable exit information (closed_trading_date /
    position_id) on its PositionSnapshot. POSITION_EXITED_TODAY is the DEPRECATED
    one-cycle transient marker retained for back-compat — the evaluator now derives an
    exit primarily from a durable OPEN→NO_POSITION transition + evidence, so cooldown no
    longer depends on observing the transient value (P3-9).
    """
    NO_POSITION = "NO_POSITION"
    POSITION_OPEN = "POSITION_OPEN"
    POSITION_EXITED = "POSITION_EXITED"                # durable, evidence-bearing exit
    POSITION_EXITED_TODAY = "POSITION_EXITED_TODAY"    # DEPRECATED transient marker
    UNKNOWN = "UNKNOWN"


# Status values that, when observed, represent an exit signal from the provider.
EXIT_SIGNAL_STATUSES = frozenset({
    PositionStatus.POSITION_EXITED, PositionStatus.POSITION_EXITED_TODAY,
})


@dataclass(frozen=True)
class PositionSnapshot:
    """Authoritative, broker-free, read-only position observation (task §2).

    Carries the operational facts needed to drive the universe lifecycle and to detect a
    durable open→flat exit EXACTLY ONCE (P3-9): the status plus durable exit evidence
    (position_id / opened/closed trading dates / a source version). It NEVER carries
    account ids, quantities, prices, or any sensitive broker detail — and the provider
    that produces it must never call a broker.

    A bare PositionStatus is also accepted by the evaluator (wrapped into a snapshot with
    no durable evidence) for back-compat; the durable exactly-once exit guarantee requires
    closed_trading_date and/or position_id.
    """
    status: "PositionStatus"
    observed_at: Optional[datetime] = None
    position_id: Optional[str] = None
    opened_trading_date: Optional[date] = None
    closed_trading_date: Optional[date] = None
    source_version: Optional[str] = None


@runtime_checkable
class PositionSnapshotProvider(Protocol):
    """Broker-free, read-only seam supplying operational position status.

    Implementations MUST NOT import a broker adapter, call IBKR/IG, submit/amend an
    order, or read a live broker session. They return only a PositionStatus or a
    PositionSnapshot derived from an already-materialised, non-broker source (e.g. a
    fixture, a copied non-production snapshot, or a shadow ledger). The evaluator
    dependency-injects an instance; when NONE is injected the position status is UNKNOWN
    (fail-safe) — never a stale prior-state assumption (P3-8).
    """
    def get_position_status(
        self,
        canonical_instrument_id: str,
        trading_date: date,
    ) -> "PositionStatus | PositionSnapshot":
        ...


# Corporate-action eligibility policy modes (task §4). The default is the SAFE,
# fail-closed paper/live policy: an unknown corporate-action status BLOCKS new
# entries. The shadow evaluator opts in to the permissive shadow policy explicitly
# (warn-not-block) — a forgotten/wrong argument therefore fails closed, never
# silently permits entry on unknown corp-action data.
ELIGIBILITY_MODE_PAPER_LIVE = "paper_live"
ELIGIBILITY_MODE_SHADOW = "shadow"


# Structural-eligibility / transition reason codes (recorded, never silently dropped).
class Reason:
    HARD_DISABLED = "hard_disabled"
    ADMIN_PAUSED = "admin_paused"
    NOT_ADMINISTRATIVELY_ACTIVE = "not_administratively_active"
    INSUFFICIENT_HISTORY = "insufficient_history"          # < MIN_HISTORY_BARS
    STALE_BAR = "stale_bar"                                # no fresh completed bar
    PRICE_BELOW_MIN = "price_below_min"
    ADV20_BELOW_MIN = "adv20_below_min"
    INDICATORS_UNAVAILABLE = "indicators_unavailable"
    INVALID_OHLC = "invalid_ohlc"
    CORP_ACTION_DATA_UNAVAILABLE = "corp_action_data_unavailable"  # paper/live: hard block
    CORP_ACTION_STATUS_UNKNOWN = "corporate_action_status_unknown"  # shadow: warn, not block
    CORP_ACTION_ANOMALY = "corp_action_anomaly"
    RESEARCH_MAPPING_MISSING = "research_mapping_missing"
    IBKR_MAPPING_MISSING = "ibkr_mapping_missing"
    IN_COOLDOWN = "in_cooldown"
    # ── currency normalisation (P2-2): all fail-CLOSED, never fall back to local ──
    FX_CONVERSION_UNAVAILABLE = "fx_conversion_unavailable"   # no provider / no rate
    FX_RATE_INVALID = "fx_rate_invalid"                       # zero/negative/NaN/inf/future
    FX_RATE_STALE = "fx_rate_stale"                           # older than accepted window
    CURRENCY_UNKNOWN = "currency_unknown"
    PRICE_UNIT_UNKNOWN = "price_unit_unknown"
    GBX_GBP_UNIT_AMBIGUOUS = "gbx_gbp_unit_ambiguous"         # pence vs pounds undecidable
    NORMALIZED_PRICE_INVALID = "normalized_price_invalid"
    NORMALIZED_ADV20_INVALID = "normalized_adv20_invalid"
    SECTOR_UNKNOWN = "sector_unknown"                      # informational; not a hard fail
    POSITION_STATUS_UNKNOWN = "position_status_unknown"    # safe non-entry; never silent
    # Legacy v1 row carried a non-zero `cooldown_until` count but no v2
    # `cooldown_sessions_remaining`; the count is NOT inferred (P3-2). The instrument is
    # held BLOCKED pending manual review rather than guessing a remaining session count.
    COOLDOWN_LEGACY_AMBIGUOUS = "cooldown_legacy_ambiguous"
    # contention / routing (hypothetical)
    SLOT_CAP_REACHED = "slot_cap_reached"
    SECTOR_CAP_REACHED = "sector_cap_reached"
    PORTFOLIO_HEAT_EXCEEDED = "portfolio_heat_exceeded"
    ELIGIBLE = "eligible"
    PASSED_HYSTERESIS = "passed_entry_hysteresis"


# Reason codes that, if present, FAIL structural eligibility.
BLOCKING_REASONS = frozenset({
    Reason.HARD_DISABLED,
    Reason.NOT_ADMINISTRATIVELY_ACTIVE,
    Reason.INSUFFICIENT_HISTORY,
    Reason.STALE_BAR,
    Reason.PRICE_BELOW_MIN,
    Reason.ADV20_BELOW_MIN,
    Reason.INDICATORS_UNAVAILABLE,
    Reason.INVALID_OHLC,
    Reason.CORP_ACTION_DATA_UNAVAILABLE,
    Reason.CORP_ACTION_ANOMALY,
    Reason.RESEARCH_MAPPING_MISSING,
    Reason.IBKR_MAPPING_MISSING,
    Reason.IN_COOLDOWN,
    # Currency-normalisation failures fail CLOSED: an instrument whose USD-normalised
    # price/ADV20 cannot be trusted is never entry-eligible (P2-2). We do NOT fall back
    # to comparing a local-currency value against a USD threshold.
    Reason.FX_CONVERSION_UNAVAILABLE,
    Reason.FX_RATE_INVALID,
    Reason.FX_RATE_STALE,
    Reason.CURRENCY_UNKNOWN,
    Reason.PRICE_UNIT_UNKNOWN,
    Reason.GBX_GBP_UNIT_AMBIGUOUS,
    Reason.NORMALIZED_PRICE_INVALID,
    Reason.NORMALIZED_ADV20_INVALID,
})


@dataclass
class EligibilityResult:
    passes: bool
    reason_codes: list = field(default_factory=list)


@dataclass
class StateOutcome:
    new_state: State
    consecutive_passes: int
    consecutive_failures: int
    reason_codes: list = field(default_factory=list)
    # v1 shadow has no trading-calendar dependency: cooldown is counted in COMPLETED
    # evaluated sessions (P3-2). The canonical persisted home is
    # universe_state.cooldown_sessions_remaining (the legacy `cooldown_until` column is
    # deprecated compatibility metadata only and is NOT read by runtime logic).
    cooldown_remaining: int = 0
    # True on the session a fresh exit starts cooldown (exit session E is NOT counted).
    cooldown_started: bool = False
    # True when this session was a countable completed post-exit session that decremented
    # the remaining count (drives cooldown_last_counted_trading_date — exactly-once/day).
    cooldown_counted: bool = False


@dataclass
class HypotheticalOrder:
    """Shadow-only sizing output — never submitted."""
    canonical_instrument_id: str
    primary_gateway: str
    entry_price: float
    initial_stop: float
    qty: int
    risk_usd: float
    notional_usd: float
    rejected_reason: Optional[str] = None
