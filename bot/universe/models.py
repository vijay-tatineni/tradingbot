"""Dynamic Universe v1 — state model, reason codes, and small data carriers.

Broker-neutral. The POSITION_OPEN / EXIT_ONLY states describe a *hypothetical*
shadow position tracked by the evaluator's own ledger — they NEVER read or manage
live positions.db. "Exit management continues" in EXIT_ONLY means the hypothetical
ATR-trail / trend-break calculation keeps running on the synthetic position; no
broker call is made.
"""
from dataclasses import dataclass, field
from datetime import date
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
    """
    NO_POSITION = "NO_POSITION"
    POSITION_OPEN = "POSITION_OPEN"
    POSITION_EXITED_TODAY = "POSITION_EXITED_TODAY"
    UNKNOWN = "UNKNOWN"


@runtime_checkable
class PositionSnapshotProvider(Protocol):
    """Broker-free, read-only seam supplying operational position status.

    Implementations MUST NOT import a broker adapter, call IBKR/IG, submit/amend an
    order, or read a live broker session. They return only a PositionStatus derived
    from an already-materialised, non-broker source (e.g. a fixture, a copied
    non-production snapshot, or a shadow ledger). The evaluator dependency-injects an
    instance; when none is injected it falls back to the legacy prior-state derivation.
    """
    def get_position_status(
        self,
        canonical_instrument_id: str,
        trading_date: date,
    ) -> "PositionStatus":
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
    SECTOR_UNKNOWN = "sector_unknown"                      # informational; not a hard fail
    POSITION_STATUS_UNKNOWN = "position_status_unknown"    # safe non-entry; never silent
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
    # v1 shadow has no trading calendar dependency: cooldown is counted in
    # completed evaluated sessions (decremented each session). Persisted as a
    # string in universe_state.cooldown_until.
    cooldown_remaining: int = 0


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
