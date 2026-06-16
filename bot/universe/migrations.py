"""Additive migration definitions for universe.db.

Ordered, append-only migration list applied via PRAGMA user_version (see db.py).
Each migration is applied atomically inside one explicit BEGIN IMMEDIATE / COMMIT
transaction (db.migrate): on failure the whole migration rolls back and
user_version is left unchanged. Migrations must be additive only. This DB is a
RESEARCH/operational-shadow store; it is never regime.db or backtest.db, and
operational execution state is never merged in.

To add a schema change: append a new (version, [statements]) tuple — never edit a
released migration.
"""

# Each entry: (target_user_version, [DDL statements]).
MIGRATIONS = [
    (
        1,
        [
            # ── canonical_instruments: broker-neutral master identity ──────
            """
            CREATE TABLE IF NOT EXISTS canonical_instruments (
                canonical_instrument_id  TEXT PRIMARY KEY,
                display_symbol           TEXT NOT NULL,
                name                     TEXT,
                asset_class              TEXT,
                sector                   TEXT,
                industry                 TEXT,
                exchange                 TEXT,
                currency                 TEXT,
                timezone                 TEXT,
                research_symbol          TEXT,
                administratively_active  INTEGER NOT NULL DEFAULT 1,
                hard_disabled            INTEGER NOT NULL DEFAULT 0,
                disabled_reason          TEXT,
                primary_gateway          TEXT NOT NULL DEFAULT 'IBKR',
                created_at               TEXT NOT NULL,
                updated_at               TEXT NOT NULL
            )
            """,
            # ── gateway_map_ibkr ───────────────────────────────────────────
            """
            CREATE TABLE IF NOT EXISTS gateway_map_ibkr (
                canonical_instrument_id  TEXT PRIMARY KEY
                    REFERENCES canonical_instruments(canonical_instrument_id),
                conId                    INTEGER,
                symbol                   TEXT NOT NULL,
                secType                  TEXT,
                exchange                 TEXT,
                primaryExchange          TEXT,
                currency                 TEXT,
                tradingClass             TEXT,
                minTick                  REAL,
                lotSize                  REAL,
                verification_status      TEXT NOT NULL DEFAULT 'UNVERIFIED',
                verified_at              TEXT
            )
            """,
            # ── gateway_map_ig (always order-routing-blocked in v1) ────────
            """
            CREATE TABLE IF NOT EXISTS gateway_map_ig (
                canonical_instrument_id  TEXT PRIMARY KEY
                    REFERENCES canonical_instruments(canonical_instrument_id),
                epic                     TEXT,
                instrument_type          TEXT,
                currency                 TEXT,
                verification_status      TEXT NOT NULL DEFAULT 'UNVERIFIED',
                order_routing_blocked    INTEGER NOT NULL DEFAULT 1,
                verified_at              TEXT
            )
            """,
            # ── candidate_sources (AUTO / TTI / MANUAL) ────────────────────
            """
            CREATE TABLE IF NOT EXISTS candidate_sources (
                candidate_id              TEXT PRIMARY KEY,
                canonical_instrument_id   TEXT NOT NULL
                    REFERENCES canonical_instruments(canonical_instrument_id),
                source                    TEXT NOT NULL,
                source_reference          TEXT,
                added_at                  TEXT NOT NULL,
                effective_trading_date    TEXT,
                expires_after_trading_date TEXT,
                reason_codes              TEXT,
                operator_notes            TEXT,
                active                    INTEGER NOT NULL DEFAULT 1,
                created_by                TEXT,
                created_at                TEXT NOT NULL,
                updated_at                TEXT NOT NULL
            )
            """,
            # ── universe_state (current state per instrument) ──────────────
            """
            CREATE TABLE IF NOT EXISTS universe_state (
                canonical_instrument_id  TEXT PRIMARY KEY
                    REFERENCES canonical_instruments(canonical_instrument_id),
                current_state            TEXT NOT NULL,
                previous_state           TEXT,
                reason_codes             TEXT,
                consecutive_passes       INTEGER NOT NULL DEFAULT 0,
                consecutive_failures     INTEGER NOT NULL DEFAULT 0,
                eligible_since           TEXT,
                ineligible_since         TEXT,
                cooldown_until           TEXT,
                evaluated_trading_date   TEXT,
                evaluated_at             TEXT,
                feature_snapshot_hash    TEXT,
                evaluator_version        TEXT
            )
            """,
            # ── universe_state_history (append-only) ───────────────────────
            """
            CREATE TABLE IF NOT EXISTS universe_state_history (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_instrument_id  TEXT NOT NULL,
                trading_date             TEXT NOT NULL,
                prior_state              TEXT,
                new_state                TEXT NOT NULL,
                reason_codes             TEXT,
                feature_snapshot_json    TEXT,
                feature_snapshot_hash    TEXT,
                evaluator_version        TEXT NOT NULL,
                created_at               TEXT NOT NULL
            )
            """,
            # Idempotency: one history row per (instrument, trading_date, evaluator_version).
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_history_idem
            ON universe_state_history
                (canonical_instrument_id, trading_date, evaluator_version)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_candidate_active
            ON candidate_sources (active, canonical_instrument_id)
            """,
        ],
    ),
    (
        # ── v2: Pre-Enable R1 (P3-2 session-based cooldown, P3-9 durable exit markers,
        #        append-only history enforcement). Strictly ADDITIVE. The legacy
        #        universe_state.cooldown_until column is intentionally LEFT IN PLACE as
        #        deprecated compatibility metadata and is no longer read by runtime logic
        #        (P3-2). No column is back-filled: an ambiguous legacy cooldown is failed
        #        safe into a blocked/manual-review state by the evaluator, never inferred.
        2,
        [
            # ── P3-2: explicit session-based cooldown fields ───────────────
            # Misleading `cooldown_until` (which actually stored a session COUNT) is
            # superseded by these. `cooldown_release_estimate` is DISPLAY-ONLY and is
            # never treated as authoritative unless computed from an approved exchange
            # calendar (v1 has none) — it is left NULL here.
            "ALTER TABLE universe_state ADD COLUMN cooldown_started_trading_date TEXT",
            "ALTER TABLE universe_state ADD COLUMN cooldown_sessions_remaining INTEGER",
            "ALTER TABLE universe_state ADD COLUMN cooldown_last_counted_trading_date TEXT",
            "ALTER TABLE universe_state ADD COLUMN cooldown_release_estimate TEXT",
            # ── P3-9: durable exit-event markers (non-sensitive) for exactly-once
            #         open→flat cooldown start across restarts/reruns ─────────
            "ALTER TABLE universe_state ADD COLUMN last_observed_position_status TEXT",
            "ALTER TABLE universe_state ADD COLUMN last_observed_position_id_hash TEXT",
            "ALTER TABLE universe_state ADD COLUMN last_processed_position_event_id TEXT",
            "ALTER TABLE universe_state ADD COLUMN last_position_close_trading_date TEXT",
            # ── P3-3 (defence-in-depth): make append-only history physically
            #         non-mutable. The store only ever INSERTs; these triggers reject any
            #         UPDATE/DELETE so a history row can never be silently rewritten. ──
            """
            CREATE TRIGGER IF NOT EXISTS trg_universe_history_no_update
            BEFORE UPDATE ON universe_state_history
            BEGIN
                SELECT RAISE(ABORT, 'universe_state_history is append-only');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS trg_universe_history_no_delete
            BEFORE DELETE ON universe_state_history
            BEGIN
                SELECT RAISE(ABORT, 'universe_state_history is append-only');
            END
            """,
        ],
    ),
    (
        # ── v3: Pre-Enable R1.1 + R1.2 (authoritative position continuity). Strictly ADDITIVE.
        #        Separates the LATEST observed status (which UNKNOWN may overwrite) from the
        #        LAST AUTHORITATIVE position evidence (which a non-authoritative observation
        #        must NEVER erase) — the root cause of the R1 cooldown-bypass across an
        #        outage. Adds a durable position_reconciliation_required block.
        #        R1.2 (P2-B) corrects the back-fill IN PLACE (v3 is unreleased / never run in
        #        production / only on the in-review branch, so editing it — rather than adding
        #        a v4 to repair an unreleased migration — is the chosen policy) so an
        #        OPEN-at-boundary v2 row keeps its authoritative-open anchor instead of being
        #        silently downgraded to a no-anchor flat that bypasses cooldown.
        3,
        [
            # latest observation (may be overwritten by a non-authoritative UNKNOWN):
            "ALTER TABLE universe_state ADD COLUMN latest_observed_position_status TEXT",
            "ALTER TABLE universe_state ADD COLUMN latest_observed_at TEXT",
            # last AUTHORITATIVE evidence (survives provider outages; never erased by UNKNOWN):
            "ALTER TABLE universe_state ADD COLUMN last_authoritative_position_status TEXT",
            "ALTER TABLE universe_state ADD COLUMN last_authoritative_position_id_hash TEXT",
            "ALTER TABLE universe_state ADD COLUMN last_authoritative_observed_at TEXT",
            # durable blocked condition requiring authoritative reconciliation:
            "ALTER TABLE universe_state ADD COLUMN position_reconciliation_required INTEGER NOT NULL DEFAULT 0",

            # ── R1.3 (P2-D / Finding 2) complete immutable transition record ──
            # A canonical, deterministic serialization (+ its hash) of ALL material transition
            # outputs — INCLUDING the cooldown bookkeeping that the feature_snapshot_hash omits.
            # This makes the content-aware idempotency check able to detect a divergent replay
            # (e.g. different cooldown_sessions_remaining) even when the current-state row has
            # legitimately advanced past the replayed trading date. Added to the append-only
            # history table; ALTER ADD COLUMN is DDL (not a row UPDATE/DELETE) so the v2
            # append-only triggers do not fire. v3 is corrected IN PLACE (unreleased / never run
            # in production), so no schema v4 is introduced.
            "ALTER TABLE universe_state_history ADD COLUMN transition_snapshot_json TEXT",
            "ALTER TABLE universe_state_history ADD COLUMN transition_snapshot_hash TEXT",

            # ── R1.2 (P2-B) conservative authoritative-continuity back-fill ──
            # All statements run inside the single v3 BEGIN IMMEDIATE transaction (db.migrate),
            # so the back-fill is atomic with the schema change and never touches the
            # append-only history table. The new columns above are NULL/0 at this point.
            # Goal: a v2 row that was OPEN at the migration boundary must NOT silently lose its
            # authoritative-open anchor (which would let a later evidence-bearing close be
            # mistaken for an ordinary flat and bypass cooldown — the exact P2-B defect). We
            # derive the authoritative anchor from the v2 `last_observed_position_status`, using
            # the last evaluation's trading date (`evaluated_trading_date`) as documented
            # observation provenance. Uncertainty is NEVER inferred to flat.
            #
            # NOTE (conscious, documented default): if R1 ran WITHOUT a position provider, every
            # v2 row's last_observed_position_status is 'UNKNOWN', so this back-fill blocks the
            # WHOLE universe for reconciliation on upgrade. That is the intended fail-safe: each
            # instrument unblocks automatically on its next AUTHORITATIVE snapshot. There is no
            # production v2 DB and no production migration is run by this change.

            # (a) OPEN at the boundary → preserve the authoritative-open anchor + provenance.
            """
            UPDATE universe_state
               SET last_authoritative_position_status = 'POSITION_OPEN',
                   last_authoritative_position_id_hash = last_observed_position_id_hash,
                   last_authoritative_observed_at = evaluated_trading_date
             WHERE last_observed_position_status = 'POSITION_OPEN'
               AND last_authoritative_position_status IS NULL
            """,
            # (a2) OPEN but NO usable provenance date → cannot safely establish the anchor's
            #      observation time → block for reconciliation (entry stays blocked until a
            #      fresh authoritative snapshot arrives). The anchor status is still recorded.
            """
            UPDATE universe_state
               SET position_reconciliation_required = 1
             WHERE last_observed_position_status = 'POSITION_OPEN'
               AND last_authoritative_observed_at IS NULL
            """,
            # (b) authoritatively flat/closed (NO_POSITION or a durable exit status) → a clean
            #     flat anchor. A v2 row resting at NO_POSITION already represents a RESOLVED
            #     flat (R1 detected open→flat via the last_observed transition; a row left at
            #     NO_POSITION carries no dangling-open signal), so it may remain non-blocked.
            """
            UPDATE universe_state
               SET last_authoritative_position_status = 'NO_POSITION',
                   last_authoritative_observed_at = evaluated_trading_date
             WHERE last_observed_position_status IN
                   ('NO_POSITION', 'POSITION_EXITED', 'POSITION_EXITED_TODAY')
               AND last_authoritative_position_status IS NULL
            """,
            # (c) UNKNOWN, NULL/missing, or any unrecognised legacy status → authoritative
            #     state cannot be reconstructed → block for reconciliation. NEVER infer flat
            #     from uncertainty. (Rows handled by (a)/(b) already have a non-NULL anchor and
            #     are excluded.)
            """
            UPDATE universe_state
               SET position_reconciliation_required = 1
             WHERE last_authoritative_position_status IS NULL
               AND (last_observed_position_status IS NULL
                    OR last_observed_position_status NOT IN
                       ('POSITION_OPEN', 'NO_POSITION', 'POSITION_EXITED', 'POSITION_EXITED_TODAY'))
            """,
        ],
    ),
    (
        # ── v4: Pre-Enable R2A-0 / R2A-0.1 (P3-R1-A reused explicit close-event id). Strictly
        #        ADDITIVE. Persists the lifecycle-QUALIFIED close-event key alongside the raw
        #        last_processed_position_event_id. A provider close_event_id MUST be globally
        #        unique per close lifecycle; binding it to its full lifecycle (canonical id +
        #        hashed position id + opened & closed trading dates) lets the evaluator detect the
        #        SAME explicit id reused under a DIFFERENT lifecycle — a provider-contract
        #        violation routed to POSITION_RECONCILIATION (entry blocked, no cooldown
        #        manufactured), never a masked second close. Forward-only v4 — v1–v3 NOT edited.
        #
        #        R2A-0.1 FAIL-CLOSED BACK-FILL (independent-review Finding 3): a pre-v4 row may
        #        carry a previously-processed event but a NULL qualified key (the column did not
        #        exist before v4), so it cannot participate in the reuse-violation check. Rather
        #        than infer a key from incomplete legacy data, the migration blocks such rows for
        #        reconciliation (see the UPDATE below). This means v3→v4 fails closed for
        #        imported / rehearsal / restored / future pre-v4 databases — not merely a fresh DB.
        4,
        [
            "ALTER TABLE universe_state ADD COLUMN last_close_event_key TEXT",
            # Fail-closed: a row with a processed event but no qualified key cannot be checked for
            # explicit-id reuse → block it for authoritative reconciliation. We do NOT infer or
            # reconstruct a qualified key from incomplete legacy data. On the next evaluation the
            # row resolves to POSITION_RECONCILIATION (entry blocked); a fresh authoritative OPEN
            # or a valid lifecycle-qualified close reconciles it (a proper key is then written).
            # Atomic with the ADD COLUMN (single v4 BEGIN IMMEDIATE); idempotent (a rerun matches
            # nothing new); no-op on a fresh v1→v4 DB; v1–v3 DDL unchanged.
            """
            UPDATE universe_state
               SET position_reconciliation_required = 1
             WHERE last_processed_position_event_id IS NOT NULL
               AND last_close_event_key IS NULL
            """,
        ],
    ),
]
