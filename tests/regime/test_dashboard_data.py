"""Tests for §15.1 dashboard data queries."""
import json
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
    get_current_routing,
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


def _insert_regime(db_path, instrument, date, raw_regime, confidence=0.85):
    conn = sqlite3.connect(db_path)
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "instrument": instrument,
        "classified_at": now,
        "trading_date": date,
        "raw_regime": raw_regime,
        "confidence": confidence,
        "rationale": "test",
        "features": {},
        "model_version": "test-v1",
        "prompt_version": "test-v1",
        "input_hash": "hash1",
    }
    conn.execute(
        "INSERT INTO regime_classification_cache VALUES (?,?,?,?,?)",
        (instrument, date, "hash1", json.dumps(payload), now),
    )
    conn.commit()
    conn.close()


def _insert_shadow(db_path, instrument, disagreement=None, smoothed_regime=None,
                   days_in_regime=None, engine="TripleConfirmationEngine",
                   overlays_active=""):
    conn = sqlite3.connect(db_path)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO shadow_decisions (ts, instrument, bar_time, shadow_regime, "
        "shadow_smoothed_regime, shadow_smoothed_days_in_regime, "
        "shadow_overlays_active, shadow_engine_selected, disagreement_type) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (now, instrument, now, "TRENDING", smoothed_regime, days_in_regime,
         overlays_active, engine, disagreement),
    )
    conn.commit()
    conn.close()


class TestRegimeStates:
    def test_empty_db(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        assert get_regime_states(db) == []

    def test_with_data_parses_json(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        _insert_regime(db, "AAPL", "2026-05-18", "TRENDING", confidence=0.91)
        result = get_regime_states(db)
        assert len(result) == 1
        row = result[0]
        assert row["instrument"] == "AAPL"
        assert row["raw_regime"] == "TRENDING"
        assert row["confidence"] == 0.91
        assert row["smoothed_regime"] is None
        assert row["days_in_regime"] is None
        assert row["last_classified"] is not None
        assert "_raw_classification_json" in row

    def test_merges_smoothed_from_shadow(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        _insert_regime(db, "AAPL", "2026-05-18", "TRENDING")
        _insert_shadow(db, "AAPL", smoothed_regime="TRENDING", days_in_regime=4)
        result = get_regime_states(db)
        assert result[0]["smoothed_regime"] == "TRENDING"
        assert result[0]["days_in_regime"] == 4

    def test_smoothed_uses_latest_shadow_row(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        _insert_regime(db, "AAPL", "2026-05-18", "TRENDING")
        _insert_shadow(db, "AAPL", smoothed_regime="UNCLEAR", days_in_regime=1)
        _insert_shadow(db, "AAPL", smoothed_regime="TRENDING", days_in_regime=5)
        result = get_regime_states(db)
        assert result[0]["smoothed_regime"] == "TRENDING"
        assert result[0]["days_in_regime"] == 5

    def test_filter_by_instruments(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        _insert_regime(db, "AAPL", "2026-05-18", "TRENDING")
        _insert_regime(db, "MSFT", "2026-05-18", "RANGING")
        result = get_regime_states(db, instruments=["AAPL"])
        assert len(result) == 1
        assert result[0]["instrument"] == "AAPL"

    def test_corrupt_classification_json(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        conn = sqlite3.connect(db)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO regime_classification_cache VALUES (?,?,?,?,?)",
            ("BAD", "2026-05-18", "h1", "{not json", now),
        )
        conn.commit()
        conn.close()
        result = get_regime_states(db)
        assert len(result) == 1
        assert result[0]["raw_regime"] is None
        assert result[0]["confidence"] is None


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


class TestInstrumentPauses:
    def _insert_pause(self, db, instrument, cleared=False):
        conn = sqlite3.connect(db)
        cleared_at = "2026-05-18T15:00:00" if cleared else None
        conn.execute(
            "INSERT INTO instrument_entry_pauses (instrument, paused_at, "
            "paused_by_overlay, reason, cleared_at, cleared_by) "
            "VALUES (?,?,?,?,?,?)",
            (instrument, "2026-05-18T14:00:00", "DATA_QUALITY",
             "stale bars", cleared_at, "auto" if cleared else None),
        )
        conn.commit()
        conn.close()

    def test_default_returns_all(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        self._insert_pause(db, "AAPL", cleared=False)
        self._insert_pause(db, "MSFT", cleared=True)
        result = get_instrument_pauses(db)
        assert len(result) == 2

    def test_active_only_filters_cleared(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        self._insert_pause(db, "AAPL", cleared=False)
        self._insert_pause(db, "MSFT", cleared=True)
        result = get_instrument_pauses(db, active_only=True)
        assert len(result) == 1
        assert result[0]["instrument"] == "AAPL"

    def test_active_only_empty(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        self._insert_pause(db, "MSFT", cleared=True)
        assert get_instrument_pauses(db, active_only=True) == []


class TestCurrentRouting:
    def test_empty(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        assert get_current_routing(db) == []

    def test_latest_per_instrument(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        _insert_shadow(db, "AAPL", smoothed_regime="UNCLEAR",
                       engine="NoOpEngine")
        _insert_shadow(db, "AAPL", smoothed_regime="TRENDING",
                       engine="TripleConfirmationEngine")
        _insert_shadow(db, "MSFT", smoothed_regime="RANGING",
                       engine="MeanReversionEngine")
        result = get_current_routing(db)
        result_by_instr = {r["instrument"]: r for r in result}
        assert len(result) == 2
        assert result_by_instr["AAPL"]["smoothed_regime"] == "TRENDING"
        assert result_by_instr["AAPL"]["selected_engine"] == "TripleConfirmationEngine"
        assert result_by_instr["AAPL"]["allow_new_entries"] is True
        assert result_by_instr["AAPL"]["block_reason"] == ""

    def test_noop_engine_blocks_entries(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        _insert_shadow(db, "AAPL", smoothed_regime="UNCLEAR",
                       engine="NoOpEngine")
        result = get_current_routing(db)
        assert result[0]["allow_new_entries"] is False
        assert "NoOpEngine" in result[0]["block_reason"]

    def test_overlay_blocks_entries(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        _insert_shadow(db, "AAPL", smoothed_regime="TRENDING",
                       engine="TripleConfirmationEngine",
                       overlays_active="MACRO_LOCKOUT")
        result = get_current_routing(db)
        assert result[0]["allow_new_entries"] is False
        assert "MACRO_LOCKOUT" in result[0]["block_reason"]

    def test_empty_overlay_field_not_blocking(self, tmp_path):
        db = str(tmp_path / "test.db")
        _setup_db(db)
        _insert_shadow(db, "AAPL", smoothed_regime="TRENDING",
                       engine="TripleConfirmationEngine", overlays_active="[]")
        result = get_current_routing(db)
        assert result[0]["allow_new_entries"] is True


class TestMissingTables:
    def test_no_table_returns_empty(self, tmp_path):
        db = str(tmp_path / "empty.db")
        sqlite3.connect(db).close()
        assert get_regime_states(db) == []
        assert get_active_overlays(db) == []
        assert get_degradation_events(db) == []
        assert get_shadow_decisions(db) == []
        assert get_routing_history(db) == []
        assert get_current_routing(db) == []
        assert get_instrument_pauses(db, active_only=True) == []
