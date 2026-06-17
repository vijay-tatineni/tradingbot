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
from datetime import datetime, timezone
from typing import Optional

from bot.universe.db import connect, migrate
from bot.universe.models import Reason
from bot.universe.params import CANDIDATE_TTL_SESSIONS

# Frozen source precedence (lower wins).
PRECEDENCE = {"MANUAL": 0, "TTI": 1, "AUTO": 2}
VALID_SOURCES = frozenset(PRECEDENCE)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    the blocked instruments (with a fail-closed reason), and the suppressed candidate ids."""
    effective: dict = field(default_factory=dict)     # instrument_uid -> candidate row
    blocked: dict = field(default_factory=dict)       # instrument_uid -> reason_code
    suppressed: list = field(default_factory=list)    # [candidate_id, ...]


class CandidateStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        migrate(db_path)   # idempotent; brings the DB to schema v6

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
                                    reason=Reason.CANDIDATE_INACTIVE) -> None:
        now = _utc_now_iso()
        with connect(self.db_path) as conn:
            row = _row(conn.execute(
                "SELECT source, instrument_uid, listing_uid FROM candidates WHERE candidate_id=?",
                (candidate_id,)))
            conn.execute(
                "UPDATE candidates SET status='DEACTIVATED', deactivated_at=?, "
                "deactivation_reason=?, updated_at=? WHERE candidate_id=?",
                (now, reason, now, candidate_id))
            if row:
                self._audit(conn, candidate_id, "DEACTIVATED", trading_date, row["source"],
                            row["instrument_uid"], row["listing_uid"], reason, None, None)

    # ── TTL session tick (read-then-count; idempotent per date) ───────
    def tick_ttl_atomic(self, trading_date) -> dict:
        """Count one COMPLETED trading session against every ACTIVE MANUAL/TTI candidate, exactly
        once per date (guarded by last_counted_trading_date). Decrement TTL; at zero mark EXPIRED.
        AUTO candidates are session-scoped (not TTL-counted). Caller passes only completed
        sessions; a duplicate/earlier date is a no-op (no double decrement)."""
        td = _iso(trading_date)
        now = _utc_now_iso()
        counted, expired = 0, 0
        conn = sqlite3.connect(self.db_path, timeout=5.0)
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
                        self._audit(conn, c["candidate_id"], "EXPIRED", trading_date,
                                    c["source"], c["instrument_uid"], c["listing_uid"],
                                    Reason.CANDIDATE_EXPIRED, None, None)
                        expired += 1
                    else:
                        conn.execute(
                            "UPDATE candidates SET ttl_sessions_remaining=?, "
                            "last_counted_trading_date=?, updated_at=? WHERE candidate_id=?",
                            (new_ttl, td, now, c["candidate_id"]))
                    counted += 1
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
        frozen precedence (MANUAL>TTI>AUTO), identity/listing re-validation, and TTL/session
        validity — fail closed. The ONLY selection-input API. Pure read (no writes)."""
        td = _iso(trading_date)
        sel = EffectiveSelection()
        with connect(self.db_path) as conn:
            active = [r for r in _rows(conn.execute(
                "SELECT * FROM candidates WHERE status='ACTIVE'"))
                if r["effective_from_trading_date"] is None
                or str(r["effective_from_trading_date"]) <= td]
            groups = {}
            for r in active:
                groups.setdefault(r["instrument_uid"], []).append(r)
            for iuid, cands in groups.items():
                cands.sort(key=lambda c: (PRECEDENCE.get(c["source"], 99), c["candidate_id"]))
                top = cands[0]
                # §4 conservative rule: an ACTIVE higher-precedence candidate that fails
                # identity/listing re-validation BLOCKS the instrument — never fall through.
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
                sel.suppressed.extend(c["candidate_id"] for c in cands[1:])
        return sel
