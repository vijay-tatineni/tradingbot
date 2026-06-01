"""Tests for the regime filter (Commit 1 of the experiment).

Covers:
- the new RegimeBlockedEntriesLog (table created on first use, log
  returns row id, attach_shadow_trade updates the row)
- the FeatureFlags entry for enable_regime_filter_live (default off,
  dependency on enable_classifier_shadow enforced)
- RegimeOrchestrator.apply_regime_filter behaviour:
    * flag off                       → True (no opinion)
    * flag on + TRENDING smoothed    → True, no log row
    * flag on + UNCLEAR smoothed     → False, log row written
    * flag on + no smoothed row      → False (warm-up case), log row
    * flag on + smoothing store raises → True (infra failure falls
      through to live path; documented choice — see docstring)
    * non-entry signal (signal=0)    → True (the filter only gates
      entries)
"""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from bot.regime.blocked_entries import RegimeBlockedEntriesLog
from bot.regime.flags import FeatureFlags, ConfigError
from bot.regime.models import SmoothedRegimeState
from bot.regime.orchestrator import RegimeOrchestrator


def _smoothed(symbol: str, regime: str) -> SmoothedRegimeState:
    return SmoothedRegimeState(
        instrument=symbol, effective_regime=regime,
        source_regime=regime, days_in_regime=3,
        last_changed_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        confidence=0.9, pending_regime=None, pending_days=0,
        regime_history=[],
    )


def _orch(flags_dict, **kw):
    flags = FeatureFlags(flags_dict)
    return RegimeOrchestrator(flags=flags, **kw)


def _inst(symbol="AAPL"):
    return {"symbol": symbol, "name": "Apple Inc"}


# ── Flag plumbing ─────────────────────────────────────────────

class TestFlagSurface:
    def test_default_off(self):
        flags = FeatureFlags({})
        assert flags.get("enable_regime_filter_live") is False

    def test_can_be_set_on(self):
        flags = FeatureFlags({
            "enable_classifier_shadow": True,
            "enable_regime_filter_live": True,
        })
        assert flags.get("enable_regime_filter_live") is True

    def test_depends_on_classifier_shadow(self):
        with pytest.raises(ConfigError):
            FeatureFlags({
                "enable_classifier_shadow": False,
                "enable_regime_filter_live": True,
            })


# ── Blocked-entries table ─────────────────────────────────────

class TestRegimeBlockedEntriesLog:
    def test_table_created_on_init(self, tmp_path):
        db = tmp_path / "regime.db"
        RegimeBlockedEntriesLog(str(db))
        with sqlite3.connect(str(db)) as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(regime_blocked_entries)"
            ).fetchall()]
        for col in ("ts", "instrument", "signal_type",
                    "signal_confidence", "smoothed_regime",
                    "classifier_rationale",
                    "would_have_entry_price", "bar_time",
                    "shadow_trade_id"):
            assert col in cols

    def test_log_returns_row_id(self, tmp_path):
        log = RegimeBlockedEntriesLog(str(tmp_path / "r.db"))
        row_id = log.log(
            instrument="AAPL", signal_type="BUY",
            signal_confidence="HIGH",
            smoothed_regime="UNCLEAR",
            classifier_rationale="vol falling, range flat",
            would_have_entry_price=271.50,
            bar_time="2026-06-01T16:00:00+00:00",
        )
        assert row_id >= 1

    def test_attach_shadow_trade(self, tmp_path):
        log = RegimeBlockedEntriesLog(str(tmp_path / "r.db"))
        row_id = log.log("AAPL", "BUY", "HIGH", "UNCLEAR",
                         None, 271.5, "t")
        log.attach_shadow_trade(row_id, "fake-trade-id")
        with sqlite3.connect(log._db_path) as conn:
            value = conn.execute(
                "SELECT shadow_trade_id FROM regime_blocked_entries "
                "WHERE id = ?", (row_id,)
            ).fetchone()[0]
        assert value == "fake-trade-id"


# ── apply_regime_filter behaviour ─────────────────────────────

class TestApplyRegimeFilter:
    def test_flag_off_always_allows(self, tmp_path):
        log = RegimeBlockedEntriesLog(str(tmp_path / "r.db"))
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "UNCLEAR")
        orch = _orch({}, smoothing_store=store, blocked_entries_log=log)
        assert orch.apply_regime_filter(
            _inst("AAPL"), 1, "HIGH", 100.0, "now") is True
        # No log row written when the flag is off.
        with sqlite3.connect(log._db_path) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM regime_blocked_entries"
            ).fetchone()[0]
        assert n == 0

    def test_trending_allows(self, tmp_path):
        log = RegimeBlockedEntriesLog(str(tmp_path / "r.db"))
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "TRENDING")
        orch = _orch({
            "enable_classifier_shadow": True,
            "enable_regime_filter_live": True,
        }, smoothing_store=store, blocked_entries_log=log)
        assert orch.apply_regime_filter(
            _inst("AAPL"), 1, "HIGH", 100.0, "now") is True
        with sqlite3.connect(log._db_path) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM regime_blocked_entries"
            ).fetchone()[0]
        assert n == 0

    def test_unclear_blocks_and_logs(self, tmp_path):
        log = RegimeBlockedEntriesLog(str(tmp_path / "r.db"))
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "UNCLEAR")
        orch = _orch({
            "enable_classifier_shadow": True,
            "enable_regime_filter_live": True,
        }, smoothing_store=store, blocked_entries_log=log)
        assert orch.apply_regime_filter(
            _inst("AAPL"), 1, "HIGH", 271.5, "2026-06-01T16:00Z") is False
        with sqlite3.connect(log._db_path) as conn:
            rows = conn.execute(
                "SELECT instrument, signal_type, smoothed_regime, "
                "would_have_entry_price, bar_time "
                "FROM regime_blocked_entries"
            ).fetchall()
        assert len(rows) == 1
        inst, sig, regime, price, bar_time = rows[0]
        assert inst == "AAPL"
        assert sig == "BUY"
        assert regime == "UNCLEAR"
        assert price == 271.5
        assert bar_time == "2026-06-01T16:00Z"
        # The orchestrator exposes the row id for Commit 2 wiring.
        assert orch.last_blocked_row_id("AAPL") == 1

    def test_ranging_blocks(self, tmp_path):
        log = RegimeBlockedEntriesLog(str(tmp_path / "r.db"))
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "RANGING")
        orch = _orch({
            "enable_classifier_shadow": True,
            "enable_regime_filter_live": True,
        }, smoothing_store=store, blocked_entries_log=log)
        assert orch.apply_regime_filter(
            _inst("AAPL"), 1, "HIGH", 100.0, "now") is False

    def test_no_smoothed_row_blocks_warm_up_case(self, tmp_path):
        """When the smoothing store has no row yet, the filter blocks
        — conservative default during warm-up."""
        log = RegimeBlockedEntriesLog(str(tmp_path / "r.db"))
        store = MagicMock()
        store.get_latest.return_value = None
        orch = _orch({
            "enable_classifier_shadow": True,
            "enable_regime_filter_live": True,
        }, smoothing_store=store, blocked_entries_log=log)
        assert orch.apply_regime_filter(
            _inst("AAPL"), 1, "HIGH", 100.0, "now") is False
        with sqlite3.connect(log._db_path) as conn:
            row = conn.execute(
                "SELECT smoothed_regime FROM regime_blocked_entries"
            ).fetchone()
        # Logged as NULL (no regime known)
        assert row[0] is None

    def test_smoothing_store_exception_falls_through_to_allow(self,
                                                              tmp_path):
        """Infra failures (DB locked, store unavailable) should not halt
        live trading — fall through to allow."""
        log = RegimeBlockedEntriesLog(str(tmp_path / "r.db"))
        store = MagicMock()
        store.get_latest.side_effect = RuntimeError("DB locked")
        orch = _orch({
            "enable_classifier_shadow": True,
            "enable_regime_filter_live": True,
        }, smoothing_store=store, blocked_entries_log=log)
        assert orch.apply_regime_filter(
            _inst("AAPL"), 1, "HIGH", 100.0, "now") is True

    def test_non_entry_signal_always_allows(self, tmp_path):
        log = RegimeBlockedEntriesLog(str(tmp_path / "r.db"))
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "UNCLEAR")
        orch = _orch({
            "enable_classifier_shadow": True,
            "enable_regime_filter_live": True,
        }, smoothing_store=store, blocked_entries_log=log)
        # signal=0 (no action) — filter has no opinion
        assert orch.apply_regime_filter(
            _inst("AAPL"), 0, "NONE", 100.0, "now") is True

    def test_short_signal_logs_as_sell(self, tmp_path):
        log = RegimeBlockedEntriesLog(str(tmp_path / "r.db"))
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "UNCLEAR")
        orch = _orch({
            "enable_classifier_shadow": True,
            "enable_regime_filter_live": True,
        }, smoothing_store=store, blocked_entries_log=log)
        orch.apply_regime_filter(_inst("AAPL"), -1, "HIGH", 100.0, "t")
        with sqlite3.connect(log._db_path) as conn:
            sig = conn.execute(
                "SELECT signal_type FROM regime_blocked_entries"
            ).fetchone()[0]
        assert sig == "SELL"

    def test_rationale_pulled_from_regime_cache(self, tmp_path):
        log = RegimeBlockedEntriesLog(str(tmp_path / "r.db"))
        # Stub regime_cache with the underlying _db_path the orchestrator
        # reads from.
        cache_db = tmp_path / "regime.db"
        import json as _j
        with sqlite3.connect(str(cache_db)) as conn:
            conn.execute("""CREATE TABLE regime_classification_cache(
                instrument TEXT, trading_date TEXT,
                input_hash TEXT, classification_json TEXT, created_at TEXT,
                PRIMARY KEY (instrument, trading_date, input_hash))""")
            conn.execute(
                "INSERT INTO regime_classification_cache "
                "VALUES (?,?,?,?,?)",
                ("AAPL", "2026-05-29", "h",
                 _j.dumps({"rationale": "ADX 42, slope up"}), "t"),
            )
            conn.commit()
        cache_stub = MagicMock()
        cache_stub._db_path = str(cache_db)
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "UNCLEAR")
        orch = _orch({
            "enable_classifier_shadow": True,
            "enable_regime_filter_live": True,
        }, smoothing_store=store, blocked_entries_log=log,
            regime_cache=cache_stub)
        orch.apply_regime_filter(_inst("AAPL"), 1, "HIGH", 100.0, "t")
        with sqlite3.connect(log._db_path) as conn:
            rationale = conn.execute(
                "SELECT classifier_rationale "
                "FROM regime_blocked_entries"
            ).fetchone()[0]
        assert rationale == "ADX 42, slope up"

    def test_log_failure_does_not_propagate(self, tmp_path):
        """A blocked-entries log write failure must not break the filter
        decision path — log + swallow."""
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "UNCLEAR")
        bad_log = MagicMock()
        bad_log.log.side_effect = RuntimeError("disk full")
        orch = _orch({
            "enable_classifier_shadow": True,
            "enable_regime_filter_live": True,
        }, smoothing_store=store, blocked_entries_log=bad_log)
        # Must not raise. Filter still blocks.
        assert orch.apply_regime_filter(
            _inst("AAPL"), 1, "HIGH", 100.0, "now") is False


# ── BasePlugin default ────────────────────────────────────────

class TestBasePluginDefault:
    def test_base_plugin_apply_regime_filter_allows(self):
        from bot.plugins.base_plugin import BasePlugin
        p = BasePlugin()
        assert p.apply_regime_filter({"symbol": "X"}, 1, "HIGH", 1.0, "t") is True
