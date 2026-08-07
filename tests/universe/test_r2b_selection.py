"""R2B selection integration + isolation (P3-4 §10/§11/§13).

NEW-entry selection consumes the persisted effective-candidate store ONLY; raw entry signals
cannot bypass it; candidate presence does not bypass identity/eligibility gates; candidate
expiry affects NEW entries only (open positions keep being managed). Default-off and flag-off
are pure no-ops over the candidate path.
"""
import pathlib
import sqlite3

from bot.universe.candidate_store import CandidateStore
from bot.universe.evaluator import ShadowEvaluator
from bot.universe.models import PositionStatus, Reason, State
from bot.universe.registry import Registry
from bot.universe.seed import canonical_id, seed_registry
from tests.universe._fixtures import (
    OFF, ON, SpyProvider, StubPositionProvider, flat, inst, make_bars, resolve_instrument,
    write_configs,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
VER = "r2b_resolver_v1"


def _seed(tmp_path, symbols=("AAPL",)):
    p1, p2 = write_configs(tmp_path, [inst(s) for s in symbols], [])
    db = str(tmp_path / "universe.db")
    seed_registry(db, p1, p2)
    return db


def _uptrend(sector="Tech"):
    return {"bars": make_bars(), "corp_action_status": "ok", "sector": sector, "spread": 0.01}


def _flat_bars():
    return {"bars": make_bars(slope=0.0), "corp_action_status": "ok", "sector": "Tech",
            "spread": 0.01}


def _resolve_and_link(db, cid="US_AAPL", symbol="AAPL"):
    r = resolve_instrument(db, symbol, canonical_instrument_id=cid)
    return r["instrument_uid"], r["listing_uid"]


def _submit_manual(db, iuid, luid, td="2026-06-10"):
    CandidateStore(db).submit_candidate_atomic(
        source="MANUAL", source_candidate_key="m", instrument_uid=iuid, listing_uid=luid,
        trading_date=td, resolver_version=VER, source_payload_hash="h")


def _run_two_days(reg, db, prov, pos, require, cstore=None):
    ev = ShadowEvaluator(reg, prov, ON, equity=100_000, position_provider=pos,
                         require_candidate_source=require, candidate_store=cstore)
    ev.maybe_run("2026-06-10")
    return ev.maybe_run("2026-06-11")


# ── selection consumes the effective-candidate store only ────────────────────────
def test_entry_signal_without_candidate_not_selected(tmp_path):
    db = _seed(tmp_path)
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    _resolve_and_link(db, cid)                          # identity exists, but NO candidate
    r = _run_two_days(reg, db, SpyProvider({cid: _uptrend()}), flat(), require=True)
    # the instrument IS entry-eligible with a signal ...
    o = [o for o in r["outcomes"] if o["canonical_instrument_id"] == cid][0]
    assert o["new_state"] == State.ENTRY_ELIGIBLE.value and o["entry_signal"] is True
    # ... but it is NOT selected (no effective candidate) — raw signal cannot bypass the store
    assert cid not in [s["canonical_instrument_id"] for s in r["selected"]]
    rej = [x for x in r["rejected"] if x["canonical_instrument_id"] == cid]
    assert rej and rej[0]["rejected_reason"] == Reason.CANDIDATE_INACTIVE


def test_entry_signal_with_effective_candidate_selected(tmp_path):
    db = _seed(tmp_path)
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    iuid, luid = _resolve_and_link(db, cid)
    _submit_manual(db, iuid, luid)
    r = _run_two_days(reg, db, SpyProvider({cid: _uptrend()}), flat(), require=True)
    assert cid in [s["canonical_instrument_id"] for s in r["selected"]]


def test_candidate_presence_does_not_bypass_eligibility(tmp_path):
    # A candidate is present, but the instrument has NO entry signal (flat bars) → not selected.
    db = _seed(tmp_path)
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    iuid, luid = _resolve_and_link(db, cid)
    _submit_manual(db, iuid, luid)
    r = _run_two_days(reg, db, SpyProvider({cid: _flat_bars()}), flat(), require=True)
    assert cid not in [s["canonical_instrument_id"] for s in r["selected"]]


def test_open_position_continues_without_candidate(tmp_path):
    # Candidate expiry/absence affects NEW entries only: an open position is still managed.
    db = _seed(tmp_path)
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    _resolve_and_link(db, cid)                          # no candidate at all
    pos = StubPositionProvider({cid: PositionStatus.POSITION_OPEN})
    r = _run_two_days(reg, db, SpyProvider({cid: _uptrend()}), pos, require=True)
    o = [o for o in r["outcomes"] if o["canonical_instrument_id"] == cid][0]
    assert o["new_state"] == State.POSITION_OPEN.value   # managed, not blocked by candidate gate


# ── default-off / flag-off isolation ─────────────────────────────────────────────
class _ExplodingCandidateStore:
    def effective_candidates(self, *a, **k):
        raise AssertionError("candidate store consulted while require_candidate_source=False")

    def tick_ttl_atomic(self, *a, **k):
        raise AssertionError("candidate TTL ticked while require_candidate_source=False")

    def record_selection_audit(self, *a, **k):
        raise AssertionError("selection audit written while require_candidate_source=False")


def test_default_off_never_consults_candidate_store(tmp_path):
    db = _seed(tmp_path)
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    ev = ShadowEvaluator(reg, SpyProvider({cid: _uptrend()}), ON, equity=100_000,
                         position_provider=flat(), candidate_store=_ExplodingCandidateStore())
    assert ev.require_candidate_source is False
    assert ev.maybe_run("2026-06-10")["ran"] is True     # must not raise


def test_flag_off_writes_no_candidate_rows(tmp_path):
    db = _seed(tmp_path)
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    ev = ShadowEvaluator(reg, SpyProvider({cid: _uptrend()}), OFF, equity=100_000,
                         position_provider=flat(), require_candidate_source=True)
    assert ev.maybe_run("2026-06-10") == {"ran": False, "reason": "flag_off", "evaluated": 0}
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
    con.close()


def test_candidate_store_imports_no_broker():
    forbidden = ("bot.brokers", "ib_insync", "trading_ig", "bot.connection", "eodhd")
    text = (REPO / "bot" / "universe" / "candidate_store.py").read_text()
    for f in forbidden:
        assert f not in text, f"candidate_store imports broker/data module {f}"


def test_no_live_module_imports_candidate_store():
    live = [REPO / "main.py", REPO / "api_server.py"]
    live += list((REPO / "bot").glob("*.py"))
    live += list((REPO / "bot" / "plugins").glob("*.py"))
    live += list((REPO / "bot" / "regime").glob("*.py"))
    offenders = [str(p.relative_to(REPO)) for p in live if p.exists()
                 and "candidate_store" in p.read_text()]
    assert offenders == [], f"live modules import candidate_store: {offenders}"
