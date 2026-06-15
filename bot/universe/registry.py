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


class TransitionConflictError(Exception):
    """A persist was attempted for an existing idempotency key
    (canonical_instrument_id, trading_date, evaluator_version) whose stored content
    DIVERGES from the proposed transition (different new_state / feature hash / cooldown
    result / lifecycle event / authoritative markers). R1.1 fails closed and reports it
    rather than silently accepting a conflicting replay as an idempotent no-op."""


class StateHistoryConsistencyError(Exception):
    """A persist found the stored (history, current-state) pair inconsistent: history exists
    but the current-state row is missing, or the current-state row reflects an
    incompatible later/earlier transition than the history row for this key. R1.1 fails
    closed and reports it; it does NOT silently repair or overwrite."""


# ── universe_state column registry (single source of truth) ─────────────────────
# The migration DDL, persist_transition_atomic, and upsert_state all derive their column
# handling from THIS tuple, so a new column can never be added to the schema yet silently
# dropped from a write path (the exact failure mode that re-introduced the R1 bug).
_STATE_COLUMNS = (
    "canonical_instrument_id", "current_state", "previous_state", "reason_codes",
    "consecutive_passes", "consecutive_failures", "eligible_since", "ineligible_since",
    "cooldown_until", "evaluated_trading_date", "evaluated_at", "feature_snapshot_hash",
    "evaluator_version",
    # ── v2 (R1) ──
    "cooldown_started_trading_date", "cooldown_sessions_remaining",
    "cooldown_last_counted_trading_date", "cooldown_release_estimate",
    "last_observed_position_status", "last_observed_position_id_hash",
    "last_processed_position_event_id", "last_position_close_trading_date",
    # ── v3 (R1.1) authoritative-continuity columns ──
    "latest_observed_position_status", "latest_observed_at",
    "last_authoritative_position_status", "last_authoritative_position_id_hash",
    "last_authoritative_observed_at", "position_reconciliation_required",
)


def _state_value(col, rec):
    """Normalise one column's value from a state dict for persistence."""
    if col == "evaluated_at":
        return rec.get("evaluated_at", _utc_now_iso())
    if col == "reason_codes":
        return _dumps(rec.get("reason_codes"))
    if col in ("consecutive_passes", "consecutive_failures"):
        return int(rec.get(col, 0))
    if col == "cooldown_sessions_remaining":
        v = rec.get(col)
        return int(v) if v is not None else None
    if col == "position_reconciliation_required":
        return 1 if rec.get(col) else 0
    return rec.get(col)


def _state_params(rec):
    return tuple(_state_value(c, rec) for c in _STATE_COLUMNS)


def _state_insert_sql():
    cols = ", ".join(_STATE_COLUMNS)
    placeholders = ", ".join("?" for _ in _STATE_COLUMNS)
    updates = ",\n                        ".join(
        f"{c}=excluded.{c}" for c in _STATE_COLUMNS if c != "canonical_instrument_id")
    return (f"INSERT INTO universe_state ({cols}) VALUES ({placeholders})\n"
            f"                    ON CONFLICT(canonical_instrument_id) DO UPDATE SET\n"
            f"                        {updates}")


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
        Omitted columns are written NULL/default — a v1-style preset that sets only the
        legacy `cooldown_until` therefore yields an AMBIGUOUS row (cooldown_sessions_remaining
        NULL) by construction (P3-2). Column handling is shared with the atomic persist via
        the single ``_STATE_COLUMNS`` registry (lockstep — no column can be silently dropped)."""
        with connect(self.db_path) as conn:
            conn.execute(_state_insert_sql(), _state_params(rec))

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

    # ── P3-3 / R1.1: atomic, content-aware current-state + history persistence ──
    # History-comparison columns (for content-aware idempotency, R1.1 §6).
    _HISTORY_COMPARE = ("prior_state", "new_state", "reason_codes_json", "feature_snapshot_hash")
    # Current-state columns whose divergence (for the SAME key/date) is a real conflict.
    _STATE_COMPARE = (
        "current_state", "cooldown_sessions_remaining", "cooldown_started_trading_date",
        "cooldown_last_counted_trading_date", "last_processed_position_event_id",
        "last_position_close_trading_date", "last_authoritative_position_status",
        "last_authoritative_position_id_hash", "position_reconciliation_required",
    )

    def persist_transition_atomic(self, state: dict, history: dict,
                                  _fault_hook=None) -> bool:
        """Write the current-state UPSERT and the append-only history row for ONE
        transition inside a SINGLE explicit ``BEGIN IMMEDIATE`` transaction (P3-3) with
        CONTENT-AWARE idempotency reconciliation (R1.1 §6).

        Invariant: the two writes COMMIT together or ROLL BACK together — there is no
        observable state where ``universe_state`` advanced without its history row, or a
        history row exists without the matching state.

        On a duplicate idempotency key (canonical_instrument_id, trading_date,
        evaluator_version) the stored content is compared to the proposed transition
        INSIDE the transaction (so the read is consistent under the write lock). The
        append-only history row is the authoritative record of that key's transition; the
        decision is driven by it and by the ordering of the current-state row's evaluated
        date relative to this history date (R1.2 / P2-A):

          * current state's evaluated date EQUALS this history date (state still reflects
            this transition): full comparison of history fields AND current-state lifecycle/
            cooldown/authoritative markers → identical ⇒ idempotent no-op (False);
            divergent ⇒ ``TransitionConflictError``;
          * current state's evaluated date is LATER than this history date (the universe
            legitimately advanced on D+1, D+2, …): compare ONLY the immutable history
            content → identical ⇒ idempotent no-op (a valid historical replay is NOT
            rejected just because the current state moved on); divergent ⇒
            ``TransitionConflictError``. The current-state row is NOT required to still equal
            this history row's new_state;
          * current state's evaluated date is EARLIER than this history date, or the
            current-state row is missing, or (same date) the stored current_state disagrees
            with the stored history new_state → ``StateHistoryConsistencyError``.

        A conflict/inconsistency is NEVER silently accepted as a no-op and is NEVER
        silently repaired — it rolls back and fails closed. Bounded limitation: cooldown
        bookkeeping is not folded into the history feature-hash, so a cooldown-ONLY
        divergence on an ALREADY-ADVANCED replay (identical state label and hash) is not
        re-flagged in the advanced regime; the same-date regime compares it in full.

        Returns True if persisted (new), False if idempotent no-op. ``_fault_hook`` is a
        test-only seam (``"after_history"``, ``"after_state"``, ``"at_commit"``).
        Explicit transaction control mirrors db.migrate (``isolation_level = None``; no
        ``executescript()``). The current-state column handling is shared with upsert_state
        via the single ``_STATE_COLUMNS`` registry (lockstep).
        """
        def fault(seam):
            if _fault_hook is not None:
                _fault_hook(seam)

        cid = history["canonical_instrument_id"]
        td = history["trading_date"]
        ver = history["evaluator_version"]

        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.isolation_level = None      # WE own the transaction boundary
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                # 0) content-aware reconciliation if this key already exists.
                existing_h = conn.execute(
                    "SELECT prior_state, new_state, reason_codes, feature_snapshot_hash "
                    "FROM universe_state_history "
                    "WHERE canonical_instrument_id=? AND trading_date=? AND evaluator_version=?",
                    (cid, td, ver)).fetchone()
                if existing_h is not None:
                    verdict = self._reconcile_duplicate(conn, existing_h, state, history, td)
                    conn.execute("ROLLBACK")     # nothing to write on a duplicate key
                    if verdict == "idempotent":
                        return False
                    # _reconcile_duplicate raises on conflict/inconsistency; defensive:
                    raise TransitionConflictError(
                        f"unreconciled duplicate transition for {cid} {td}")

                # 1) history FIRST — append-only; UNIQUE index is the structural backstop.
                conn.execute(
                    """
                    INSERT INTO universe_state_history
                        (canonical_instrument_id, trading_date, prior_state, new_state,
                         reason_codes, feature_snapshot_json, feature_snapshot_hash,
                         evaluator_version, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (cid, td, history.get("prior_state"), history["new_state"],
                     _dumps(history.get("reason_codes")),
                     _dumps(history.get("feature_snapshot")),
                     history.get("feature_snapshot_hash"), ver, _utc_now_iso()),
                )
                fault("after_history")
                # 2) then the mutable current state + ALL lifecycle/authoritative markers
                #    (single statement → the authoritative-marker / close-event / cooldown
                #    updates are one atomic seam with the state upsert).
                conn.execute(_state_insert_sql(), _state_params(state))
                fault("after_state")
                fault("at_commit")
                conn.execute("COMMIT")
                return True
            except BaseException:
                # Any failure (conflict, inconsistency, injected fault, DB error): roll
                # BOTH writes back together and re-raise. State remains the prior state.
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
        finally:
            conn.close()

    def _reconcile_duplicate(self, conn, existing_h, state, history, trading_date) -> str:
        """Compare a stored transition (existing history row + current-state row) against a
        proposed one for the SAME idempotency key. Returns "idempotent" if identical; raises
        TransitionConflictError on divergent content or StateHistoryConsistencyError on a
        broken/impossible history/state pair. Read-only (the caller rolls back).

        The append-only history row is the authoritative record of this key's transition.
        Which comparison applies is decided by the ordering of the current-state row's
        evaluated date relative to this history date (R1.2 / P2-A): a current state that has
        legitimately ADVANCED past this date is NOT corruption and is NOT required to still
        equal this history row's new_state."""
        cid = history["canonical_instrument_id"]
        ex_prior, ex_new, ex_reasons, ex_hash = existing_h

        row = conn.execute(
            "SELECT current_state, evaluated_trading_date, cooldown_sessions_remaining, "
            "cooldown_started_trading_date, cooldown_last_counted_trading_date, "
            "last_processed_position_event_id, last_position_close_trading_date, "
            "last_authoritative_position_status, last_authoritative_position_id_hash, "
            "position_reconciliation_required "
            "FROM universe_state WHERE canonical_instrument_id=?", (cid,)).fetchone()
        if row is None:
            raise StateHistoryConsistencyError(
                f"history row exists for {cid} {trading_date} but the current-state row is missing")
        (s_state, s_eval_date, s_cd_rem, s_cd_start, s_cd_last, s_event, s_close,
         s_auth_status, s_auth_pid, s_recon) = row

        # The immutable history row is authoritative for this key. A divergent PROPOSED
        # history content is a conflict regardless of how far the current state has advanced.
        history_same = (
            ex_prior == history.get("prior_state")
            and ex_new == history["new_state"]
            and ex_reasons == _dumps(history.get("reason_codes"))
            and ex_hash == history.get("feature_snapshot_hash"))

        if s_eval_date is None:
            raise StateHistoryConsistencyError(
                f"history exists for {cid} {trading_date} but current state has no evaluated date")
        # ISO date strings compare lexicographically == chronologically.
        if str(s_eval_date) < str(trading_date):
            # Current state is BEHIND a history row it supposedly produced — impossible
            # ordering (history only exists for dates the state has reached). Corruption.
            raise StateHistoryConsistencyError(
                f"history exists for {cid} {trading_date} but current state is older "
                f"({s_eval_date}) — impossible ordering")

        if str(s_eval_date) > str(trading_date):
            # The universe legitimately ADVANCED past this date (later evaluations ran). This
            # is NOT corruption (P2-A). Decide purely on the immutable history content: an
            # exact historical replay is an idempotent no-op; a divergent one is a conflict.
            # The current-state row is NOT required to still equal this history's new_state.
            if history_same:
                return "idempotent"
            raise TransitionConflictError(
                f"duplicate idempotency key with DIVERGENT history content for {cid} "
                f"{trading_date} (current state has since advanced to {s_eval_date})")

        # s_eval_date == trading_date: the current state still reflects THIS transition. The
        # stored current_state must agree with the stored history new_state, else the two
        # stored rows are inconsistent (corruption).
        if s_state != ex_new:
            raise StateHistoryConsistencyError(
                f"current_state={s_state!r} disagrees with history.new_state={ex_new!r} "
                f"for {cid} {trading_date}")

        # Full same-date comparison: history fields AND current-state lifecycle / cooldown /
        # authoritative markers must all agree for an idempotent no-op.
        prop_cd_rem = state.get("cooldown_sessions_remaining")
        prop_cd_rem = int(prop_cd_rem) if prop_cd_rem is not None else None
        state_same = (
            s_state == state["current_state"]
            and s_cd_rem == prop_cd_rem
            and s_cd_start == state.get("cooldown_started_trading_date")
            and s_cd_last == state.get("cooldown_last_counted_trading_date")
            and s_event == state.get("last_processed_position_event_id")
            and s_close == state.get("last_position_close_trading_date")
            and s_auth_status == state.get("last_authoritative_position_status")
            and s_auth_pid == state.get("last_authoritative_position_id_hash")
            and int(s_recon or 0) == (1 if state.get("position_reconciliation_required") else 0))

        if history_same and state_same:
            return "idempotent"
        raise TransitionConflictError(
            f"duplicate idempotency key with DIVERGENT content for {cid} {trading_date}: "
            f"the same (instrument, date, evaluator_version) was already recorded with a "
            f"different transition/lifecycle result")
