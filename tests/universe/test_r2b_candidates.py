"""R2B persisted candidate store (P3-4): precedence, identity/listing enforcement,
resubmission/supersession, fail-closed matrix, atomic rollback, concurrency.

All identity/candidate fixtures are broker-free; no broker/data provider/live DB is touched.
"""
import sqlite3

import pytest

from bot.universe.candidate_store import CandidateConflictError, CandidateStore, PRECEDENCE
from bot.universe.db import connect
from bot.universe.models import Reason
from tests.universe._fixtures import resolve_instrument

TD = "2026-06-12"
VER = "r2b_resolver_v1"


def _setup(tmp_path):
    db = str(tmp_path / "universe.db")
    a = resolve_instrument(db, "AAPL", isin="US0378331005")
    return db, a["instrument_uid"], a["listing_uid"]


def _submit(cs, source, key, iuid, luid, ph="h", td=TD, ttl=None):
    return cs.submit_candidate_atomic(
        source=source, source_candidate_key=key, instrument_uid=iuid, listing_uid=luid,
        trading_date=td, resolver_version=VER, source_payload_hash=ph, ttl_sessions=ttl)


# ── precedence ──────────────────────────────────────────────────────────────────
def test_manual_beats_tti_and_auto(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    cs = CandidateStore(db)
    _submit(cs, "AUTO", "a", iuid, luid, ph="a")
    _submit(cs, "TTI", "t", iuid, luid, ph="t")
    _submit(cs, "MANUAL", "m", iuid, luid, ph="m")
    eff = cs.effective_candidates(TD)
    assert eff.effective[iuid]["source"] == "MANUAL"
    assert len(eff.suppressed) == 2          # TTI + AUTO suppressed but retained
    # suppressed candidates remain ACTIVE/auditable (not deleted)
    for cid in eff.suppressed:
        assert cs.get_candidate(cid)["status"] == "ACTIVE"


def test_tti_beats_auto(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    cs = CandidateStore(db)
    _submit(cs, "AUTO", "a", iuid, luid, ph="a")
    _submit(cs, "TTI", "t", iuid, luid, ph="t")
    assert cs.effective_candidates(TD).effective[iuid]["source"] == "TTI"


def test_only_auto_is_effective_when_alone(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    cs = CandidateStore(db)
    cs.submit_auto_batch_atomic(generation_trading_date=TD, generation_batch_id="b1",
                                items=[{"source_candidate_key": "a", "instrument_uid": iuid,
                                        "listing_uid": luid, "source_payload_hash": "a"}],
                                resolver_version=VER)
    assert cs.effective_candidates(TD).effective[iuid]["source"] == "AUTO"


def test_precedence_order_frozen():
    assert PRECEDENCE["MANUAL"] < PRECEDENCE["TTI"] < PRECEDENCE["AUTO"]


def test_active_higher_precedence_invalid_blocks_no_fallthrough(tmp_path):
    # MANUAL (listing A) + TTI (listing B). If MANUAL's listing later becomes invalid, the
    # instrument is BLOCKED (candidate_source_conflict) — it must NOT silently fall through to
    # the TTI listing.
    db = str(tmp_path / "universe.db")
    a = resolve_instrument(db, "AAPL", isin="US0378331005", mic="XNAS", currency="USD")
    iuid = a["instrument_uid"]
    luid_a = a["listing_uid"]
    # a second active listing B (GBP/XLON) on the SAME instrument (dual listing)
    from bot.universe.identity_store import IdentityStore
    from tests.universe._fixtures import iref
    st = IdentityStore(db)
    b = st.resolve_identity_atomic(
        iref("AAPL", isin="US0378331005", figi=None, mic="XLON", currency="GBP", conid="2",
             exchange="XLON", effective="2026-06-10", verified="2026-06-10T12:00:00"),
        TD, VER)
    luid_b = b["listing_uid"]
    cs = CandidateStore(db)
    _submit(cs, "MANUAL", "m", iuid, luid_a, ph="m")
    _submit(cs, "TTI", "t", iuid, luid_b, ph="t")
    assert cs.effective_candidates(TD).effective[iuid]["source"] == "MANUAL"
    # invalidate MANUAL's listing A (delist)
    st.close_listing_atomic(luid_a, TD)
    eff = cs.effective_candidates(TD)
    assert iuid not in eff.effective                          # NOT TTI fall-through
    assert eff.blocked[iuid] == Reason.CANDIDATE_SOURCE_CONFLICT


# ── identity / listing enforcement ──────────────────────────────────────────────
def test_ticker_only_candidate_rejected(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    cs = CandidateStore(db)
    r = _submit(cs, "MANUAL", "x", None, None)
    assert r["status"] == "REJECTED" and r["reason"] == Reason.CANDIDATE_IDENTITY_UNRESOLVED


def test_unknown_instrument_uid_rejected(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    cs = CandidateStore(db)
    r = _submit(cs, "MANUAL", "x", "iid_doesnotexist", luid)
    assert r["status"] == "REJECTED" and r["reason"] == Reason.CANDIDATE_IDENTITY_UNRESOLVED


def test_unknown_listing_uid_rejected(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    cs = CandidateStore(db)
    r = _submit(cs, "MANUAL", "x", iuid, "lst_doesnotexist")
    assert r["status"] == "REJECTED" and r["reason"] == Reason.CANDIDATE_LISTING_UNVERIFIED


def test_missing_listing_with_instrument_is_ambiguous(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    cs = CandidateStore(db)
    r = _submit(cs, "MANUAL", "x", iuid, None)
    assert r["status"] == "REJECTED" and r["reason"] == Reason.CANDIDATE_LISTING_AMBIGUOUS


def test_verified_instrument_listing_accepted(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    cs = CandidateStore(db)
    r = _submit(cs, "MANUAL", "m", iuid, luid)
    assert r["status"] == "ACTIVE" and r["reason"] is None
    assert iuid in cs.effective_candidates(TD).effective


def test_rejected_candidate_never_effective(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    cs = CandidateStore(db)
    _submit(cs, "MANUAL", "x", None, None)        # rejected
    assert cs.effective_candidates(TD).effective == {}


# ── resubmission / supersession ─────────────────────────────────────────────────
def test_resubmission_supersedes_and_retains_old(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    cs = CandidateStore(db)
    first = _submit(cs, "MANUAL", "m", iuid, luid, ph="v1", td="2026-06-12")
    second = _submit(cs, "MANUAL", "m", iuid, luid, ph="v2", td="2026-06-13")
    assert first["candidate_id"] != second["candidate_id"]
    old = cs.get_candidate(first["candidate_id"])
    assert old["status"] == "SUPERSEDED"                       # retained, not deleted
    assert old["superseded_by_candidate_id"] == second["candidate_id"]
    assert cs.get_candidate(second["candidate_id"])["status"] == "ACTIVE"
    # audit complete
    events = {e["event_type"] for e in cs.audit_events(first["candidate_id"])}
    assert {"SUBMITTED", "ACTIVATED", "SUPERSEDED"}.issubset(events)


def test_identical_replay_is_idempotent_no_op(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    cs = CandidateStore(db)
    r1 = _submit(cs, "MANUAL", "m", iuid, luid, ph="v1")
    r2 = _submit(cs, "MANUAL", "m", iuid, luid, ph="v1")
    assert r2["idempotent"] is True and r2["candidate_id"] == r1["candidate_id"]
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1


def test_conflicting_replay_raises(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    cs = CandidateStore(db)
    _submit(cs, "MANUAL", "m", iuid, luid, ph="v1")
    with pytest.raises(CandidateConflictError):
        _submit(cs, "MANUAL", "m", iuid, luid, ph="DIVERGENT")     # same submission_key


# ── atomic rollback ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("seam", ["after_supersede", "after_insert", "after_audit",
                                  "before_commit"])
def test_submit_fault_rolls_back_fully(tmp_path, seam):
    db, iuid, luid = _setup(tmp_path)
    cs = CandidateStore(db)
    # an ACTIVE prior so the after_supersede seam is exercised
    first = _submit(cs, "MANUAL", "m", iuid, luid, ph="v1", td="2026-06-12")

    def boom(s):
        if s == seam:
            raise RuntimeError(f"injected {seam}")

    with pytest.raises(RuntimeError):
        cs.submit_candidate_atomic(source="MANUAL", source_candidate_key="m",
                                   instrument_uid=iuid, listing_uid=luid, trading_date="2026-06-13",
                                   resolver_version=VER, source_payload_hash="v2", _fault_hook=boom)
    # the prior candidate is NOT left superseded; no new row was committed
    assert cs.get_candidate(first["candidate_id"])["status"] == "ACTIVE"
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1


def test_concurrent_writer_serialized_by_begin_immediate(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    cs = CandidateStore(db)
    blocker = sqlite3.connect(db, timeout=0.1)
    blocker.isolation_level = None
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("CREATE TABLE _hold (x)")        # hold the write lock
    try:
        with pytest.raises(sqlite3.OperationalError):
            cs2 = CandidateStore(db)
            # a fresh store submit must fail fast (database locked), not corrupt state
            cs2.submit_candidate_atomic(source="MANUAL", source_candidate_key="z",
                                        instrument_uid=iuid, listing_uid=luid, trading_date=TD,
                                        resolver_version=VER, source_payload_hash="z")
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    # after the lock releases a submit succeeds
    assert _submit(cs, "MANUAL", "z", iuid, luid, ph="z")["status"] == "ACTIVE"


# ── append-only candidate audit ──────────────────────────────────────────────────
def test_candidate_audit_is_append_only(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    CandidateStore(db)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO candidate_audit (event_type, created_at) VALUES ('X','t')")
    con.commit()
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE candidate_audit SET event_type='Y'"); con.commit()
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM candidate_audit"); con.commit()
    con.close()
