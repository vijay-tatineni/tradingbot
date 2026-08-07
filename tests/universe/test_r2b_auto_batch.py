"""R2B AUTO batch behavior (P3-4 §7): atomic per-session replacement, idempotent replay,
divergent-replay conflict, failed-batch leaves prior batch unchanged.
"""
import sqlite3

import pytest

from bot.universe.candidate_store import CandidateConflictError, CandidateStore
from bot.universe.db import connect
from tests.universe._fixtures import iref, resolve_instrument

VER = "r2b_resolver_v1"


def _two_instruments(tmp_path):
    db = str(tmp_path / "universe.db")
    a = resolve_instrument(db, "AAPL", isin="US0378331005", conid="1")
    from bot.universe.identity_store import IdentityStore
    st = IdentityStore(db)
    b = st.resolve_identity_atomic(
        iref("MSFT", isin="US5949181045", figi=None, mic="XNAS", currency="USD", conid="2",
             exchange="XNAS", effective="2026-06-10", verified="2026-06-10T12:00:00"),
        "2026-06-12", VER)
    return db, a, b


def _item(r, ph="p"):
    return {"source_candidate_key": r["instrument_uid"], "instrument_uid": r["instrument_uid"],
            "listing_uid": r["listing_uid"], "source_payload_hash": ph}


def test_new_batch_replaces_prior_session_auto_atomically(tmp_path):
    db, a, b = _two_instruments(tmp_path)
    cs = CandidateStore(db)
    # batch 1 (session 1): AAPL + MSFT
    cs.submit_auto_batch_atomic(generation_trading_date="2026-06-12", generation_batch_id="b1",
                                items=[_item(a), _item(b)], resolver_version=VER)
    # batch 2 (session 2): only AAPL
    cs.submit_auto_batch_atomic(generation_trading_date="2026-06-13", generation_batch_id="b2",
                                items=[_item(a)], resolver_version=VER)
    # session 2: AAPL effective via b2; MSFT's prior-batch AUTO deactivated (not effective)
    eff2 = cs.effective_candidates("2026-06-13")
    assert a["instrument_uid"] in eff2.effective
    assert eff2.effective[a["instrument_uid"]]["generation_batch_id"] == "b2"
    assert b["instrument_uid"] not in eff2.effective
    # the prior MSFT AUTO row is retained (DEACTIVATED), audited
    with connect(db) as conn:
        msft_rows = conn.execute(
            "SELECT status FROM candidates WHERE instrument_uid=? AND source='AUTO'",
            (b["instrument_uid"],)).fetchall()
    assert any(s[0] == "DEACTIVATED" for s in msft_rows)


def test_same_batch_replay_is_idempotent(tmp_path):
    db, a, b = _two_instruments(tmp_path)
    cs = CandidateStore(db)
    r1 = cs.submit_auto_batch_atomic(generation_trading_date="2026-06-12", generation_batch_id="b1",
                                     items=[_item(a), _item(b)], resolver_version=VER)
    r2 = cs.submit_auto_batch_atomic(generation_trading_date="2026-06-12", generation_batch_id="b1",
                                     items=[_item(a), _item(b)], resolver_version=VER)
    assert r2["idempotent"] is True
    with connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM candidates WHERE generation_batch_id='b1'").fetchone()[0]
    assert n == 2                                   # no duplicate rows


def test_divergent_batch_replay_conflicts(tmp_path):
    db, a, b = _two_instruments(tmp_path)
    cs = CandidateStore(db)
    cs.submit_auto_batch_atomic(generation_trading_date="2026-06-12", generation_batch_id="b1",
                                items=[_item(a, ph="p1")], resolver_version=VER)
    with pytest.raises(CandidateConflictError):
        cs.submit_auto_batch_atomic(generation_trading_date="2026-06-12", generation_batch_id="b1",
                                    items=[_item(a, ph="DIVERGENT")], resolver_version=VER)


@pytest.mark.parametrize("seam", ["after_deactivate", "after_insert", "after_audit",
                                  "before_commit"])
def test_failed_batch_leaves_prior_batch_unchanged(tmp_path, seam):
    db, a, b = _two_instruments(tmp_path)
    cs = CandidateStore(db)
    cs.submit_auto_batch_atomic(generation_trading_date="2026-06-12", generation_batch_id="b1",
                                items=[_item(a), _item(b)], resolver_version=VER)

    def boom(s):
        if s == seam:
            raise RuntimeError(f"injected {seam}")

    with pytest.raises(RuntimeError):
        cs.submit_auto_batch_atomic(generation_trading_date="2026-06-13", generation_batch_id="b2",
                                    items=[_item(a)], resolver_version=VER, _fault_hook=boom)
    # prior batch b1 fully intact: both rows still ACTIVE, no b2 rows committed
    with connect(db) as conn:
        b1_active = conn.execute(
            "SELECT COUNT(*) FROM candidates WHERE generation_batch_id='b1' AND status='ACTIVE'"
        ).fetchone()[0]
        b2_any = conn.execute(
            "SELECT COUNT(*) FROM candidates WHERE generation_batch_id='b2'").fetchone()[0]
    assert b1_active == 2 and b2_any == 0
