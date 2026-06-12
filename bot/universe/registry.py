"""Registry store for universe.db — CRUD for canonical instruments, gateway maps,
candidate sources, and universe state (+ append-only history).

Mirrors the existing sqlite store idiom (bot/regime/*_store.py): a db_path string,
``CREATE`` handled by migrations, simple context-managed connections. No broker, no
data provider, no live-DB access.
"""
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from bot.universe.db import connect, migrate


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(value) -> Optional[str]:
    if value is None:
        return None
    # default=str is a safety net for non-native scalars (e.g. numpy types) so a
    # serialisation edge case can never silently drop a history/state write.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _loads(text):
    return json.loads(text) if text else None


class Registry:
    def __init__(self, db_path: str):
        self.db_path = db_path
        migrate(db_path)  # idempotent

    # ── canonical_instruments ────────────────────────────────────────
    def upsert_canonical(self, rec: dict) -> None:
        now = _utc_now_iso()
        with connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT created_at FROM canonical_instruments WHERE canonical_instrument_id=?",
                (rec["canonical_instrument_id"],),
            ).fetchone()
            created_at = existing[0] if existing else now
            conn.execute(
                """
                INSERT INTO canonical_instruments
                    (canonical_instrument_id, display_symbol, name, asset_class,
                     sector, industry, exchange, currency, timezone, research_symbol,
                     administratively_active, hard_disabled, disabled_reason,
                     primary_gateway, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(canonical_instrument_id) DO UPDATE SET
                    display_symbol=excluded.display_symbol,
                    name=excluded.name, asset_class=excluded.asset_class,
                    sector=excluded.sector, industry=excluded.industry,
                    exchange=excluded.exchange, currency=excluded.currency,
                    timezone=excluded.timezone, research_symbol=excluded.research_symbol,
                    administratively_active=excluded.administratively_active,
                    hard_disabled=excluded.hard_disabled,
                    disabled_reason=excluded.disabled_reason,
                    primary_gateway=excluded.primary_gateway,
                    updated_at=excluded.updated_at
                """,
                (
                    rec["canonical_instrument_id"], rec["display_symbol"],
                    rec.get("name"), rec.get("asset_class"), rec.get("sector"),
                    rec.get("industry"), rec.get("exchange"), rec.get("currency"),
                    rec.get("timezone"), rec.get("research_symbol"),
                    int(rec.get("administratively_active", 1)),
                    int(rec.get("hard_disabled", 0)), rec.get("disabled_reason"),
                    rec.get("primary_gateway", "IBKR"), created_at, now,
                ),
            )

    def get_canonical(self, cid: str) -> Optional[dict]:
        with connect(self.db_path) as conn:
            conn.row_factory = None
            cur = conn.execute(
                "SELECT * FROM canonical_instruments WHERE canonical_instrument_id=?",
                (cid,),
            )
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
            return dict(zip(cols, row)) if row else None

    def all_canonical(self) -> list:
        with connect(self.db_path) as conn:
            cur = conn.execute("SELECT * FROM canonical_instruments ORDER BY canonical_instrument_id")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    # ── gateway_map_ibkr ─────────────────────────────────────────────
    def upsert_gateway_ibkr(self, rec: dict) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO gateway_map_ibkr
                    (canonical_instrument_id, conId, symbol, secType, exchange,
                     primaryExchange, currency, tradingClass, minTick, lotSize,
                     verification_status, verified_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(canonical_instrument_id) DO UPDATE SET
                    conId=excluded.conId, symbol=excluded.symbol,
                    secType=excluded.secType, exchange=excluded.exchange,
                    primaryExchange=excluded.primaryExchange, currency=excluded.currency,
                    tradingClass=excluded.tradingClass, minTick=excluded.minTick,
                    lotSize=excluded.lotSize,
                    verification_status=excluded.verification_status,
                    verified_at=excluded.verified_at
                """,
                (
                    rec["canonical_instrument_id"], rec.get("conId"), rec["symbol"],
                    rec.get("secType"), rec.get("exchange"), rec.get("primaryExchange"),
                    rec.get("currency"), rec.get("tradingClass"), rec.get("minTick"),
                    rec.get("lotSize"), rec.get("verification_status", "UNVERIFIED"),
                    rec.get("verified_at"),
                ),
            )

    def get_gateway_ibkr(self, cid: str) -> Optional[dict]:
        with connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT * FROM gateway_map_ibkr WHERE canonical_instrument_id=?", (cid,))
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
            return dict(zip(cols, row)) if row else None

    # ── gateway_map_ig (ALWAYS order-routing-blocked in v1) ──────────
    def upsert_gateway_ig(self, rec: dict) -> None:
        # Safety invariant: IG mappings are never order-capable in this task.
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO gateway_map_ig
                    (canonical_instrument_id, epic, instrument_type, currency,
                     verification_status, order_routing_blocked, verified_at)
                VALUES (?,?,?,?,?,1,?)
                ON CONFLICT(canonical_instrument_id) DO UPDATE SET
                    epic=excluded.epic, instrument_type=excluded.instrument_type,
                    currency=excluded.currency,
                    verification_status=excluded.verification_status,
                    order_routing_blocked=1,
                    verified_at=excluded.verified_at
                """,
                (
                    rec["canonical_instrument_id"], rec.get("epic"),
                    rec.get("instrument_type"), rec.get("currency"),
                    rec.get("verification_status", "UNVERIFIED"), rec.get("verified_at"),
                ),
            )

    def get_gateway_ig(self, cid: str) -> Optional[dict]:
        with connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT * FROM gateway_map_ig WHERE canonical_instrument_id=?", (cid,))
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
            return dict(zip(cols, row)) if row else None

    # ── candidate_sources ────────────────────────────────────────────
    def add_candidate(self, rec: dict) -> None:
        now = _utc_now_iso()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO candidate_sources
                    (candidate_id, canonical_instrument_id, source, source_reference,
                     added_at, effective_trading_date, expires_after_trading_date,
                     reason_codes, operator_notes, active, created_by, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                    source=excluded.source, source_reference=excluded.source_reference,
                    effective_trading_date=excluded.effective_trading_date,
                    expires_after_trading_date=excluded.expires_after_trading_date,
                    reason_codes=excluded.reason_codes, operator_notes=excluded.operator_notes,
                    active=excluded.active, updated_at=excluded.updated_at
                """,
                (
                    rec["candidate_id"], rec["canonical_instrument_id"], rec["source"],
                    rec.get("source_reference"), rec.get("added_at", now),
                    rec.get("effective_trading_date"), rec.get("expires_after_trading_date"),
                    _dumps(rec.get("reason_codes")), rec.get("operator_notes"),
                    int(rec.get("active", 1)), rec.get("created_by", "seed"), now, now,
                ),
            )

    def active_candidates(self) -> list:
        with connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT * FROM candidate_sources WHERE active=1 ORDER BY candidate_id")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def expire_candidates(self, trading_date: str) -> int:
        """Deactivate TTI/MANUAL candidates whose expiry date is < trading_date.
        Returns the number expired. Idempotent. AUTO candidates are not TTL-bound here.
        """
        with connect(self.db_path) as conn:
            cur = conn.execute(
                """
                UPDATE candidate_sources
                   SET active=0, updated_at=?
                 WHERE active=1
                   AND source IN ('TTI','MANUAL')
                   AND expires_after_trading_date IS NOT NULL
                   AND expires_after_trading_date < ?
                """,
                (_utc_now_iso(), trading_date),
            )
            return cur.rowcount

    # ── universe_state + history ─────────────────────────────────────
    def get_state(self, cid: str) -> Optional[dict]:
        with connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT * FROM universe_state WHERE canonical_instrument_id=?", (cid,))
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
            return dict(zip(cols, row)) if row else None

    def upsert_state(self, rec: dict) -> None:
        """Direct current-state upsert. Retained for test setup and back-compat; the
        evaluator persists transitions via persist_transition_atomic (P3-3), not this.
        Omitted v2 columns are written NULL — a v1-style preset that sets only the legacy
        `cooldown_until` therefore yields an AMBIGUOUS row (cooldown_sessions_remaining
        NULL) by construction (P3-2)."""
        sr = rec.get("cooldown_sessions_remaining")
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO universe_state
                    (canonical_instrument_id, current_state, previous_state, reason_codes,
                     consecutive_passes, consecutive_failures, eligible_since,
                     ineligible_since, cooldown_until, evaluated_trading_date,
                     evaluated_at, feature_snapshot_hash, evaluator_version,
                     cooldown_started_trading_date, cooldown_sessions_remaining,
                     cooldown_last_counted_trading_date, cooldown_release_estimate,
                     last_observed_position_status, last_observed_position_id_hash,
                     last_processed_position_event_id, last_position_close_trading_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(canonical_instrument_id) DO UPDATE SET
                    current_state=excluded.current_state,
                    previous_state=excluded.previous_state,
                    reason_codes=excluded.reason_codes,
                    consecutive_passes=excluded.consecutive_passes,
                    consecutive_failures=excluded.consecutive_failures,
                    eligible_since=excluded.eligible_since,
                    ineligible_since=excluded.ineligible_since,
                    cooldown_until=excluded.cooldown_until,
                    evaluated_trading_date=excluded.evaluated_trading_date,
                    evaluated_at=excluded.evaluated_at,
                    feature_snapshot_hash=excluded.feature_snapshot_hash,
                    evaluator_version=excluded.evaluator_version,
                    cooldown_started_trading_date=excluded.cooldown_started_trading_date,
                    cooldown_sessions_remaining=excluded.cooldown_sessions_remaining,
                    cooldown_last_counted_trading_date=excluded.cooldown_last_counted_trading_date,
                    cooldown_release_estimate=excluded.cooldown_release_estimate,
                    last_observed_position_status=excluded.last_observed_position_status,
                    last_observed_position_id_hash=excluded.last_observed_position_id_hash,
                    last_processed_position_event_id=excluded.last_processed_position_event_id,
                    last_position_close_trading_date=excluded.last_position_close_trading_date
                """,
                (
                    rec["canonical_instrument_id"], rec["current_state"],
                    rec.get("previous_state"), _dumps(rec.get("reason_codes")),
                    int(rec.get("consecutive_passes", 0)),
                    int(rec.get("consecutive_failures", 0)),
                    rec.get("eligible_since"), rec.get("ineligible_since"),
                    rec.get("cooldown_until"), rec.get("evaluated_trading_date"),
                    rec.get("evaluated_at", _utc_now_iso()),
                    rec.get("feature_snapshot_hash"), rec.get("evaluator_version"),
                    rec.get("cooldown_started_trading_date"),
                    (int(sr) if sr is not None else None),
                    rec.get("cooldown_last_counted_trading_date"),
                    rec.get("cooldown_release_estimate"),
                    rec.get("last_observed_position_status"),
                    rec.get("last_observed_position_id_hash"),
                    rec.get("last_processed_position_event_id"),
                    rec.get("last_position_close_trading_date"),
                ),
            )

    def has_history(self, cid: str, trading_date: str, evaluator_version: str) -> bool:
        """Idempotency check: (canonical instrument + trading date + evaluator version)."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM universe_state_history
                 WHERE canonical_instrument_id=? AND trading_date=? AND evaluator_version=?
                 LIMIT 1
                """,
                (cid, trading_date, evaluator_version),
            ).fetchone()
            return row is not None

    def append_history(self, rec: dict) -> bool:
        """Append-only history write. Returns False if a row already exists for the
        idempotency key (same instrument/date/version) — never duplicates."""
        with connect(self.db_path) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO universe_state_history
                        (canonical_instrument_id, trading_date, prior_state, new_state,
                         reason_codes, feature_snapshot_json, feature_snapshot_hash,
                         evaluator_version, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        rec["canonical_instrument_id"], rec["trading_date"],
                        rec.get("prior_state"), rec["new_state"],
                        _dumps(rec.get("reason_codes")),
                        _dumps(rec.get("feature_snapshot")),
                        rec.get("feature_snapshot_hash"), rec["evaluator_version"],
                        _utc_now_iso(),
                    ),
                )
                return True
            except sqlite3.IntegrityError:
                # UNIQUE(idem) violation → already recorded this date/version.
                # (Only the idempotency conflict is swallowed; other errors propagate.)
                return False

    def history_count(self, cid: str) -> int:
        with connect(self.db_path) as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM universe_state_history WHERE canonical_instrument_id=?",
                (cid,)).fetchone()[0])

    # ── P3-3: atomic current-state + history persistence ─────────────
    _STATE_COLUMNS = (
        "canonical_instrument_id", "current_state", "previous_state", "reason_codes",
        "consecutive_passes", "consecutive_failures", "eligible_since",
        "ineligible_since", "cooldown_until", "evaluated_trading_date", "evaluated_at",
        "feature_snapshot_hash", "evaluator_version",
        # ── v2 (R1) additive columns ──
        "cooldown_started_trading_date", "cooldown_sessions_remaining",
        "cooldown_last_counted_trading_date", "cooldown_release_estimate",
        "last_observed_position_status", "last_observed_position_id_hash",
        "last_processed_position_event_id", "last_position_close_trading_date",
    )

    def persist_transition_atomic(self, state: dict, history: dict,
                                  _fault_hook=None) -> bool:
        """Write the current-state UPSERT and the append-only history row for ONE
        transition inside a SINGLE explicit ``BEGIN IMMEDIATE`` transaction (P3-3).

        Invariant: the two writes COMMIT together or ROLL BACK together — there is no
        observable state where ``universe_state`` advanced without its history row, or a
        history row exists without the matching state. The history row is written FIRST so
        its UNIQUE idempotency index (canonical_instrument_id, trading_date,
        evaluator_version) is the gate: a duplicate transition rolls the whole tx back and
        is a no-op (neither table changes), so a retried-after-success run never
        double-advances counters and never duplicates history.

        Returns True if persisted, False if it was an idempotent no-op (history row for
        this (instrument, date, version) already existed). Any non-idempotency error rolls
        back and re-raises.

        ``_fault_hook`` is a test-only seam: a callable invoked with a seam name
        ("after_history", "after_state", "at_commit") at which it may raise to prove the
        all-or-nothing rollback. Production callers never pass it.

        Explicit transaction control mirrors db.migrate: ``isolation_level = None`` (no
        implicit BEGIN/COMMIT) and individual ``conn.execute`` statements — NEVER
        ``executescript()`` (which would force an implicit COMMIT and defeat the boundary).
        """
        def fault(seam):
            if _fault_hook is not None:
                _fault_hook(seam)

        hist_params = (
            history["canonical_instrument_id"], history["trading_date"],
            history.get("prior_state"), history["new_state"],
            _dumps(history.get("reason_codes")),
            _dumps(history.get("feature_snapshot")),
            history.get("feature_snapshot_hash"), history["evaluator_version"],
            _utc_now_iso(),
        )
        sr = state.get("cooldown_sessions_remaining")
        state_params = (
            state["canonical_instrument_id"], state["current_state"],
            state.get("previous_state"), _dumps(state.get("reason_codes")),
            int(state.get("consecutive_passes", 0)),
            int(state.get("consecutive_failures", 0)),
            state.get("eligible_since"), state.get("ineligible_since"),
            state.get("cooldown_until"), state.get("evaluated_trading_date"),
            state.get("evaluated_at", _utc_now_iso()),
            state.get("feature_snapshot_hash"), state.get("evaluator_version"),
            state.get("cooldown_started_trading_date"),
            (int(sr) if sr is not None else None),
            state.get("cooldown_last_counted_trading_date"),
            state.get("cooldown_release_estimate"),
            state.get("last_observed_position_status"),
            state.get("last_observed_position_id_hash"),
            state.get("last_processed_position_event_id"),
            state.get("last_position_close_trading_date"),
        )

        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.isolation_level = None      # WE own the transaction boundary
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                # 1) history FIRST — UNIQUE idempotency index is the gate.
                try:
                    conn.execute(
                        """
                        INSERT INTO universe_state_history
                            (canonical_instrument_id, trading_date, prior_state, new_state,
                             reason_codes, feature_snapshot_json, feature_snapshot_hash,
                             evaluator_version, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        hist_params,
                    )
                except sqlite3.IntegrityError:
                    # duplicate (instrument, date, version) → idempotent no-op; roll the
                    # whole tx back so NEITHER table is touched.
                    conn.execute("ROLLBACK")
                    return False
                fault("after_history")
                # 2) then the mutable current state.
                conn.execute(
                    """
                    INSERT INTO universe_state
                        (canonical_instrument_id, current_state, previous_state, reason_codes,
                         consecutive_passes, consecutive_failures, eligible_since,
                         ineligible_since, cooldown_until, evaluated_trading_date,
                         evaluated_at, feature_snapshot_hash, evaluator_version,
                         cooldown_started_trading_date, cooldown_sessions_remaining,
                         cooldown_last_counted_trading_date, cooldown_release_estimate,
                         last_observed_position_status, last_observed_position_id_hash,
                         last_processed_position_event_id, last_position_close_trading_date)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(canonical_instrument_id) DO UPDATE SET
                        current_state=excluded.current_state,
                        previous_state=excluded.previous_state,
                        reason_codes=excluded.reason_codes,
                        consecutive_passes=excluded.consecutive_passes,
                        consecutive_failures=excluded.consecutive_failures,
                        eligible_since=excluded.eligible_since,
                        ineligible_since=excluded.ineligible_since,
                        cooldown_until=excluded.cooldown_until,
                        evaluated_trading_date=excluded.evaluated_trading_date,
                        evaluated_at=excluded.evaluated_at,
                        feature_snapshot_hash=excluded.feature_snapshot_hash,
                        evaluator_version=excluded.evaluator_version,
                        cooldown_started_trading_date=excluded.cooldown_started_trading_date,
                        cooldown_sessions_remaining=excluded.cooldown_sessions_remaining,
                        cooldown_last_counted_trading_date=excluded.cooldown_last_counted_trading_date,
                        cooldown_release_estimate=excluded.cooldown_release_estimate,
                        last_observed_position_status=excluded.last_observed_position_status,
                        last_observed_position_id_hash=excluded.last_observed_position_id_hash,
                        last_processed_position_event_id=excluded.last_processed_position_event_id,
                        last_position_close_trading_date=excluded.last_position_close_trading_date
                    """,
                    state_params,
                )
                fault("after_state")
                fault("at_commit")
                conn.execute("COMMIT")
                return True
            except BaseException:
                # Any non-idempotency failure (including an injected fault): roll BOTH
                # writes back together and re-raise. State remains the prior state.
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
        finally:
            conn.close()
