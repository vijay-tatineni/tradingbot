"""Dynamic Universe R2B — persisted candidate-source store (P3-4).

The ONLY authoritative source of selection candidates. A candidate must be persisted here
(keyed by the R2A-1 canonical identity — instrument_uid + listing_uid, NEVER a ticker)
before it can affect selection; the evaluator consumes candidates exclusively via
``effective_candidates(trading_date)``. All multi-row writes run inside one explicit
``BEGIN IMMEDIATE`` transaction (candidate row(s) + supersession/deactivation + append-only
audit COMMIT together or ROLL BACK together).

Broker-free: never imports a broker adapter, calls IBKR/IG/EODHD, submits an order, or reads
a live broker session. Fail-closed: a missing/unresolved/ambiguous/conflicting/expired/
malformed candidate blocks NEW entry only — it never forces liquidation and never calls a
broker. Only NON-SENSITIVE audit metadata and a deterministic source_payload_hash are stored
(never credentials, account ids, tokens, or raw source payloads).

Frozen precedence: MANUAL > TTI > AUTO. Frozen TTL: MANUAL/TTI default 5 completed trading
sessions; AUTO is valid only for its generation session and is regenerated per session.

TTL SEMANTICS (frozen, read-then-count): the evaluator reads ``effective_candidates`` FIRST
(selecting on the current ``ttl_sessions_remaining``), THEN calls ``tick_ttl_atomic`` to count
the completed session. A MANUAL/TTI candidate submitted effective at session E with TTL=5 is
therefore EFFECTIVE on E, E+1, E+2, E+3, E+4 (five completed sessions). The tick at E+4 takes
its remaining count to 0 and marks it EXPIRED, so it is blocked from E+5 onward — it "expires
before the next eligible session". The tick is idempotent per date (guarded by
``last_counted_trading_date``): a duplicate same-date run, a missing-bar/weekend/non-session,
or a provider outage before a valid session evaluation never double-counts (the caller passes
only completed sessions; a same/earlier date is a no-op).
"""
import hashlib
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from bot.universe.db import connect, migrate
from bot.universe.models import Reason
from bot.universe.params import CANDIDATE_TTL_SESSIONS

# Frozen source precedence (lower wins).
PRECEDENCE = {"MANUAL": 0, "TTI": 1, "AUTO": 2}
VALID_SOURCES = frozenset(PRECEDENCE)

# Frozen candidate status enum (the only statuses a well-formed row may hold). Any other
# status value is a corruption / raw-injection and is treated as malformed (fail-closed).
VALID_STATUSES = frozenset({"ACTIVE", "EXPIRED", "SUPERSEDED", "DEACTIVATED", "REJECTED"})

# Audit event types. The lifecycle events are emitted by the write APIs; the two SELECTION
# events (R2B-P3-3) are emitted idempotently by record_selection_audit and are the only event
# types covered by the v8 partial-unique idempotency index.
EVENT_SELECTED_EFFECTIVE = "SELECTED_EFFECTIVE"
EVENT_SUPPRESSED = "SUPPRESSED"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_int(v) -> bool:
    """True only for a genuine integer TTL value (never bool, float, str, or None)."""
    return isinstance(v, int) and not isinstance(v, bool)


def _valid_date(v) -> bool:
    """True if ``v`` is a non-empty ISO date string / date the store can compare. Used to
    fail closed on a raw/injected row carrying a garbage effective/generation date."""
    if v is None:
        return False
    try:
        date.fromisoformat(str(v))
        return True
    except (ValueError, TypeError):
        return False


def _iso(v) -> Optional[str]:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _row(cur):
    cols = [d[0] for d in cur.description]
    r = cur.fetchone()
    return dict(zip(cols, r)) if r else None


def _rows(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _candidate_id(submission_key, instrument_uid, listing_uid, payload_hash) -> str:
    seed = f"{submission_key}|{instrument_uid}|{listing_uid}|{payload_hash}"
    return "cand_" + hashlib.sha256(seed.encode()).hexdigest()[:24]


class CandidateConflictError(Exception):
    """A submission reused an idempotency key (submission_key, or an AUTO generation_batch_id)
    with DIVERGENT immutable content. Fails closed — nothing is written; the caller must mint a
    new submission rather than mutate an existing candidate's identity/content."""


@dataclass
class EffectiveSelection:
    """Result of effective_candidates(): the single effective candidate per instrument_uid,
    the blocked instruments (with a fail-closed reason), and the suppressed candidate ids.

    ``store_unavailable`` (R2B-P3-1) is True when the candidate store / database could not be
    read at all (locked DB, missing table, sqlite read error). It is a fail-closed signal: the
    caller must block ALL new entries with ``candidate_store_unavailable`` and must NOT mutate
    TTL — there is no per-instrument selection to trust."""
    effective: dict = field(default_factory=dict)     # instrument_uid -> candidate row
    blocked: dict = field(default_factory=dict)       # instrument_uid -> reason_code
    suppressed: list = field(default_factory=list)    # [candidate_id, ...]
    store_unavailable: bool = False                   # R2B-P3-1: whole-store read failure


class CandidateStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        migrate(db_path)   # idempotent; brings the DB to schema head (v7)

    # ── identity verification (inline; uses the R2A-1 identity tables) ──
    @staticmethod
    def _verify_identity(conn, instrument_uid, listing_uid):
        """Return (ok, reason_code). A candidate is usable only with a VERIFIED instrument_uid
        and an ACTIVE listing_uid that belongs to it. Never resolves/merges by ticker."""
        if not instrument_uid:
            return False, Reason.CANDIDATE_IDENTITY_UNRESOLVED
        ident = conn.execute(
            "SELECT identity_status FROM instrument_identity WHERE instrument_uid=?",
            (instrument_uid,)).fetchone()
        if ident is None or ident[0] != "VERIFIED":
            return False, Reason.CANDIDATE_IDENTITY_UNRESOLVED
        if not listing_uid:
            # No explicit listing: if the instrument has >1 active listing this is ambiguous;
            # either way the candidate must explicitly identify the listing (never pick one).
            return False, Reason.CANDIDATE_LISTING_AMBIGUOUS
        listing = conn.execute(
            "SELECT instrument_uid, listing_status, valid_to FROM instrument_listing "
            "WHERE listing_uid=?", (listing_uid,)).fetchone()
        if listing is None or listing[0] != instrument_uid:
            return False, Reason.CANDIDATE_LISTING_UNVERIFIED
        if listing[1] != "ACTIVE" or listing[2] is not None:
            return False, Reason.CANDIDATE_LISTING_UNVERIFIED
        return True, None

    @staticmethod
    def _candidate_row_malformed(row):
        """Return Reason.CANDIDATE_MALFORMED if an otherwise-ACTIVE candidate row is
        STRUCTURALLY invalid, else None (R2B-P3-2). Defensive read-path validation for a
        raw/injected row: the write APIs already reject an unknown source, but a row written
        directly into the DB can carry an unknown source, a non-ACTIVE/unknown status, a
        missing identity key, a non-integer TTL, or a garbage effective/generation date.
        Such a row must FAIL CLOSED as malformed — never be selected and never be silently
        dropped in a way that lets a lower-precedence candidate fall through (the caller runs
        this on the highest-precedence candidate per instrument before selecting)."""
        if row.get("source") not in VALID_SOURCES:
            return Reason.CANDIDATE_MALFORMED
        if row.get("status") != "ACTIVE":
            return Reason.CANDIDATE_MALFORMED
        if not row.get("instrument_uid") or not row.get("listing_uid"):
            return Reason.CANDIDATE_MALFORMED
        # A PRESENT-but-unparseable date or a PRESENT-but-non-integer TTL is corruption. A NULL
        # value is NOT malformed here: the existing selection logic treats a missing
        # generation date / TTL as a stale-or-expired (non-effective) candidate, not corruption.
        ef = row.get("effective_from_trading_date")
        if ef is not None and not _valid_date(ef):
            return Reason.CANDIDATE_MALFORMED
        gd = row.get("generation_trading_date")
        if gd is not None and not _valid_date(gd):
            return Reason.CANDIDATE_MALFORMED
        ttl = row.get("ttl_sessions_remaining")
        if ttl is not None and not _is_int(ttl):
            return Reason.CANDIDATE_MALFORMED
        return None

    @staticmethod
    def _audit(conn, candidate_id, event_type, trading_date, source, instrument_uid,
               listing_uid, reason_code, payload_hash, resolver_version):
        conn.execute(
            "INSERT INTO candidate_audit (candidate_id, event_type, trading_date, source, "
            "instrument_uid, listing_uid, reason_code, source_payload_hash, resolver_version, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (candidate_id, event_type, _iso(trading_date), source, instrument_uid,
             listing_uid, reason_code, payload_hash, resolver_version, _utc_now_iso()))

    # ── single-candidate submission (MANUAL / TTI; also single AUTO) ──
    def submit_candidate_atomic(self, *, source, source_candidate_key, instrument_uid,
                                listing_uid, trading_date, resolver_version,
                                source_payload_hash, ttl_sessions=None, effective_from=None,
                                _fault_hook=None) -> dict:
        if source not in VALID_SOURCES:
            raise ValueError(f"unknown candidate source {source!r}")
        submission_key = f"{source}|{source_candidate_key}|{_iso(trading_date)}"
        cand_id = _candidate_id(submission_key, instrument_uid, listing_uid, source_payload_hash)
        ttl = CANDIDATE_TTL_SESSIONS if ttl_sessions is None else int(ttl_sessions)
        eff_from = _iso(effective_from) if effective_from is not None else _iso(trading_date)
        now = _utc_now_iso()

        def fault(seam):
            if _fault_hook is not None:
                _fault_hook(seam)

        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.isolation_level = None
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = _row(conn.execute(
                    "SELECT * FROM candidates WHERE submission_key=?", (submission_key,)))
                if existing is not None:
                    same = (existing["source"] == source
                            and existing["instrument_uid"] == instrument_uid
                            and existing["listing_uid"] == listing_uid
                            and existing["source_payload_hash"] == source_payload_hash)
                    conn.execute("ROLLBACK")
                    if same:
                        return {"candidate_id": existing["candidate_id"],
                                "status": existing["status"], "idempotent": True,
                                "reason": existing["deactivation_reason"]}
                    raise CandidateConflictError(
                        f"submission_key {submission_key!r} reused with divergent content")

                ok, reason = self._verify_identity(conn, instrument_uid, listing_uid)
                status = "ACTIVE" if ok else "REJECTED"

                if ok:
                    prior = _row(conn.execute(
                        "SELECT candidate_id FROM candidates WHERE source=? AND "
                        "source_candidate_key=? AND instrument_uid=? AND listing_uid=? "
                        "AND status='ACTIVE'",
                        (source, source_candidate_key, instrument_uid, listing_uid)))
                    if prior is not None:
                        conn.execute(
                            "UPDATE candidates SET status='SUPERSEDED', "
                            "superseded_by_candidate_id=?, deactivated_at=?, "
                            "deactivation_reason='superseded_by_resubmission', updated_at=? "
                            "WHERE candidate_id=?",
                            (cand_id, now, now, prior["candidate_id"]))
                        self._audit(conn, prior["candidate_id"], "SUPERSEDED", trading_date,
                                    source, instrument_uid, listing_uid,
                                    "superseded_by_resubmission", source_payload_hash,
                                    resolver_version)
                        fault("after_supersede")

                conn.execute(
                    "INSERT INTO candidates (candidate_id, submission_key, source, "
                    "source_candidate_key, instrument_uid, listing_uid, submitted_trading_date, "
                    "effective_from_trading_date, status, ttl_sessions_remaining, "
                    "deactivation_reason, source_payload_hash, resolver_version, created_at, "
                    "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cand_id, submission_key, source, source_candidate_key, instrument_uid,
                     listing_uid, _iso(trading_date), eff_from, status,
                     (ttl if ok else None), (None if ok else reason), source_payload_hash,
                     resolver_version, now, now))
                fault("after_insert")
                self._audit(conn, cand_id, "SUBMITTED", trading_date, source, instrument_uid,
                            listing_uid, None, source_payload_hash, resolver_version)
                self._audit(conn, cand_id, "ACTIVATED" if ok else "REJECTED", trading_date,
                            source, instrument_uid, listing_uid, reason, source_payload_hash,
                            resolver_version)
                fault("after_audit")
                fault("before_commit")
                conn.execute("COMMIT")
                return {"candidate_id": cand_id, "status": status, "idempotent": False,
                        "reason": reason}
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
        finally:
            conn.close()

    # ── AUTO batch submission ─────────────────────────────────────────
    def submit_auto_batch_atomic(self, *, generation_trading_date, generation_batch_id,
                                 items, resolver_version, _fault_hook=None) -> dict:
        """Atomically persist a new AUTO batch and deactivate prior-session AUTO candidates not
        in this batch. A failed batch never partially replaces the prior batch (all-or-nothing).
        ``items``: list of {source_candidate_key, instrument_uid, listing_uid, source_payload_hash}.
        """
        now = _utc_now_iso()

        def fault(seam):
            if _fault_hook is not None:
                _fault_hook(seam)

        def item_id(it):
            sk = f"AUTO|{generation_batch_id}|{it.get('instrument_uid')}|{it.get('listing_uid')}"
            return _candidate_id(sk, it.get("instrument_uid"), it.get("listing_uid"),
                                 it.get("source_payload_hash")), sk

        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.isolation_level = None
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing_batch = _rows(conn.execute(
                    "SELECT candidate_id, instrument_uid, listing_uid, source_payload_hash "
                    "FROM candidates WHERE source='AUTO' AND generation_batch_id=?",
                    (generation_batch_id,)))
                if existing_batch:
                    want = sorted((it.get("instrument_uid"), it.get("listing_uid"),
                                   it.get("source_payload_hash")) for it in items)
                    have = sorted((r["instrument_uid"], r["listing_uid"],
                                   r["source_payload_hash"]) for r in existing_batch)
                    conn.execute("ROLLBACK")
                    if want == have:
                        return {"generation_batch_id": generation_batch_id,
                                "idempotent": True,
                                "candidate_ids": [r["candidate_id"] for r in existing_batch]}
                    raise CandidateConflictError(
                        f"AUTO batch {generation_batch_id!r} reused with divergent content")

                # Deactivate prior-session ACTIVE AUTO candidates (other batches) FIRST so the
                # ux_candidate_active unique index never collides on a reused coordinate.
                prior = _rows(conn.execute(
                    "SELECT candidate_id, instrument_uid, listing_uid FROM candidates "
                    "WHERE source='AUTO' AND status='ACTIVE' AND generation_batch_id!=?",
                    (generation_batch_id,)))
                for p in prior:
                    conn.execute(
                        "UPDATE candidates SET status='DEACTIVATED', deactivated_at=?, "
                        "deactivation_reason='superseded_by_new_auto_batch', updated_at=? "
                        "WHERE candidate_id=?", (now, now, p["candidate_id"]))
                    self._audit(conn, p["candidate_id"], "DEACTIVATED", generation_trading_date,
                                "AUTO", p["instrument_uid"], p["listing_uid"],
                                "superseded_by_new_auto_batch", None, resolver_version)
                fault("after_deactivate")

                inserted = []
                for it in items:
                    cid, sk = item_id(it)
                    iuid, luid = it.get("instrument_uid"), it.get("listing_uid")
                    ph = it.get("source_payload_hash")
                    ok, reason = self._verify_identity(conn, iuid, luid)
                    status = "ACTIVE" if ok else "REJECTED"
                    conn.execute(
                        "INSERT INTO candidates (candidate_id, submission_key, source, "
                        "source_candidate_key, instrument_uid, listing_uid, "
                        "submitted_trading_date, effective_from_trading_date, status, "
                        "ttl_sessions_remaining, deactivation_reason, source_payload_hash, "
                        "resolver_version, generation_trading_date, generation_batch_id, "
                        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (cid, sk, "AUTO", it.get("source_candidate_key"), iuid, luid,
                         _iso(generation_trading_date), _iso(generation_trading_date), status,
                         (1 if ok else None), (None if ok else reason), ph, resolver_version,
                         _iso(generation_trading_date), generation_batch_id, now, now))
                    self._audit(conn, cid, "SUBMITTED", generation_trading_date, "AUTO",
                                iuid, luid, None, ph, resolver_version)
                    self._audit(conn, cid, "ACTIVATED" if ok else "REJECTED",
                                generation_trading_date, "AUTO", iuid, luid, reason, ph,
                                resolver_version)
                    inserted.append(cid)
                fault("after_insert")
                fault("after_audit")
                fault("before_commit")
                conn.execute("COMMIT")
                return {"generation_batch_id": generation_batch_id, "idempotent": False,
                        "candidate_ids": inserted}
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
        finally:
            conn.close()

    # ── deactivation ─────────────────────────────────────────────────
    def deactivate_candidate_atomic(self, candidate_id, trading_date,
                                    reason=Reason.CANDIDATE_INACTIVE, *, timeout=5.0,
                                    _fault_hook=None) -> None:
        """R2B-P3-4: deactivate one candidate atomically. Uses an explicit ``BEGIN IMMEDIATE``
        (consistent with the other multi-row lifecycle writes) so the status UPDATE and the
        append-only audit INSERT COMMIT together or ROLL BACK together — a fault between them
        leaves the row and its audit consistent. ``BEGIN IMMEDIATE`` takes the write lock up
        front, so a concurrent writer either serializes or fails cleanly (``OperationalError``)
        within ``timeout`` with no partial data; the connection is discarded on rollback and a
        retry uses a fresh connection. ``_fault_hook`` is a test seam only."""
        now = _utc_now_iso()

        def fault(seam):
            if _fault_hook is not None:
                _fault_hook(seam)

        conn = sqlite3.connect(self.db_path, timeout=timeout)
        conn.isolation_level = None
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = _row(conn.execute(
                    "SELECT source, instrument_uid, listing_uid FROM candidates "
                    "WHERE candidate_id=?", (candidate_id,)))
                conn.execute(
                    "UPDATE candidates SET status='DEACTIVATED', deactivated_at=?, "
                    "deactivation_reason=?, updated_at=? WHERE candidate_id=?",
                    (now, reason, now, candidate_id))
                fault("after_update")
                if row:
                    self._audit(conn, candidate_id, "DEACTIVATED", trading_date, row["source"],
                                row["instrument_uid"], row["listing_uid"], reason, None, None)
                fault("after_audit")
                fault("before_commit")
                conn.execute("COMMIT")
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
        finally:
            conn.close()

    # ── TTL session tick (read-then-count; idempotent per date) ───────
    def tick_ttl_atomic(self, trading_date, *, timeout=5.0, _fault_hook=None) -> dict:
        """Count one COMPLETED trading session against every ACTIVE MANUAL/TTI candidate, exactly
        once per date (guarded by last_counted_trading_date). Decrement TTL; at zero mark EXPIRED.
        AUTO candidates are session-scoped (not TTL-counted). Caller passes only completed
        sessions; a duplicate/earlier date is a no-op (no double decrement).

        R2B-P3-4: the whole tick (every TTL decrement, every expiry status flip, and every
        EXPIRED audit insert) runs inside ONE explicit ``BEGIN IMMEDIATE`` transaction — a fault
        AFTER any TTL update, expiry update, or audit insert rolls the WHOLE batch back (no
        partial decrement, no orphan audit). A concurrent writer serializes or fails cleanly
        within ``timeout`` with no partial data. ``_fault_hook`` is a test seam only."""
        td = _iso(trading_date)
        now = _utc_now_iso()
        counted, expired = 0, 0

        def fault(seam):
            if _fault_hook is not None:
                _fault_hook(seam)

        conn = sqlite3.connect(self.db_path, timeout=timeout)
        conn.isolation_level = None
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                active = _rows(conn.execute(
                    "SELECT candidate_id, source, instrument_uid, listing_uid, "
                    "ttl_sessions_remaining, last_counted_trading_date FROM candidates "
                    "WHERE status='ACTIVE' AND source IN ('MANUAL','TTI')"))
                for c in active:
                    last = c["last_counted_trading_date"]
                    if last is not None and td <= str(last):
                        continue                      # duplicate/earlier date → no decrement
                    new_ttl = int(c["ttl_sessions_remaining"] or 0) - 1
                    if new_ttl <= 0:
                        conn.execute(
                            "UPDATE candidates SET ttl_sessions_remaining=0, status='EXPIRED', "
                            "last_counted_trading_date=?, deactivated_at=?, "
                            "deactivation_reason=?, updated_at=? WHERE candidate_id=?",
                            (td, now, Reason.CANDIDATE_EXPIRED, now, c["candidate_id"]))
                        fault("after_expiry_update")
                        self._audit(conn, c["candidate_id"], "EXPIRED", trading_date,
                                    c["source"], c["instrument_uid"], c["listing_uid"],
                                    Reason.CANDIDATE_EXPIRED, None, None)
                        fault("after_expiry_audit")
                        expired += 1
                    else:
                        conn.execute(
                            "UPDATE candidates SET ttl_sessions_remaining=?, "
                            "last_counted_trading_date=?, updated_at=? WHERE candidate_id=?",
                            (new_ttl, td, now, c["candidate_id"]))
                        fault("after_ttl_update")
                    counted += 1
                fault("before_commit")
                conn.execute("COMMIT")
                return {"counted": counted, "expired": expired}
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
        finally:
            conn.close()

    # ── reads ─────────────────────────────────────────────────────────
    def get_candidate(self, candidate_id) -> Optional[dict]:
        with connect(self.db_path) as conn:
            return _row(conn.execute(
                "SELECT * FROM candidates WHERE candidate_id=?", (candidate_id,)))

    def audit_events(self, candidate_id) -> list:
        with connect(self.db_path) as conn:
            return _rows(conn.execute(
                "SELECT * FROM candidate_audit WHERE candidate_id=? ORDER BY id",
                (candidate_id,)))

    def active_candidates(self, trading_date=None) -> list:
        td = _iso(trading_date)
        with connect(self.db_path) as conn:
            rows = _rows(conn.execute(
                "SELECT * FROM candidates WHERE status='ACTIVE' ORDER BY candidate_id"))
        if td is None:
            return rows
        return [r for r in rows
                if r["effective_from_trading_date"] is None
                or str(r["effective_from_trading_date"]) <= td]

    def effective_candidates(self, trading_date) -> EffectiveSelection:
        """The single effective candidate per instrument_uid for ``trading_date``, applying
        frozen precedence (MANUAL>TTI>AUTO), structural validation, identity/listing
        re-validation, and TTL/session validity — fail closed. The ONLY selection-input API.
        Pure read (no writes).

        R2B-P3-1 (error observability): a whole-store read failure (locked DB, missing table,
        any sqlite error) does NOT propagate — it returns ``store_unavailable=True`` so the
        caller blocks every new entry with ``candidate_store_unavailable`` (no crash, no TTL
        mutation).

        R2B-P3-2 (unknown source/status): a raw/injected ACTIVE row with an unknown source,
        non-integer TTL, or garbage effective/generation date is validated on the
        HIGHEST-precedence candidate per instrument and blocks that instrument with
        ``candidate_malformed`` — never falling through to a lower-precedence candidate. A row
        carrying a literal unknown (non-enum) status is caught by a separate defensive pass."""
        td = _iso(trading_date)
        sel = EffectiveSelection()
        try:
            with connect(self.db_path) as conn:
                rows = _rows(conn.execute(
                    "SELECT * FROM candidates WHERE status='ACTIVE'"))
                # ── R2B-P3-2: a row with a literal unknown (non-enum) status is corruption.
                # Surface it as malformed for its instrument WITHOUT pulling it into the
                # precedence/suppression path (so it never produces a spurious SUPPRESSED audit).
                placeholders = ",".join("?" * len(VALID_STATUSES))
                for r in _rows(conn.execute(
                        "SELECT DISTINCT instrument_uid FROM candidates "
                        f"WHERE status NOT IN ({placeholders}) "
                        "AND instrument_uid IS NOT NULL", tuple(sorted(VALID_STATUSES)))):
                    sel.blocked.setdefault(r["instrument_uid"], Reason.CANDIDATE_MALFORMED)
                # Group ACTIVE rows by instrument. A validly future-dated row is not yet
                # effective and is skipped; a row with an INVALID effective date is NOT
                # silently dropped — it stays in contention so a malformed higher-precedence
                # candidate cannot be hidden (the no-fall-through guarantee).
                groups = {}
                for r in rows:
                    ef = r["effective_from_trading_date"]
                    if ef is not None and _valid_date(ef) and str(ef) > td:
                        continue
                    groups.setdefault(r["instrument_uid"], []).append(r)
                for iuid, cands in groups.items():
                    cands.sort(
                        key=lambda c: (PRECEDENCE.get(c["source"], 99), c["candidate_id"]))
                    top = cands[0]
                    # §4 conservative rule, extended: an ACTIVE higher-precedence candidate that
                    # is structurally malformed OR fails identity/listing re-validation BLOCKS
                    # the instrument — never fall through to a lower-precedence candidate.
                    mal = self._candidate_row_malformed(top)
                    if mal is not None:
                        sel.blocked[iuid] = mal
                        continue
                    ok, _reason = self._verify_identity(conn, iuid, top["listing_uid"])
                    if not ok:
                        sel.blocked[iuid] = Reason.CANDIDATE_SOURCE_CONFLICT
                        continue
                    if top["source"] == "AUTO":
                        if str(top["generation_trading_date"]) != td:
                            sel.blocked[iuid] = Reason.CANDIDATE_EXPIRED   # stale AUTO session
                            continue
                    elif int(top["ttl_sessions_remaining"] or 0) < 1:
                        sel.blocked[iuid] = Reason.CANDIDATE_EXPIRED
                        continue
                    sel.effective[iuid] = top
                    # Only well-formed siblings are recorded as suppressed (a malformed sibling
                    # must not generate a SUPPRESSED audit for a corrupt row).
                    sel.suppressed.extend(
                        c["candidate_id"] for c in cands[1:]
                        if self._candidate_row_malformed(c) is None)
        except sqlite3.Error:
            # R2B-P3-1: fail closed on any candidate-store/database read failure.
            return EffectiveSelection(store_unavailable=True)
        return sel

    def record_selection_audit(self, trading_date, selection) -> dict:
        """R2B-P3-3: persist deterministic, idempotent selection-audit events for ONE
        evaluation — SELECTED_EFFECTIVE for each effective candidate and SUPPRESSED for each
        suppressed candidate. Called only on the gate-enabled path; the default-off path never
        invokes it (zero candidate audit writes).

        Idempotent per (candidate_id, trading_date, event_type) via a check-then-insert guarded
        by the ``BEGIN IMMEDIATE`` write lock (concurrent same-date evaluations serialize on the
        lock, so the second sees the first's committed rows and writes nothing) — a duplicate
        same-date evaluation creates no new rows. NO schema change is required (see the R2B
        residuals completion doc, "No schema v8 required"). Append-only is preserved: only
        INSERTs occur — existing audit rows are never updated or deleted. All inserts run inside
        ONE explicit ``BEGIN IMMEDIATE`` transaction (atomic; a fault rolls back the whole
        batch). Writes NOTHING when there is nothing to record."""
        td = _iso(trading_date)
        # trading_date MUST be non-null: it is part of the idempotency key, and SQLite treats
        # NULLs as distinct in a unique index (a NULL date would defeat de-duplication).
        if td is None:
            raise ValueError("record_selection_audit requires a non-null trading_date")
        now = _utc_now_iso()
        eff_rows = [(c["candidate_id"], c.get("source"), c.get("instrument_uid"),
                     c.get("listing_uid")) for c in selection.effective.values()]
        sup_ids = list(selection.suppressed)
        if not eff_rows and not sup_ids:
            return {"selected": 0, "suppressed": 0}
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.isolation_level = None
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for cid, src, iuid, luid in eff_rows:
                    self._audit_idempotent(conn, cid, EVENT_SELECTED_EFFECTIVE, td, src,
                                           iuid, luid, None)
                for cid in sup_ids:
                    r = _row(conn.execute(
                        "SELECT source, instrument_uid, listing_uid FROM candidates "
                        "WHERE candidate_id=?", (cid,)))
                    src = r["source"] if r else None
                    iuid = r["instrument_uid"] if r else None
                    luid = r["listing_uid"] if r else None
                    self._audit_idempotent(conn, cid, EVENT_SUPPRESSED, td, src, iuid, luid,
                                           Reason.SUPPRESSED_BY_HIGHER_PRECEDENCE_SOURCE)
                conn.execute("COMMIT")
                return {"selected": len(eff_rows), "suppressed": len(sup_ids)}
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
        finally:
            conn.close()

    @staticmethod
    def _audit_idempotent(conn, candidate_id, event_type, trading_date, source,
                          instrument_uid, listing_uid, reason_code):
        """Append a selection-audit row at most once per (candidate_id, trading_date,
        event_type). Check-then-insert under the caller's BEGIN IMMEDIATE write lock: a row
        already present (from an earlier same-date evaluation) is a no-op; otherwise one row is
        appended. No UPDATE/DELETE ever occurs (append-only preserved)."""
        td = _iso(trading_date)
        exists = conn.execute(
            "SELECT 1 FROM candidate_audit WHERE candidate_id=? AND trading_date=? "
            "AND event_type=? LIMIT 1", (candidate_id, td, event_type)).fetchone()
        if exists:
            return
        conn.execute(
            "INSERT INTO candidate_audit (candidate_id, event_type, trading_date, "
            "source, instrument_uid, listing_uid, reason_code, source_payload_hash, "
            "resolver_version, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (candidate_id, event_type, td, source, instrument_uid,
             listing_uid, reason_code, None, None, _utc_now_iso()))
