"""Dynamic Universe R2A-1 — canonical identity domain (P3-6 / P3-7).

PURE domain layer: enums, the immutable verified-reference carrier, the broker-free
identity-provider seam, opaque-uid derivation, and the pure IBKR mapping verifier.
No SQLite, no broker, no data-provider import — persistence lives in
``bot.universe.identity_store`` and the evaluator gate reads (never calls a provider
on the hot path).

Four distinct concepts (operator-frozen):

  * ``instrument_uid`` — opaque, immutable, NEVER derived from the ticker; the economic
    security. Survives ticker renames, broker remaps, and listing migrations.
  * ``listing_uid``    — opaque, immutable; one venue/currency listing. A listing
    migration creates a NEW listing_uid and never rewrites historical listing identity.
  * broker mapping     — IBKR ``conId`` / IG ``epic`` are VERIFIED MAPPING ATTRIBUTES
    only, never canonical identity.
  * ``display_symbol`` — the human ticker; auditable, never an identity anchor.

Identity anchoring (operator-frozen): the preferred anchor is a verified
``ISIN + MIC + currency``; a verified ``FIGI`` is a supported fallback/corroborating
anchor. No verified anchor ⇒ ``identity_unverified`` ⇒ entry blocked. Instruments are
NEVER merged merely because their tickers match.
"""
import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional, Protocol, runtime_checkable


class IdentityReferenceStatus(str, Enum):
    """Status of an external identity-provider resolution. Only ``VERIFIED`` may
    establish a canonical identity; every other value fails closed."""
    VERIFIED = "VERIFIED"          # a single, verified anchor (ISIN+MIC+ccy, or FIGI)
    AMBIGUOUS = "AMBIGUOUS"        # >1 plausible match — never auto-resolved
    NOT_FOUND = "NOT_FOUND"        # provider found no match
    UNVERIFIED = "UNVERIFIED"      # provider could not verify the anchor
    STALE = "STALE"               # reference older than the accepted window


class MappingVerificationStatus(str, Enum):
    """Frozen broker-mapping eligibility states (operator-frozen). Only
    ``VERIFIED_REFERENCE_MATCH`` may pass shadow eligibility; all else fails closed."""
    VERIFIED_REFERENCE_MATCH = "VERIFIED_REFERENCE_MATCH"
    VERIFIED_CONFIGURED = "VERIFIED_CONFIGURED"
    UNVERIFIED = "UNVERIFIED"
    STALE = "STALE"
    REJECTED = "REJECTED"
    AMBIGUOUS = "AMBIGUOUS"


# Default reverification interval for a verified mapping (operator-frozen): 90 calendar
# days. A mapping whose ``reverify_after_date`` has passed the evaluation date is treated
# as STALE and fails closed.
DEFAULT_REVERIFY_DAYS = 90


class IdentityConflictError(Exception):
    """A resolution asserts an anchor that CONFLICTS with a stored identity/listing on a
    material field (ISIN / FIGI / MIC / currency / instrument name) for the SAME opaque
    key. The store NEVER merges or overwrites on a conflict — it raises this and the
    evaluator gate blocks entry (``identity_conflict``)."""


class IdentityResolutionError(Exception):
    """A resolution could not be safely persisted: the provider failed, returned a
    non-VERIFIED status, or returned a malformed/ambiguous/stale/future-dated reference.
    Fails closed — no partial identity is written."""


@dataclass(frozen=True)
class IdentityReference:
    """Immutable result of a verified external identity resolution (operator-frozen shape).

    Broker identifiers (``ibkr_conid`` / ``ig_epic``) are carried here as VERIFIED MAPPING
    ATTRIBUTES only — they are never part of the canonical identity. Never carries
    credentials, account ids, tokens, or raw provider payloads.
    """
    status: IdentityReferenceStatus
    instrument_uid: Optional[str]
    listing_uid: Optional[str]

    isin: Optional[str]
    figi: Optional[str]
    mic: Optional[str]
    currency: Optional[str]
    display_symbol: Optional[str]

    source: str
    effective_date: date
    verified_at: datetime

    ibkr_conid: Optional[str] = None
    ig_epic: Optional[str] = None
    instrument_name: Optional[str] = None
    price_unit: Optional[str] = None
    exchange: Optional[str] = None


@runtime_checkable
class IdentityReferenceProvider(Protocol):
    """Injected, BROKER-FREE reference seam. Implementations resolve identity from a
    reference/golden source (e.g. a security master) and MUST NOT import a broker adapter,
    call IBKR/IG/EODHD, submit an order, or read a live broker session. The core evaluator
    and registry never call this on the hot path — resolution is a controlled, offline
    store operation."""
    def resolve(
        self,
        *,
        display_symbol: str,
        mic: "str | None",
        currency: "str | None",
        as_of: date,
    ) -> IdentityReference:
        ...


# ── opaque uid derivation ───────────────────────────────────────────────────────
# instrument_uid is derived from the VERIFIED ECONOMIC ANCHOR (ISIN preferred, FIGI
# fallback) — NEVER from the ticker. Deterministic so an identical resolution replays to
# the same uid (idempotent). Opaque (a one-way hash token) so it is not a reversible alias
# of the symbol. listing_uid is derived from (instrument_uid, MIC, currency): the same
# economic instrument on a different venue/currency is a DISTINCT listing.

def _norm(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip().upper()
    return s or None


def derive_instrument_uid(isin=None, figi=None) -> str:
    """Opaque, deterministic instrument_uid from the verified anchor. ISIN is preferred;
    FIGI is the fallback. Raises if neither is present (a verified anchor is required)."""
    isin_n, figi_n = _norm(isin), _norm(figi)
    if isin_n:
        seed = f"ISIN|{isin_n}"
    elif figi_n:
        seed = f"FIGI|{figi_n}"
    else:
        raise IdentityResolutionError(
            "cannot derive instrument_uid without a verified ISIN or FIGI anchor")
    return "iid_" + hashlib.sha256(seed.encode()).hexdigest()[:32]


def derive_listing_uid(instrument_uid: str, mic=None, currency=None) -> str:
    """Opaque, deterministic listing_uid for one venue/currency listing of an instrument.
    Requires MIC and currency (a listing is venue- and currency-specific)."""
    mic_n, ccy_n = _norm(mic), _norm(currency)
    if not mic_n or not ccy_n:
        raise IdentityResolutionError(
            "cannot derive listing_uid without both MIC and currency")
    seed = f"{instrument_uid}|{mic_n}|{ccy_n}"
    return "lst_" + hashlib.sha256(seed.encode()).hexdigest()[:32]


def reference_has_verified_anchor(ref: IdentityReference) -> bool:
    """True iff the reference carries a usable verified anchor: ISIN+MIC+currency, or FIGI.
    (MIC+currency are still required to anchor the LISTING even on the FIGI fallback.)"""
    if ref.status != IdentityReferenceStatus.VERIFIED:
        return False
    has_isin = bool(_norm(ref.isin))
    has_figi = bool(_norm(ref.figi))
    has_listing = bool(_norm(ref.mic) and _norm(ref.currency))
    return (has_isin or has_figi) and has_listing


# ── pure IBKR mapping verification ──────────────────────────────────────────────

@dataclass(frozen=True)
class MappingVerdict:
    passes: bool
    reason_code: Optional[str]      # None iff passes; else a Reason.* gate code


def _parse_date(v) -> Optional[date]:
    """Parse a date/ISO-date/ISO-datetime to a date; None on missing/unparseable."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def verify_ibkr_mapping(mapping: Optional[dict], listing: Optional[dict],
                        evaluation_date: date) -> MappingVerdict:
    """Pure, fail-closed IBKR mapping verification (operator-frozen, task §5).

    Only ``VERIFIED_REFERENCE_MATCH`` — fresh, with a conId, and consistent with the
    verified listing's currency/exchange — passes. Every other state, and every freshness
    or field mismatch, fails closed with a stable reason code. Reason codes are imported
    lazily to keep this module dependency-light.
    """
    from bot.universe.models import Reason

    if mapping is None:
        return MappingVerdict(False, Reason.IBKR_MAPPING_UNVERIFIED)

    status = (mapping.get("verification_status") or "").strip()
    blocked_by_status = {
        MappingVerificationStatus.REJECTED.value: Reason.IBKR_MAPPING_REJECTED,
        MappingVerificationStatus.AMBIGUOUS.value: Reason.IBKR_MAPPING_AMBIGUOUS,
        MappingVerificationStatus.STALE.value: Reason.IBKR_MAPPING_STALE,
        MappingVerificationStatus.VERIFIED_CONFIGURED.value:
            Reason.IBKR_MAPPING_NOT_REFERENCE_VERIFIED,
        MappingVerificationStatus.UNVERIFIED.value: Reason.IBKR_MAPPING_UNVERIFIED,
    }
    if status in blocked_by_status:
        return MappingVerdict(False, blocked_by_status[status])
    if status != MappingVerificationStatus.VERIFIED_REFERENCE_MATCH.value:
        # Unknown/empty status → fail closed (never assume reference-verified).
        return MappingVerdict(False, Reason.IBKR_MAPPING_UNVERIFIED)

    # ── VERIFIED_REFERENCE_MATCH: enforce freshness + field consistency ──
    if not (mapping.get("conid") and str(mapping.get("conid")).strip()):
        return MappingVerdict(False, Reason.IBKR_MAPPING_MISMATCH)   # missing conId

    verified_at = _parse_date(mapping.get("verified_at"))
    if verified_at is None or verified_at > evaluation_date:
        # No parseable verification time, or a FUTURE verified_at → freshness invalid.
        return MappingVerdict(False, Reason.IBKR_MAPPING_STALE)

    reverify_after = _parse_date(mapping.get("reverify_after_date"))
    if reverify_after is None or reverify_after < evaluation_date:
        # No freshness window, or the reverification deadline has passed → STALE.
        return MappingVerdict(False, Reason.IBKR_MAPPING_STALE)

    if listing is not None:
        m_ccy, l_ccy = _norm(mapping.get("currency")), _norm(listing.get("currency"))
        if m_ccy and l_ccy and m_ccy != l_ccy:
            return MappingVerdict(False, Reason.IBKR_MAPPING_MISMATCH)   # currency
        m_exch = _norm(mapping.get("exchange"))
        l_exch, l_mic = _norm(listing.get("exchange")), _norm(listing.get("mic"))
        if m_exch and (l_exch or l_mic) and m_exch != l_exch and m_exch != l_mic:
            return MappingVerdict(False, Reason.IBKR_MAPPING_MISMATCH)   # MIC/exchange

    return MappingVerdict(True, None)
