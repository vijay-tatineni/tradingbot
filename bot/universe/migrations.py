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
]
