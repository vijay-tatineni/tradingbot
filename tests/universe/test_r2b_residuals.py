"""R2B residual fixes (R2B-P3-1..4): candidate-store error observability, unknown
source/status defensive rejection, selection-audit completeness/idempotency, and
transaction-consistency / TTL fault coverage.

Temporary DBs only — no broker, no live provider, no production database. The feature stays
default-off and un-wired; these tests exercise the store/evaluator seams directly.
"""
import pathlib
import sqlite3
import threading
import time

import pytest

from bot.universe.candidate_store import (
    EVENT_SELECTED_EFFECTIVE, EVENT_SUPPRESSED, CandidateStore,
)
from bot.universe.evaluator import ShadowEvaluator
from bot.universe.models import PositionStatus, Reason, State
from bot.universe.registry import Registry
from bot.universe.seed import canonical_id, seed_registry
from tests.universe._fixtures import (
    ON, SpyProvider, StubPositionProvider, flat, inst, make_bars, resolve_instrument,
    write_configs,
)

TD = "2026-06-12"
VER = "r2b_resolver_v1"


# ── helpers ───────────────────────────────────────────────────────────────────────
def _setup(tmp_path, symbol="AAPL", isin="US0378331005", cid=None):
    db = str(tmp_path / "universe.db")
    r = resolve_instrument(db, symbol, isin=isin, canonical_instrument_id=cid)
    return db, r["instrument_uid"], r["listing_uid"]


def _inject(db, *, candidate_id, source, iuid, luid, key, status="ACTIVE", ttl=5,
            effective_from="2026-06-10", generation_trading_date=None):
    """Raw-inject a candidate row (bypassing the write API) to exercise the defensive
    read-path validation against rows the supported APIs would never produce."""
    now = "2026-06-10T00:00:00+00:00"
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO candidates (candidate_id, submission_key, source, source_candidate_key, "
        "instrument_uid, listing_uid, submitted_trading_date, effective_from_trading_date, "
        "status, ttl_sessions_remaining, generation_trading_date, source_payload_hash, "
        "resolver_version, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (candidate_id, candidate_id, source, key, iuid, luid, "2026-06-10", effective_from,
         status, ttl, generation_trading_date, "h", VER, now, now))
    con.commit()
    con.close()


def _submit_manual(db, iuid, luid, key="m", ttl=None, td="2026-06-10"):
    return CandidateStore(db).submit_candidate_atomic(
        source="MANUAL", source_candidate_key=key, instrument_uid=iuid, listing_uid=luid,
        trading_date=td, resolver_version=VER, source_payload_hash="h" + key,
        ttl_sessions=ttl)


def _submit_tti(db, iuid, luid, key="t", td="2026-06-10"):
    return CandidateStore(db).submit_candidate_atomic(
        source="TTI", source_candidate_key=key, instrument_uid=iuid, listing_uid=luid,
        trading_date=td, resolver_version=VER, source_payload_hash="h" + key)


def _audit_count(db, candidate_id, event_type):
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM candidate_audit WHERE candidate_id=? AND event_type=?",
                    (candidate_id, event_type)).fetchone()[0]
    con.close()
    return n


def _status(db, candidate_id):
    con = sqlite3.connect(db)
    r = con.execute("SELECT status, ttl_sessions_remaining, last_counted_trading_date "
                    "FROM candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
    con.close()
    return r


# ════════════════════════════════════════════════════════════════════════════════════
# R2B-P3-1 — candidate-store error observability
# ════════════════════════════════════════════════════════════════════════════════════
def test_missing_candidates_table_returns_store_unavailable(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    cs = CandidateStore(db)
    con = sqlite3.connect(db)
    con.execute("DROP TABLE candidates")
    con.commit()
    con.close()
    sel = cs.effective_candidates(TD)                 # must NOT raise
    assert sel.store_unavailable is True
    assert sel.effective == {} and sel.blocked == {} and sel.suppressed == []


def test_corrupt_db_read_returns_store_unavailable(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    cs = CandidateStore(db)
    # Overwrite the file with non-SQLite bytes → any read raises sqlite3.DatabaseError.
    with open(db, "wb") as fh:
        fh.write(b"not a sqlite database at all" * 64)
    sel = cs.effective_candidates(TD)                 # caught → fail closed, no crash
    assert sel.store_unavailable is True


class _UnavailableStore:
    """A candidate store whose read raises a DB error (locked/unavailable). Records whether the
    TTL tick or selection audit were (incorrectly) invoked after a read failure."""
    def __init__(self):
        self.ticked = False
        self.audited = False

    def effective_candidates(self, trading_date):
        raise sqlite3.OperationalError("database is locked")

    def tick_ttl_atomic(self, *a, **k):
        self.ticked = True
        return {"counted": 0, "expired": 0}

    def record_selection_audit(self, *a, **k):
        self.audited = True
        return {"selected": 0, "suppressed": 0}


def _seed_eligible(tmp_path, symbols=("AAPL",)):
    p1, p2 = write_configs(tmp_path, [inst(s) for s in symbols], [])
    db = str(tmp_path / "universe.db")
    seed_registry(db, p1, p2)
    return db


def _uptrend(sector="Tech"):
    return {"bars": make_bars(), "corp_action_status": "ok", "sector": sector, "spread": 0.01}


def test_evaluator_store_unavailable_blocks_all_new_entries_no_crash(tmp_path):
    db = _seed_eligible(tmp_path)
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    resolve_instrument(db, "AAPL", canonical_instrument_id=cid)
    store = _UnavailableStore()
    ev = ShadowEvaluator(reg, SpyProvider({cid: _uptrend()}), ON, equity=100_000,
                         position_provider=flat(), require_candidate_source=True,
                         candidate_store=store)
    ev.maybe_run("2026-06-10")
    r = ev.maybe_run("2026-06-11")                    # must not raise
    assert r["ran"] is True
    o = [o for o in r["outcomes"] if o["canonical_instrument_id"] == cid][0]
    assert o["new_state"] == State.ENTRY_ELIGIBLE.value and o["entry_signal"] is True
    # the entry-eligible instrument is BLOCKED with the stable fail-closed reason ...
    assert cid not in [s["canonical_instrument_id"] for s in r["selected"]]
    rej = [x for x in r["rejected"] if x["canonical_instrument_id"] == cid]
    assert rej and rej[0]["rejected_reason"] == Reason.CANDIDATE_STORE_UNAVAILABLE
    # ... and on a store-read failure NO TTL mutation and NO selection audit occur.
    assert store.ticked is False and store.audited is False


def test_open_position_still_managed_when_store_unavailable(tmp_path):
    db = _seed_eligible(tmp_path)
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    resolve_instrument(db, "AAPL", canonical_instrument_id=cid)
    pos = StubPositionProvider({cid: PositionStatus.POSITION_OPEN})
    ev = ShadowEvaluator(reg, SpyProvider({cid: _uptrend()}), ON, equity=100_000,
                         position_provider=pos, require_candidate_source=True,
                         candidate_store=_UnavailableStore())
    ev.maybe_run("2026-06-10")
    r = ev.maybe_run("2026-06-11")
    o = [o for o in r["outcomes"] if o["canonical_instrument_id"] == cid][0]
    # candidate-store failure gates NEW entries only — the open position is still managed.
    assert o["new_state"] == State.POSITION_OPEN.value


# ════════════════════════════════════════════════════════════════════════════════════
# R2B-P3-2 — unknown persisted source / status / malformed ACTIVE row
# ════════════════════════════════════════════════════════════════════════════════════
def test_unknown_active_source_is_malformed(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    _inject(db, candidate_id="c_weird", source="WEIRD", iuid=iuid, luid=luid, key="w")
    sel = CandidateStore(db).effective_candidates(TD)
    assert iuid not in sel.effective
    assert sel.blocked.get(iuid) == Reason.CANDIDATE_MALFORMED


def test_unknown_active_status_is_malformed(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    _inject(db, candidate_id="c_zombie", source="MANUAL", iuid=iuid, luid=luid, key="z",
            status="ZOMBIE")                          # not in the frozen status enum
    sel = CandidateStore(db).effective_candidates(TD)
    assert iuid not in sel.effective
    assert sel.blocked.get(iuid) == Reason.CANDIDATE_MALFORMED


def test_invalid_ttl_state_is_malformed(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    _inject(db, candidate_id="c_badttl", source="MANUAL", iuid=iuid, luid=luid, key="b",
            ttl="not-an-int")                         # raw TEXT in the INTEGER column
    sel = CandidateStore(db).effective_candidates(TD)
    assert iuid not in sel.effective
    assert sel.blocked.get(iuid) == Reason.CANDIDATE_MALFORMED


def test_invalid_effective_date_is_malformed(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    _inject(db, candidate_id="c_baddate", source="MANUAL", iuid=iuid, luid=luid, key="d",
            effective_from="not-a-date")
    sel = CandidateStore(db).effective_candidates(TD)
    assert iuid not in sel.effective
    assert sel.blocked.get(iuid) == Reason.CANDIDATE_MALFORMED


def test_higher_precedence_malformed_blocks_fallthrough(tmp_path):
    # A malformed HIGHER-precedence MANUAL must block the instrument — never fall through to a
    # valid lower-precedence AUTO (the conservative no-fall-through rule).
    db, iuid, luid = _setup(tmp_path)
    _inject(db, candidate_id="c_man_bad", source="MANUAL", iuid=iuid, luid=luid, key="m",
            ttl="garbage")                            # malformed top
    _inject(db, candidate_id="c_auto_ok", source="AUTO", iuid=iuid, luid=luid, key="a",
            ttl=1, generation_trading_date=TD)        # otherwise-effective AUTO
    sel = CandidateStore(db).effective_candidates(TD)
    assert iuid not in sel.effective                  # AUTO was NOT selected
    assert sel.blocked.get(iuid) == Reason.CANDIDATE_MALFORMED


def test_wellformed_candidate_still_selected(tmp_path):
    # Guard against over-rejection: a normally-submitted candidate is unaffected by validation.
    db, iuid, luid = _setup(tmp_path)
    _submit_manual(db, iuid, luid)
    sel = CandidateStore(db).effective_candidates(TD)
    assert iuid in sel.effective and sel.effective[iuid]["source"] == "MANUAL"


# ════════════════════════════════════════════════════════════════════════════════════
# R2B-P3-3 — selection audit completeness + idempotency
# ════════════════════════════════════════════════════════════════════════════════════
def test_selected_effective_persisted_once_per_date(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    res = _submit_manual(db, iuid, luid)
    cid = res["candidate_id"]
    cs = CandidateStore(db)
    sel = cs.effective_candidates(TD)
    cs.record_selection_audit(TD, sel)
    assert _audit_count(db, cid, EVENT_SELECTED_EFFECTIVE) == 1
    # duplicate same-date evaluation → no new audit row (idempotent)
    cs.record_selection_audit(TD, cs.effective_candidates(TD))
    assert _audit_count(db, cid, EVENT_SELECTED_EFFECTIVE) == 1
    # a DIFFERENT trading date is a distinct event and IS recorded.
    cs.record_selection_audit("2026-06-13", cs.effective_candidates("2026-06-13"))
    assert _audit_count(db, cid, EVENT_SELECTED_EFFECTIVE) == 2


def test_suppressed_persisted_once_per_date(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    _submit_manual(db, iuid, luid, key="m")
    tti = _submit_tti(db, iuid, luid, key="t")        # lower precedence → suppressed
    cs = CandidateStore(db)
    sel = cs.effective_candidates(TD)
    assert tti["candidate_id"] in sel.suppressed
    cs.record_selection_audit(TD, sel)
    cs.record_selection_audit(TD, cs.effective_candidates(TD))   # duplicate
    assert _audit_count(db, tti["candidate_id"], EVENT_SUPPRESSED) == 1
    con = sqlite3.connect(db)
    reason = con.execute(
        "SELECT reason_code FROM candidate_audit WHERE candidate_id=? AND event_type=?",
        (tti["candidate_id"], EVENT_SUPPRESSED)).fetchone()[0]
    con.close()
    assert reason == Reason.SUPPRESSED_BY_HIGHER_PRECEDENCE_SOURCE


def test_record_selection_audit_requires_non_null_date(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    _submit_manual(db, iuid, luid)
    cs = CandidateStore(db)
    with pytest.raises(ValueError):
        cs.record_selection_audit(None, cs.effective_candidates(TD))


def test_record_selection_audit_appends_only(tmp_path):
    # The selection-audit path must never UPDATE/DELETE existing audit rows (append-only).
    db, iuid, luid = _setup(tmp_path)
    res = _submit_manual(db, iuid, luid)
    cs = CandidateStore(db)
    before = _audit_count(db, res["candidate_id"], "SUBMITTED")
    cs.record_selection_audit(TD, cs.effective_candidates(TD))
    assert _audit_count(db, res["candidate_id"], "SUBMITTED") == before   # untouched


# ════════════════════════════════════════════════════════════════════════════════════
# R2B-P3-4 — transaction consistency + TTL fault coverage
# ════════════════════════════════════════════════════════════════════════════════════
def test_deactivate_fault_rolls_back_and_retry_succeeds(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    res = _submit_manual(db, iuid, luid)
    cid = res["candidate_id"]
    cs = CandidateStore(db)

    def boom(seam):
        if seam == "after_audit":
            raise RuntimeError("injected fault after audit insert")
    with pytest.raises(RuntimeError):
        cs.deactivate_candidate_atomic(cid, TD, _fault_hook=boom)
    # rolled back fully: still ACTIVE, no DEACTIVATED audit row.
    assert _status(db, cid)[0] == "ACTIVE"
    assert _audit_count(db, cid, "DEACTIVATED") == 0
    # connection discarded; a clean retry succeeds.
    cs.deactivate_candidate_atomic(cid, TD)
    assert _status(db, cid)[0] == "DEACTIVATED"
    assert _audit_count(db, cid, "DEACTIVATED") == 1


def test_ttl_update_fault_rolls_back(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    res = _submit_manual(db, iuid, luid, ttl=5)
    cid = res["candidate_id"]
    cs = CandidateStore(db)

    def boom(seam):
        if seam == "after_ttl_update":
            raise RuntimeError("injected fault after TTL decrement")
    with pytest.raises(RuntimeError):
        cs.tick_ttl_atomic(TD, _fault_hook=boom)
    st = _status(db, cid)
    assert st[0] == "ACTIVE" and st[1] == 5 and st[2] is None   # no decrement, no count date
    # retry (no fault) decrements cleanly exactly once.
    cs.tick_ttl_atomic(TD)
    assert _status(db, cid)[1] == 4


def test_ttl_expiry_audit_fault_rolls_back(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    res = _submit_manual(db, iuid, luid, ttl=1)        # next tick → 0 → EXPIRED + audit
    cid = res["candidate_id"]
    cs = CandidateStore(db)

    def boom(seam):
        if seam == "after_expiry_audit":
            raise RuntimeError("injected fault after EXPIRED audit insert")
    with pytest.raises(RuntimeError):
        cs.tick_ttl_atomic(TD, _fault_hook=boom)
    st = _status(db, cid)
    assert st[0] == "ACTIVE" and st[1] == 1            # expiry rolled back fully
    assert _audit_count(db, cid, "EXPIRED") == 0
    cs.tick_ttl_atomic(TD)                             # retry expires cleanly
    assert _status(db, cid)[0] == "EXPIRED"
    assert _audit_count(db, cid, "EXPIRED") == 1


def test_concurrent_writer_fails_cleanly_with_no_partial_data(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    res = _submit_manual(db, iuid, luid)
    cid = res["candidate_id"]
    cs = CandidateStore(db)
    blocker = sqlite3.connect(db, timeout=0.2)
    blocker.isolation_level = None
    blocker.execute("BEGIN IMMEDIATE")                 # hold the write lock
    blocker.execute("UPDATE candidates SET updated_at=updated_at")
    try:
        with pytest.raises(sqlite3.OperationalError):  # serialize-or-fail; here it fails cleanly
            cs.deactivate_candidate_atomic(cid, TD, timeout=0.2)
        assert _status(db, cid)[0] == "ACTIVE"         # no partial candidate/audit data
        assert _audit_count(db, cid, "DEACTIVATED") == 0
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    # lock released → retry succeeds (serializes safely).
    cs.deactivate_candidate_atomic(cid, TD)
    assert _status(db, cid)[0] == "DEACTIVATED"


def test_concurrent_ttl_tick_serializes_after_lock_release(tmp_path):
    db, iuid, luid = _setup(tmp_path)
    res = _submit_manual(db, iuid, luid, ttl=5)
    cid = res["candidate_id"]
    cs = CandidateStore(db)
    started = threading.Event()

    def hold_then_release():
        # The lock-holding connection is created AND used entirely within this thread.
        h = sqlite3.connect(db, timeout=0.5)
        h.isolation_level = None
        h.execute("BEGIN IMMEDIATE")
        h.execute("UPDATE candidates SET updated_at=updated_at")
        started.set()
        time.sleep(0.2)
        h.execute("ROLLBACK")
        h.close()

    t = threading.Thread(target=hold_then_release)
    t.start()
    assert started.wait(2.0)                           # lock is held before we attempt the tick
    cs.tick_ttl_atomic(TD, timeout=5.0)                # blocks then serializes after release
    t.join()
    assert _status(db, cid)[1] == 4                    # decremented exactly once, no corruption


# ════════════════════════════════════════════════════════════════════════════════════
# Feature-off isolation — zero candidate DB I/O
# ════════════════════════════════════════════════════════════════════════════════════
class _SpyStore:
    def __init__(self):
        self.reads = 0
        self.ticks = 0
        self.audits = 0

    def effective_candidates(self, trading_date):
        self.reads += 1
        from bot.universe.candidate_store import EffectiveSelection
        return EffectiveSelection()

    def tick_ttl_atomic(self, *a, **k):
        self.ticks += 1
        return {"counted": 0, "expired": 0}

    def record_selection_audit(self, *a, **k):
        self.audits += 1
        return {"selected": 0, "suppressed": 0}


def test_feature_off_does_zero_candidate_io(tmp_path):
    db = _seed_eligible(tmp_path)
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    resolve_instrument(db, "AAPL", canonical_instrument_id=cid)
    spy = _SpyStore()
    ev = ShadowEvaluator(reg, SpyProvider({cid: _uptrend()}), ON, equity=100_000,
                         position_provider=flat(), require_candidate_source=False,
                         candidate_store=spy)
    assert ev.require_candidate_source is False
    ev.maybe_run("2026-06-10")
    ev.maybe_run("2026-06-11")
    assert (spy.reads, spy.ticks, spy.audits) == (0, 0, 0)   # store never read/written


def test_candidate_store_residual_paths_import_no_broker():
    repo = pathlib.Path(__file__).resolve().parents[2]
    text = (repo / "bot" / "universe" / "candidate_store.py").read_text()
    for forbidden in ("bot.brokers", "ib_insync", "trading_ig", "bot.connection", "eodhd"):
        assert forbidden not in text
