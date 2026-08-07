"""Dynamic Universe R2C — atomic risk-evaluation evidence store (schema v7).

Persists the deterministic EVIDENCE of an FX-normalized sizing + open-book-heat NEW-entry
decision (a ``risk_evaluation`` row) together with an append-only ``risk_evaluation_audit``
trail, inside ONE explicit ``BEGIN IMMEDIATE`` transaction (both writes COMMIT together or
ROLL BACK together). Mirrors the candidate_store atomicity/fault-seam pattern.

Broker-free: never imports a broker adapter, calls IBKR/IG/EODHD, submits an order, or reads a
live broker session. Stores only NON-SENSITIVE evidence — a deterministic fx_rate_id /
portfolio_snapshot_hash and base-currency money values as canonical Decimal strings (no float
drift) — never credentials, account ids, tokens, or raw provider payloads.

DEFAULT-OFF: the evaluator's default contention path writes NOTHING here. This store is an
explicit, opt-in evidence trail (and the subject of the schema-v7 atomicity tests). No
production universe.db is created or migrated by importing or using it in tests.
"""
import hashlib
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from bot.universe.db import migrate
from bot.universe.risk_gate import RiskDecision


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(v) -> Optional[str]:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _dstr(v) -> Optional[str]:
    """Canonical string for a Decimal/None money value (never a float)."""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return format(v, "f")
    return str(v)


def fx_rate_id(decision: RiskDecision) -> Optional[str]:
    """Deterministic, non-sensitive id for the FX rate used (pair|rate|source). None when no
    cross-currency rate was applied at all is still hashed (same_currency → stable id)."""
    seed = f"{decision.fx_pair}|{_dstr(decision.fx_rate)}|{decision.fx_source}"
    return "fx_" + hashlib.sha256(seed.encode()).hexdigest()[:24]


def _risk_evaluation_id(decision: RiskDecision, trading_date, evaluation_time,
                        snapshot_hash) -> str:
    """Content-addressed id: identical evidence → identical id (idempotent replay); ANY change
    (decision/reason/money/fx/snapshot) → a distinct id, so replays never silently overwrite."""
    seed = "|".join(str(x) for x in (
        _iso(trading_date), _iso(evaluation_time), decision.canonical_instrument_id,
        decision.instrument_uid, decision.listing_uid, decision.base_currency,
        decision.instrument_currency, decision.fx_pair, _dstr(decision.fx_rate),
        _dstr(decision.proposed_risk_base), _dstr(decision.proposed_notional_base),
        _dstr(decision.existing_heat_base), _dstr(decision.post_trade_heat_base),
        _dstr(decision.limit_base), decision.decision, decision.reason, snapshot_hash))
    return "rv_" + hashlib.sha256(seed.encode()).hexdigest()[:32]


class RiskEvaluationStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        migrate(db_path)   # idempotent; brings the DB to schema v7

    def record_evaluation_atomic(
        self,
        decision: RiskDecision,
        *,
        trading_date,
        evaluation_time=None,
        evaluator_version: Optional[str] = None,
        portfolio_snapshot_hash: Optional[str] = None,
        _fault_hook=None,
    ) -> dict:
        """Atomically persist the risk_evaluation row + its append-only audit event. Idempotent
        on identical evidence (content-addressed id); a fault at any injected seam rolls BOTH
        writes back and leaves the DB at its prior state."""
        rid = _risk_evaluation_id(decision, trading_date, evaluation_time, portfolio_snapshot_hash)
        frid = fx_rate_id(decision)
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
                existing = conn.execute(
                    "SELECT decision FROM risk_evaluation WHERE risk_evaluation_id=?",
                    (rid,)).fetchone()
                if existing is not None:
                    conn.execute("ROLLBACK")
                    return {"risk_evaluation_id": rid, "idempotent": True,
                            "decision": existing[0]}

                conn.execute(
                    "INSERT INTO risk_evaluation (risk_evaluation_id, trading_date, "
                    "evaluation_time, canonical_instrument_id, instrument_uid, listing_uid, "
                    "base_currency, instrument_currency, fx_pair, fx_rate, fx_rate_id, "
                    "fx_rate_source, portfolio_snapshot_hash, proposed_qty, proposed_risk_base, "
                    "proposed_notional_base, existing_heat_base, post_trade_heat_base, "
                    "limit_base, decision, reason_code, evaluator_version, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, _iso(trading_date), _iso(evaluation_time),
                     decision.canonical_instrument_id, decision.instrument_uid,
                     decision.listing_uid, decision.base_currency, decision.instrument_currency,
                     decision.fx_pair, _dstr(decision.fx_rate), frid, decision.fx_source,
                     portfolio_snapshot_hash, decision.qty, _dstr(decision.proposed_risk_base),
                     _dstr(decision.proposed_notional_base), _dstr(decision.existing_heat_base),
                     _dstr(decision.post_trade_heat_base), _dstr(decision.limit_base),
                     decision.decision, decision.reason, evaluator_version, now))
                fault("after_risk_evaluation_insert")
                conn.execute(
                    "INSERT INTO risk_evaluation_audit (risk_evaluation_id, event_type, "
                    "trading_date, decision, reason_code, portfolio_snapshot_hash, fx_rate_id, "
                    "created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (rid, "EVALUATED", _iso(trading_date), decision.decision, decision.reason,
                     portfolio_snapshot_hash, frid, now))
                fault("after_audit_insert")
                fault("before_commit")
                conn.execute("COMMIT")
                return {"risk_evaluation_id": rid, "idempotent": False,
                        "decision": decision.decision, "reason": decision.reason}
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
        finally:
            conn.close()

    # ── reads ─────────────────────────────────────────────────────────
    def get_evaluation(self, risk_evaluation_id) -> Optional[dict]:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            cur = conn.execute("SELECT * FROM risk_evaluation WHERE risk_evaluation_id=?",
                               (risk_evaluation_id,))
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
            return dict(zip(cols, row)) if row else None
        finally:
            conn.close()

    def audit_events(self, risk_evaluation_id) -> list:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            cur = conn.execute(
                "SELECT * FROM risk_evaluation_audit WHERE risk_evaluation_id=? ORDER BY id",
                (risk_evaluation_id,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            conn.close()
