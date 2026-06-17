"""R2B candidate TTL semantics (P3-4 §5), read-then-count.

Frozen: MANUAL/TTI initial TTL = 5 completed sessions; a candidate effective at session E is
EFFECTIVE on E..E+4 and EXPIRED from E+5. The tick is idempotent per date and never decrements
for a duplicate same-date run, a missing-bar/weekend/non-session, or a pre-session outage
(the caller passes only completed sessions; same/earlier dates are no-ops).
"""
from bot.universe.candidate_store import CandidateStore
from bot.universe.params import CANDIDATE_TTL_SESSIONS
from tests.universe._fixtures import resolve_instrument

VER = "r2b_resolver_v1"


def _setup(tmp_path):
    db = str(tmp_path / "universe.db")
    a = resolve_instrument(db, "AAPL")
    cs = CandidateStore(db)
    r = cs.submit_candidate_atomic(source="MANUAL", source_candidate_key="m",
                                   instrument_uid=a["instrument_uid"], listing_uid=a["listing_uid"],
                                   trading_date="2026-06-12", resolver_version=VER,
                                   source_payload_hash="h")
    return cs, a["instrument_uid"], r["candidate_id"]


def test_initial_ttl_is_five(tmp_path):
    cs, iuid, cid = _setup(tmp_path)
    assert CANDIDATE_TTL_SESSIONS == 5
    assert cs.get_candidate(cid)["ttl_sessions_remaining"] == 5


def test_effective_for_five_sessions_then_expires(tmp_path):
    cs, iuid, cid = _setup(tmp_path)
    sessions = ["2026-06-12", "2026-06-13", "2026-06-15", "2026-06-16", "2026-06-17"]  # E..E+4
    for d in sessions:
        assert iuid in cs.effective_candidates(d).effective, f"should be effective on {d}"
        cs.tick_ttl_atomic(d)                       # count the completed session AFTER selecting
    # E+5: the candidate is now EXPIRED (status changed at E+4's tick), so it is no longer an
    # ACTIVE candidate — absent from both effective and blocked (selection treats the instrument
    # as having no effective candidate → candidate_inactive at the evaluator gate).
    after = "2026-06-18"
    sel = cs.effective_candidates(after)
    assert iuid not in sel.effective and iuid not in sel.blocked
    row = cs.get_candidate(cid)
    assert row["status"] == "EXPIRED" and row["ttl_sessions_remaining"] == 0


def test_duplicate_same_date_tick_does_not_double_decrement(tmp_path):
    cs, iuid, cid = _setup(tmp_path)
    cs.tick_ttl_atomic("2026-06-12")
    assert cs.get_candidate(cid)["ttl_sessions_remaining"] == 4
    cs.tick_ttl_atomic("2026-06-12")               # duplicate same-date
    cs.tick_ttl_atomic("2026-06-12")
    assert cs.get_candidate(cid)["ttl_sessions_remaining"] == 4   # unchanged


def test_earlier_date_tick_does_not_decrement(tmp_path):
    # a missing-bar/weekend/out-of-order/pre-session date must not count.
    cs, iuid, cid = _setup(tmp_path)
    cs.tick_ttl_atomic("2026-06-13")
    assert cs.get_candidate(cid)["ttl_sessions_remaining"] == 4
    cs.tick_ttl_atomic("2026-06-12")               # earlier than last counted → no-op
    assert cs.get_candidate(cid)["ttl_sessions_remaining"] == 4


def test_expired_candidate_cannot_enter(tmp_path):
    cs, iuid, cid = _setup(tmp_path)
    cs.deactivate_candidate_atomic(cid, "2026-06-12", reason="candidate_expired")
    # not effective once inactive
    assert iuid not in cs.effective_candidates("2026-06-12").effective
