"""Dynamic Universe R2A-1 — canonical-identity store + pre-entry gate (P3-6 / P3-7).

The controlled identity-resolution/store API. Opaque ``instrument_uid`` / ``listing_uid``
values are created HERE and only here (deterministically, from the verified anchor — never
from the ticker) and persisted permanently. All multi-row writes run inside one explicit
``BEGIN IMMEDIATE`` transaction (mirroring db.migrate / persist_transition_atomic): identity,
listing, broker mappings, and the immutable audit row COMMIT together or ROLL BACK together.

Broker-free: this module never imports a broker adapter, calls IBKR/IG/EODHD, submits an
order, or reads a live broker session. Provider errors / non-VERIFIED / malformed / ambiguous
/ stale / future-dated references fail closed (no partial identity is written).
"""
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from bot.universe.db import connect, migrate
from bot.universe.identity import (
    DEFAULT_REVERIFY_DAYS, IdentityConflictError, IdentityReference,
    IdentityReferenceStatus, IdentityResolutionError, MappingVerificationStatus,
    derive_instrument_uid, derive_listing_uid, reference_has_verified_anchor,
    verify_ibkr_mapping,
)
from bot.universe.models import Reason


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(v) -> Optional[str]:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _to_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _norm(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip().upper()
    return s or None


def _row(cur):
    cols = [d[0] for d in cur.description]
    r = cur.fetchone()
    return dict(zip(cols, r)) if r else None


def _rows(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


@dataclass
class GateResult:
    """Deterministic pre-entry identity/mapping gate verdict. ``passes`` is True only when a
    verified identity, an active verified listing, and a VERIFIED_REFERENCE_MATCH (fresh,
    field-consistent) IBKR mapping all hold. Failure records stable reason codes and NEVER
    forces liquidation, alters a position, or calls a broker."""
    passes: bool
    reason_codes: list = field(default_factory=list)
    instrument_uid: Optional[str] = None
    listing_uid: Optional[str] = None


class IdentityStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        migrate(db_path)   # idempotent; brings the DB to schema v5

    # ── read helpers ─────────────────────────────────────────────────
    def get_identity(self, instrument_uid: str) -> Optional[dict]:
        with connect(self.db_path) as conn:
            return _row(conn.execute(
                "SELECT * FROM instrument_identity WHERE instrument_uid=?", (instrument_uid,)))

    def get_listing(self, listing_uid: str) -> Optional[dict]:
        with connect(self.db_path) as conn:
            return _row(conn.execute(
                "SELECT * FROM instrument_listing WHERE listing_uid=?", (listing_uid,)))

    def active_listings(self, instrument_uid: str) -> list:
        with connect(self.db_path) as conn:
            return _rows(conn.execute(
                "SELECT * FROM instrument_listing WHERE instrument_uid=? "
                "AND listing_status='ACTIVE' AND valid_to IS NULL "
                "ORDER BY listing_uid", (instrument_uid,)))

    def get_ibkr_mapping(self, instrument_uid: str, listing_uid: str) -> Optional[dict]:
        with connect(self.db_path) as conn:
            return _row(conn.execute(
                "SELECT * FROM ibkr_mapping WHERE instrument_uid=? AND listing_uid=?",
                (instrument_uid, listing_uid)))

    def get_ig_mapping(self, instrument_uid: str, listing_uid: str) -> Optional[dict]:
        with connect(self.db_path) as conn:
            return _row(conn.execute(
                "SELECT * FROM ig_mapping WHERE instrument_uid=? AND listing_uid=?",
                (instrument_uid, listing_uid)))

    def audit_events(self, instrument_uid: str) -> list:
        with connect(self.db_path) as conn:
            return _rows(conn.execute(
                "SELECT * FROM identity_audit WHERE instrument_uid=? ORDER BY id",
                (instrument_uid,)))

    # ── controlled atomic resolution ─────────────────────────────────
    def resolve_identity_atomic(self, reference: IdentityReference, trading_date,
                                resolver_version: str,
                                canonical_instrument_id: Optional[str] = None,
                                _fault_hook=None) -> dict:
        """Atomically persist a verified identity, its listing, broker mappings, and an
        immutable audit row for ONE verified reference (operator-frozen, task §4).

          identical resolution replay        → idempotent no-op (same opaque uids; upsert
                                                rewrites identical content)
          same opaque key + conflicting anchor→ IdentityConflictError (never merge/overwrite)
          existing ticker + different anchor  → a NEW instrument_uid (anchor-derived); never
                                                merged into the existing identity
          same anchor + ticker rename         → SAME instrument_uid; listing display_symbol
                                                updated; old symbol retained in the audit log

        Fails closed: a None / non-VERIFIED / anchor-less / future-dated reference raises
        IdentityResolutionError and writes nothing. ``canonical_instrument_id`` (optional)
        links the legacy canonical row to the resolved identity inside the same transaction.
        """
        self._validate_reference(reference, trading_date)
        iuid = derive_instrument_uid(reference.isin, reference.figi)
        luid = derive_listing_uid(iuid, reference.mic, reference.currency)
        now = _utc_now_iso()
        td_iso = _iso(trading_date)

        def fault(seam):
            if _fault_hook is not None:
                _fault_hook(seam)

        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.isolation_level = None
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                ex_id = _row(conn.execute(
                    "SELECT * FROM instrument_identity WHERE instrument_uid=?", (iuid,)))
                self._check_identity_conflict(ex_id, reference)

                ex_listing = _row(conn.execute(
                    "SELECT * FROM instrument_listing WHERE listing_uid=?", (luid,)))
                self._check_listing_conflict(ex_listing, reference, iuid)

                # Collision guard: the SAME active (mic, currency, display) coordinates bound
                # to a DIFFERENT economic instrument is an unresolved ambiguity → conflict.
                collision = _rows(conn.execute(
                    "SELECT listing_uid, instrument_uid FROM instrument_listing "
                    "WHERE mic=? AND currency=? AND display_symbol=? "
                    "AND listing_status='ACTIVE' AND instrument_uid != ?",
                    (_norm(reference.mic), _norm(reference.currency),
                     reference.display_symbol, iuid)))
                if collision:
                    raise IdentityConflictError(
                        f"active listing coordinates ({reference.display_symbol},"
                        f"{reference.mic},{reference.currency}) already bound to a different "
                        f"instrument {collision[0]['instrument_uid']}")

                # ── persist identity (rename-safe: created_at preserved) ──
                created_at = ex_id["created_at"] if ex_id else now
                conn.execute(
                    """
                    INSERT INTO instrument_identity
                        (instrument_uid, display_symbol, instrument_name, isin, figi,
                         identity_status, identity_source, identity_verified_at,
                         identity_effective_date, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(instrument_uid) DO UPDATE SET
                        display_symbol=excluded.display_symbol,
                        instrument_name=COALESCE(excluded.instrument_name,
                                                 instrument_identity.instrument_name),
                        isin=COALESCE(excluded.isin, instrument_identity.isin),
                        figi=COALESCE(excluded.figi, instrument_identity.figi),
                        identity_status=excluded.identity_status,
                        identity_source=excluded.identity_source,
                        identity_verified_at=excluded.identity_verified_at,
                        identity_effective_date=excluded.identity_effective_date,
                        updated_at=excluded.updated_at
                    """,
                    (iuid, reference.display_symbol, reference.instrument_name,
                     _norm(reference.isin), _norm(reference.figi),
                     IdentityReferenceStatus.VERIFIED.value, reference.source,
                     _iso(reference.verified_at), _iso(reference.effective_date),
                     created_at, now))
                fault("after_identity")

                is_rename = bool(ex_listing
                                 and _norm(ex_listing.get("display_symbol"))
                                 != _norm(reference.display_symbol))
                l_created_at = ex_listing["created_at"] if ex_listing else now
                valid_from = ex_listing["valid_from"] if ex_listing else td_iso
                conn.execute(
                    """
                    INSERT INTO instrument_listing
                        (listing_uid, instrument_uid, display_symbol, mic, exchange,
                         currency, price_unit, valid_from, valid_to, listing_status,
                         created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?, 'ACTIVE', ?, ?)
                    ON CONFLICT(listing_uid) DO UPDATE SET
                        display_symbol=excluded.display_symbol,
                        exchange=COALESCE(excluded.exchange, instrument_listing.exchange),
                        price_unit=COALESCE(excluded.price_unit, instrument_listing.price_unit),
                        listing_status='ACTIVE',
                        updated_at=excluded.updated_at
                    """,
                    (luid, iuid, reference.display_symbol, _norm(reference.mic),
                     _norm(reference.exchange), _norm(reference.currency),
                     reference.price_unit, valid_from, None, l_created_at, now))
                fault("after_listing")

                # ── broker mappings (verified mapping ATTRIBUTES, never identity) ──
                if reference.ibkr_conid:
                    reverify = (_to_date(reference.verified_at)
                                or _to_date(trading_date))
                    reverify_after = (reverify + timedelta(days=DEFAULT_REVERIFY_DAYS)
                                      ).isoformat() if reverify else None
                    conn.execute(
                        """
                        INSERT INTO ibkr_mapping
                            (instrument_uid, listing_uid, conid, exchange, currency,
                             verification_status, verification_method, verified_at,
                             reverify_after_date, source_reference, mapping_version,
                             created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(instrument_uid, listing_uid) DO UPDATE SET
                            conid=excluded.conid, exchange=excluded.exchange,
                            currency=excluded.currency,
                            verification_status=excluded.verification_status,
                            verification_method=excluded.verification_method,
                            verified_at=excluded.verified_at,
                            reverify_after_date=excluded.reverify_after_date,
                            source_reference=excluded.source_reference,
                            mapping_version=excluded.mapping_version,
                            updated_at=excluded.updated_at
                        """,
                        (iuid, luid, str(reference.ibkr_conid), _norm(reference.exchange),
                         _norm(reference.currency),
                         MappingVerificationStatus.VERIFIED_REFERENCE_MATCH.value,
                         "reference_match", _iso(reference.verified_at), reverify_after,
                         reference.source, resolver_version, now, now))
                if reference.ig_epic:
                    # IG epic is reference/shadow metadata ONLY; routing is FROZEN blocked.
                    conn.execute(
                        """
                        INSERT INTO ig_mapping
                            (instrument_uid, listing_uid, epic, verification_status,
                             verified_at, reverify_after_date, order_routing_blocked,
                             created_at, updated_at)
                        VALUES (?,?,?,?,?,?, 1, ?, ?)
                        ON CONFLICT(instrument_uid, listing_uid) DO UPDATE SET
                            epic=excluded.epic,
                            verification_status=excluded.verification_status,
                            verified_at=excluded.verified_at,
                            order_routing_blocked=1,
                            updated_at=excluded.updated_at
                        """,
                        (iuid, luid, reference.ig_epic,
                         MappingVerificationStatus.UNVERIFIED.value,
                         _iso(reference.verified_at), None, now, now))

                if canonical_instrument_id is not None:
                    conn.execute(
                        "UPDATE canonical_instruments "
                        "SET instrument_uid=?, identity_status=? WHERE canonical_instrument_id=?",
                        (iuid, IdentityReferenceStatus.VERIFIED.value,
                         canonical_instrument_id))

                event = ("LISTING_RENAMED" if is_rename
                         else ("IDENTITY_RESOLVED" if not ex_id else "IDENTITY_REVERIFIED"))
                self._audit(conn, iuid, luid, event, reference, td_iso, resolver_version,
                            detail={"prior_display_symbol":
                                    (ex_listing or {}).get("display_symbol")} if is_rename
                            else None)
                fault("at_commit")
                conn.execute("COMMIT")
                return {"instrument_uid": iuid, "listing_uid": luid,
                        "created": ex_id is None, "renamed": is_rename}
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
        finally:
            conn.close()

    def record_listing_migration_atomic(self, instrument_uid: str, old_listing_uid: str,
                                         new_reference: IdentityReference, trading_date,
                                         resolver_version: str) -> dict:
        """Venue/listing migration (operator-frozen, task §3): same instrument_uid, a NEW
        listing_uid for the new venue/currency. The old listing's ``valid_to`` is set and its
        status becomes MIGRATED — the historical listing row is RETAINED, never overwritten.
        """
        self._validate_reference(new_reference, trading_date)
        new_luid = derive_listing_uid(instrument_uid, new_reference.mic,
                                      new_reference.currency)
        now = _utc_now_iso()
        td_iso = _iso(trading_date)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.isolation_level = None
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                old = _row(conn.execute(
                    "SELECT * FROM instrument_listing WHERE listing_uid=?", (old_listing_uid,)))
                if old is None or old["instrument_uid"] != instrument_uid:
                    raise IdentityConflictError(
                        f"old listing {old_listing_uid} not found for instrument {instrument_uid}")
                if new_luid == old_listing_uid:
                    raise IdentityConflictError(
                        "migration target listing_uid equals the source — not a migration")
                # close the historical listing (RETAINED, not deleted)
                conn.execute(
                    "UPDATE instrument_listing SET valid_to=?, listing_status='MIGRATED', "
                    "updated_at=? WHERE listing_uid=?", (td_iso, now, old_listing_uid))
                # create the new listing
                conn.execute(
                    """
                    INSERT INTO instrument_listing
                        (listing_uid, instrument_uid, display_symbol, mic, exchange,
                         currency, price_unit, valid_from, valid_to, listing_status,
                         created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?, NULL, 'ACTIVE', ?, ?)
                    """,
                    (new_luid, instrument_uid, new_reference.display_symbol,
                     _norm(new_reference.mic), _norm(new_reference.exchange),
                     _norm(new_reference.currency), new_reference.price_unit, td_iso,
                     now, now))
                self._audit(conn, instrument_uid, new_luid, "LISTING_MIGRATED",
                            new_reference, td_iso, resolver_version,
                            detail={"old_listing_uid": old_listing_uid})
                conn.execute("COMMIT")
                return {"instrument_uid": instrument_uid, "old_listing_uid": old_listing_uid,
                        "new_listing_uid": new_luid}
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
        finally:
            conn.close()

    def close_listing_atomic(self, listing_uid: str, trading_date,
                             status: str = "DELISTED") -> None:
        """Delist/close a listing (status DELISTED). The row is RETAINED with valid_to set —
        a later relisting creates a NEW listing_uid; the historical row is never overwritten."""
        if status not in ("DELISTED", "MIGRATED"):
            raise ValueError(f"invalid close status {status!r}")
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE instrument_listing SET valid_to=?, listing_status=?, updated_at=? "
                "WHERE listing_uid=?",
                (_iso(trading_date), status, _utc_now_iso(), listing_uid))

    def upsert_ibkr_mapping(self, instrument_uid: str, listing_uid: str, fields: dict) -> None:
        """Persist an IBKR mapping with an explicit verification_status (test/operator seam for
        states OTHER than the resolver's reference-match happy path). order routing is never
        implied here — mappings carry no routing authority in this tranche."""
        now = _utc_now_iso()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO ibkr_mapping
                    (instrument_uid, listing_uid, conid, exchange, currency,
                     verification_status, verification_method, verified_at,
                     reverify_after_date, source_reference, mapping_version,
                     created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(instrument_uid, listing_uid) DO UPDATE SET
                    conid=excluded.conid, exchange=excluded.exchange,
                    currency=excluded.currency,
                    verification_status=excluded.verification_status,
                    verification_method=excluded.verification_method,
                    verified_at=excluded.verified_at,
                    reverify_after_date=excluded.reverify_after_date,
                    source_reference=excluded.source_reference,
                    mapping_version=excluded.mapping_version,
                    updated_at=excluded.updated_at
                """,
                (instrument_uid, listing_uid,
                 (str(fields["conid"]) if fields.get("conid") is not None else None),
                 _norm(fields.get("exchange")), _norm(fields.get("currency")),
                 fields.get("verification_status",
                            MappingVerificationStatus.UNVERIFIED.value),
                 fields.get("verification_method"), _iso(fields.get("verified_at")),
                 _iso(fields.get("reverify_after_date")), fields.get("source_reference"),
                 fields.get("mapping_version"), now, now))

    # ── deterministic pre-entry gate (task §7) ───────────────────────
    def entry_identity_gate(self, canonical_rec: dict, evaluation_date) -> GateResult:
        """Deterministic, fail-closed pre-entry gate: identity verified? listing verified?
        IBKR mapping VERIFIED_REFERENCE_MATCH and fresh? Returns blocking reason codes; it
        never forces liquidation, never alters a position, never calls a broker."""
        eval_d = _to_date(evaluation_date)
        instrument_uid = (canonical_rec or {}).get("instrument_uid")
        if not instrument_uid:
            return GateResult(False, [Reason.IDENTITY_UNVERIFIED])

        identity = self.get_identity(instrument_uid)
        if identity is None:
            return GateResult(False, [Reason.IDENTITY_UNVERIFIED], instrument_uid)
        status = identity.get("identity_status")
        if status == "AMBIGUOUS":
            return GateResult(False, [Reason.IDENTITY_AMBIGUOUS], instrument_uid)
        if status != IdentityReferenceStatus.VERIFIED.value:
            return GateResult(False, [Reason.IDENTITY_UNVERIFIED], instrument_uid)

        listings = self.active_listings(instrument_uid)
        if not listings:
            return GateResult(False, [Reason.LISTING_UNVERIFIED], instrument_uid)
        # Prefer the active listing matching the canonical currency/exchange; else the first.
        want_ccy = _norm(canonical_rec.get("currency"))
        listing = next((l for l in listings if _norm(l.get("currency")) == want_ccy),
                       listings[0])
        luid = listing["listing_uid"]

        # IG routing is blocked unconditionally in this tranche (defensive — v1 is IBKR-primary).
        if (canonical_rec.get("primary_gateway") or "IBKR") == "IG":
            return GateResult(False, [Reason.IG_ORDER_ROUTING_BLOCKED], instrument_uid, luid)

        mapping = self.get_ibkr_mapping(instrument_uid, luid)
        verdict = verify_ibkr_mapping(mapping, listing, eval_d)
        if not verdict.passes:
            return GateResult(False, [verdict.reason_code], instrument_uid, luid)
        return GateResult(True, [], instrument_uid, luid)

    # ── internals ────────────────────────────────────────────────────
    def _validate_reference(self, reference: IdentityReference, trading_date) -> None:
        if reference is None:
            raise IdentityResolutionError("no identity reference (provider returned None)")
        if reference.status == IdentityReferenceStatus.AMBIGUOUS:
            raise IdentityResolutionError("ambiguous identity reference — failing closed")
        if reference.status != IdentityReferenceStatus.VERIFIED:
            raise IdentityResolutionError(
                f"non-verified identity reference status {reference.status!r}")
        if not reference_has_verified_anchor(reference):
            raise IdentityResolutionError(
                "reference lacks a verified anchor (ISIN+MIC+currency, or FIGI+MIC+currency)")
        td = _to_date(trading_date)
        eff = _to_date(reference.effective_date)
        if td is not None and eff is not None and eff > td:
            raise IdentityResolutionError(
                f"reference effective_date {eff} is in the future (> {td}) — failing closed")
        vat = _to_date(reference.verified_at)
        if td is not None and vat is not None and vat > td:
            raise IdentityResolutionError(
                f"reference verified_at {vat} is in the future (> {td}) — failing closed")

    @staticmethod
    def _check_identity_conflict(ex_id: Optional[dict], reference: IdentityReference) -> None:
        if ex_id is None:
            return
        # Same opaque key: any material anchor field that disagrees is a conflict (never merge).
        for fld, ref_val in (("isin", reference.isin), ("figi", reference.figi),
                             ("instrument_name", reference.instrument_name)):
            stored = ex_id.get(fld)
            a, b = (_norm(stored) if fld != "instrument_name" else (stored or None),
                    _norm(ref_val) if fld != "instrument_name" else (ref_val or None))
            if a and b and a != b:
                raise IdentityConflictError(
                    f"instrument {ex_id['instrument_uid']} stored {fld}={stored!r} conflicts "
                    f"with asserted {fld}={ref_val!r}")

    @staticmethod
    def _check_listing_conflict(ex_listing: Optional[dict], reference: IdentityReference,
                                iuid: str) -> None:
        if ex_listing is None:
            return
        if ex_listing.get("instrument_uid") != iuid:
            raise IdentityConflictError(
                f"listing {ex_listing['listing_uid']} bound to {ex_listing['instrument_uid']} "
                f"conflicts with asserted instrument {iuid}")
        if (_norm(ex_listing.get("mic")) != _norm(reference.mic)
                or _norm(ex_listing.get("currency")) != _norm(reference.currency)):
            raise IdentityConflictError(
                f"listing {ex_listing['listing_uid']} venue/currency conflicts with reference")

    @staticmethod
    def _audit(conn, instrument_uid, listing_uid, event_type, reference, effective_date,
               resolver_version, detail=None) -> None:
        conn.execute(
            """
            INSERT INTO identity_audit
                (instrument_uid, listing_uid, event_type, isin, figi, mic, currency,
                 display_symbol, identity_source, effective_date, resolver_version,
                 detail, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (instrument_uid, listing_uid, event_type, _norm(reference.isin),
             _norm(reference.figi), _norm(reference.mic), _norm(reference.currency),
             reference.display_symbol, reference.source, effective_date, resolver_version,
             json.dumps(detail, sort_keys=True) if detail else None, _utc_now_iso()))
