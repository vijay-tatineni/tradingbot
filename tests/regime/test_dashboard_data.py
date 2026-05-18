"""Tests for §15.1 dashboard data queries."""
import sqlite3
from datetime import datetime, timezone

import pytest

from bot.regime.dashboard_data import (
    get_regime_states,
    get_active_overlays,
    get_degradation_events,
    get_instrument_pauses,
    get_shadow_decisions,
    get_shadow_vs_live_summary,
    get_routing_history,
)


def _setup_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS regime_classification_cache (
        instrument TEXT, trading_date TEXT, input_hash TEXT,
        classification_json TEXT, created_at TEXT,
        PRIMARY KEY (instrument, trading_date, input_hash))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS instrument_entry_pauses (
        instrument TEXT PRIMARY KEY, paused_at TEXT,
        paused_by_overlay TEXT, reason TEXT, cleared_at TEXT, cleared_by TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS degradation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, component TEXT,
        severity TEXT, trigger_reason TEXT, action_taken TEXT,
        flag_disabled TEXT, instruments_paused TEXT, recovery_instructions TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS shadow_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, instrument TEXT,
        bar_time TEXT, live_engine TEXT, live_signal_json TEXT,
        live_action_taken TEXT, live_trade_id TEXT, shadow_regime TEXT,
        shadow_confidence REAL, shadow_smoothed_regime TEXT,
        shadow_smoothed_days_in_regime INTEGER, shadow_overlays_active TEXT,
        shadow_engine_selected TEXT, shadow_signal_json TEXT,
        shadow_action_would_be TEXT, disagreement_type TEXT,
        hypothetical_trade_id TEXT, flag_snapshot_json TEXT)""")
    conn.commit()
    conn.close()


def _insert_regime(db_path, instrument, date, regime):
    conn = sqlite3.connect(db_path)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO regime_classification_cache VALUES (?,?,?,?,?)",
        (instrument, date, "hash1", f'{{"regime":"{regime}"}}', now),
    )
    conn.commit()
    conn.close()


def _insert_shadow(db_path, instrument, disagreement=None):
    conn = sqlite3.connect(db_path)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO shadow_decisions (ts, instrument, bar_time, shadow_regime, "
        "shadow_engine_selected, disagreement_type) VALUES (?,?,?,?,?,?)",
        (now, instrument, now, "TRENDING", "TripleConfirmationEngine", disagreement),
    )
    conn.commit()
    conn.close()


class TestRegimeStates:
    def test_empty_db(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        assert get_regime_states(db) == []

    def test_with_data(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        _insert_regime(db, "AAPL", "2026-05-18", "TRENDING")
        result = get_regime_states(db)
        assert len(result) == 1
        assert result[0]["instrument"] == "AAPL"

    def test_filter_by_instruments(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        _insert_regime(db, "AAPL", "2026-05-18", "TRENDING")
        _insert_regime(db, "MSFT", "2026-05-18", "RANGING")
        result = get_regime_states(db, instruments=["AAPL"])
        assert len(result) == 1


class TestShadowVsLive:
    def test_empty(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        result = get_shadow_vs_live_summary(db)
        assert result["total_decisions"] == 0
        assert result["agreement_pct"] == 0

    def test_with_data(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        _insert_shadow(db, "AAPL")
        _insert_shadow(db, "AAPL")
        _insert_shadow(db, "AAPL", disagreement="ENGINE_MISMATCH")
        result = get_shadow_vs_live_summary(db)
        assert result["total_decisions"] == 3
        assert result["agreements"] == 2
        assert result["disagreements"] == 1


class TestDegradationEvents:
    def test_empty(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        assert get_degradation_events(db) == []

    def test_with_event(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO degradation_events (ts, component, severity, "
            "trigger_reason, action_taken) VALUES (?,?,?,?,?)",
            ("2026-05-18T14:00:00", "classifier", "hard",
             "3 failures", "Disabled flag"),
        )
        conn.commit()
        conn.close()
        result = get_degradation_events(db)
        assert len(result) == 1
        assert result[0]["component"] == "classifier"


class TestMissingTables:
    def test_no_table_returns_empty(self, tmp_path):
        db = str(tmp_path / "empty.db")
        sqlite3.connect(db).close()
        assert get_regime_states(db) == []
        assert get_active_overlays(db) == []
        assert get_degradation_events(db) == []
        assert get_shadow_decisions(db) == []
        assert get_routing_history(db) == []
