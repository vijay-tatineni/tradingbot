"""R2C schema-v7 atomicity & isolation — risk_evaluation evidence store.

The risk_evaluation row + its append-only risk_evaluation_audit event COMMIT together or ROLL
BACK together (single BEGIN IMMEDIATE). A fault injected at any seam rolls BOTH writes back; the
audit table is physically append-only; replay is idempotent; money is stored as canonical Decimal
strings (no float). Temporary DBs only — the production universe.db path is never touched.
"""
import os
from datetime import date, datetime
from decimal import Decimal

import pytest

from bot.universe.db import connect, default_db_path
from bot.universe.risk_gate import evaluate_entry_risk
from bot.universe.risk_store import RiskEvaluationStore
from bot.universe.sizing import SizingInputs
from tests.universe._fixtures import fresh_snapshot, pos_risk, SpyPortfolioRiskProvider

ET = datetime(2026, 6, 11, 15, 0, 0)
TD = date(2026, 6, 11)
SNAP_HASH = "snap_hash_abc"
VER = "dyn_universe_shadow_v1"


def _decision():
    snap = fresh_snapshot(base_currency="USD", evaluation_time=ET, trading_date=TD,
                          positions=[pos_risk("1000")])
    return evaluate_entry_risk(
        SizingInputs(base_currency="USD", instrument_currency="USD", entry_price="100",
                     stop_distance="2", equity_base="1000000", instrument_uid="iuid-1",
                     canonical_instrument_id="US_AAPL", listing_uid="luid-1"),
        evaluation_time=ET, evaluation_date=TD, fx_provider=None,
        portfolio_provider=SpyPortfolioRiskProvider(snapshot=snap))


def _counts(db):
    with connect(db) as conn:
        e = conn.execute("SELECT COUNT(*) FROM risk_evaluation").fetchone()[0]
        a = conn.execute("SELECT COUNT(*) FROM risk_evaluation_audit").fetchone()[0]
    return e, a


def _store(tmp_path):
    return RiskEvaluationStore(str(tmp_path / "universe.db")), str(tmp_path / "universe.db")


def _record(store, decision, **kw):
    return store.record_evaluation_atomic(
        decision, trading_date=TD, evaluation_time=ET, evaluator_version=VER,
        portfolio_snapshot_hash=SNAP_HASH, **kw)


# ── happy path ──────────────────────────────────────────────────────────
def test_record_writes_both_rows(tmp_path):
    store, db = _store(tmp_path)
    d = _decision()
    assert d.ok                                            # the decision under audit is ALLOW
    res = _record(store, d)
    assert res["idempotent"] is False and res["decision"] == "ALLOW"
    assert _counts(db) == (1, 1)
    row = store.get_evaluation(res["risk_evaluation_id"])
    assert row["decision"] == "ALLOW" and row["base_currency"] == "USD"
    assert row["portfolio_snapshot_hash"] == SNAP_HASH and row["fx_rate_id"]
    assert len(store.audit_events(res["risk_evaluation_id"])) == 1


def test_money_stored_as_canonical_decimal_strings(tmp_path):
    store, db = _store(tmp_path)
    d = _decision()
    res = _record(store, d)
    row = store.get_evaluation(res["risk_evaluation_id"])
    # stored as TEXT; round-trips losslessly to the exact Decimal (no float drift).
    assert Decimal(row["proposed_risk_base"]) == d.proposed_risk_base
    assert Decimal(row["post_trade_heat_base"]) == d.post_trade_heat_base
    assert Decimal(row["limit_base"]) == d.limit_base


# ── fault injection: a failure at ANY seam rolls BOTH writes back ─────────
@pytest.mark.parametrize("seam", ["after_risk_evaluation_insert", "after_audit_insert",
                                  "before_commit"])
def test_fault_at_each_seam_rolls_back_both(tmp_path, seam):
    store, db = _store(tmp_path)
    d = _decision()

    def hook(s):
        if s == seam:
            raise RuntimeError(f"injected at {s}")

    with pytest.raises(RuntimeError):
        _record(store, d, _fault_hook=hook)
    assert _counts(db) == (0, 0)                           # neither table advanced
    # connection not wedged: a clean retry fully succeeds.
    res = _record(store, d)
    assert res["idempotent"] is False and _counts(db) == (1, 1)


def test_no_production_db_path_touched(tmp_path):
    # the store writes ONLY to the injected tmp path; the production universe.db is untouched.
    prod = default_db_path()
    existed = os.path.exists(prod)
    before = os.path.getmtime(prod) if existed else None
    store, db = _store(tmp_path)
    assert store.db_path != prod
    _record(store, _decision())
    assert os.path.exists(prod) == existed                 # not created by this test
    if existed:
        assert os.path.getmtime(prod) == before            # not modified


# ── append-only audit (defence in depth) ─────────────────────────────────
def test_audit_is_append_only(tmp_path):
    import sqlite3
    store, db = _store(tmp_path)
    _record(store, _decision())
    with connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE risk_evaluation_audit SET decision='HACKED'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM risk_evaluation_audit")


# ── idempotent replay (content-addressed id) ─────────────────────────────
def test_identical_replay_is_idempotent(tmp_path):
    store, db = _store(tmp_path)
    d = _decision()
    first = _record(store, d)
    second = _record(store, d)
    assert second["idempotent"] is True
    assert second["risk_evaluation_id"] == first["risk_evaluation_id"]
    assert _counts(db) == (1, 1)                           # no duplicate rows


def test_divergent_decision_gets_distinct_row(tmp_path):
    # a BLOCK decision for the same instrument is distinct evidence → a new content-addressed row.
    store, db = _store(tmp_path)
    allow = _decision()
    snap = fresh_snapshot(base_currency="USD", evaluation_time=ET, trading_date=TD,
                          positions=[pos_risk("26000")])
    block = evaluate_entry_risk(
        SizingInputs(base_currency="USD", instrument_currency="USD", entry_price="100",
                     stop_distance="2", equity_base="1000000", instrument_uid="iuid-1",
                     canonical_instrument_id="US_AAPL", listing_uid="luid-1"),
        evaluation_time=ET, evaluation_date=TD, fx_provider=None,
        portfolio_provider=SpyPortfolioRiskProvider(snapshot=snap))
    assert not block.ok
    _record(store, allow)
    _record(store, block)
    assert _counts(db) == (2, 2)
