"""Tests for ShadowTradeSimulator (Commit 2 of the regime-filter
experiment).

Covers:
- DB schema extension is idempotent (peak_price + trail_stop columns
  added on first init; re-init doesn't fail)
- open() inserts a shadow_hypothetical_trades row and tracks state
- tick() exits via tier-1 (emergency stop) on every cycle
- tick() exits via tier-2 (take profit + trail stop) only on bar
  close, and the trail stop trails the peak
- close path writes pnl and pnl_pct
- Reload from DB: open positions survive a re-instantiation of the
  simulator (e.g. bot restart)
- Orchestrator wiring: apply_regime_filter opens a shadow trade and
  attaches its id to the regime_blocked_entries row
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
from bot.regime.flags import FeatureFlags
from bot.regime.models import SmoothedRegimeState
from bot.regime.orchestrator import RegimeOrchestrator
from bot.shadow.counterfactual_logger import CounterfactualLogger
from bot.shadow.trade_simulator import ShadowTradeSimulator


def _make_cf(tmp_path):
    return CounterfactualLogger(str(tmp_path / "regime.db"))


def _smoothed(symbol, regime):
    return SmoothedRegimeState(
        instrument=symbol, effective_regime=regime,
        source_regime=regime, days_in_regime=3,
        last_changed_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        confidence=0.9, pending_regime=None, pending_days=0,
        regime_history=[],
    )


def _row(db_path, trade_id):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute(
            "SELECT * FROM shadow_hypothetical_trades WHERE id = ?",
            (trade_id,),
        ).fetchone())


class TestSchemaExtension:
    def test_peak_and_trail_columns_added(self, tmp_path):
        cf = _make_cf(tmp_path)
        ShadowTradeSimulator(cf)
        with sqlite3.connect(cf._db_path) as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(shadow_hypothetical_trades)"
            ).fetchall()]
        assert "peak_price" in cols
        assert "trail_stop" in cols

    def test_idempotent_init(self, tmp_path):
        cf = _make_cf(tmp_path)
        ShadowTradeSimulator(cf)
        ShadowTradeSimulator(cf)  # must not raise on duplicate column


class TestOpen:
    def test_open_writes_row(self, tmp_path):
        cf = _make_cf(tmp_path)
        sim = ShadowTradeSimulator(cf)
        trade_id = sim.open(
            instrument="AAPL", side="LONG", price=100.0, qty=10.0,
            bar_time="2026-06-01T16:00Z", regime="UNCLEAR",
            trail_stop_pct=2.0,
        )
        assert trade_id is not None
        row = _row(cf._db_path, trade_id)
        assert row["instrument"] == "AAPL"
        assert row["entry_price"] == 100.0
        assert row["entry_quantity"] == 10.0
        assert row["status"] == "OPEN"
        # Initial trail stop is entry × (1 − pct/100)
        assert row["entry_stop"] == pytest.approx(98.0)
        assert row["peak_price"] == pytest.approx(100.0)
        assert row["trail_stop"] == pytest.approx(98.0)

    def test_open_short_negates_quantity(self, tmp_path):
        cf = _make_cf(tmp_path)
        sim = ShadowTradeSimulator(cf)
        trade_id = sim.open(
            instrument="X", side="SHORT", price=50.0, qty=4.0,
            bar_time="t", regime="RANGING", trail_stop_pct=2.0,
        )
        row = _row(cf._db_path, trade_id)
        assert row["entry_quantity"] == -4.0
        # SHORT initial trail is above entry
        assert row["entry_stop"] == pytest.approx(51.0)

    def test_open_dedupes_per_instrument(self, tmp_path):
        cf = _make_cf(tmp_path)
        sim = ShadowTradeSimulator(cf)
        first = sim.open("AAPL", "LONG", 100.0, 1.0, "t", None, 2.0)
        second = sim.open("AAPL", "LONG", 105.0, 1.0, "t", None, 2.0)
        assert first is not None
        assert second is None  # same instrument is already open


class TestTickEmergencyStop:
    def test_emergency_long_fires_every_cycle(self, tmp_path):
        cf = _make_cf(tmp_path)
        sim = ShadowTradeSimulator(cf)
        trade_id = sim.open("AAPL", "LONG", 100.0, 10.0, "t",
                            None, 2.0)
        # emergency stop at −4% from entry → 96.0; price 95.0 hits.
        exit_reason = sim.tick(
            "AAPL", price=95.0, bar_closed=False, bar_time="t",
            trail_stop_pct=2.0, take_profit_pct=8.0,
            emergency_stop_pct=4.0,
        )
        assert exit_reason is not None
        assert exit_reason.startswith("EMERGENCY_STOP")
        row = _row(cf._db_path, trade_id)
        assert row["status"] == "CLOSED"
        # PnL = (95 − 100) × 10 = −50
        assert row["pnl"] == pytest.approx(-50.0)
        assert row["exit_reason"].startswith("EMERGENCY_STOP")

    def test_emergency_short_fires_on_rip(self, tmp_path):
        cf = _make_cf(tmp_path)
        sim = ShadowTradeSimulator(cf)
        sim.open("X", "SHORT", 50.0, 4.0, "t", None, 2.0)
        exit_reason = sim.tick(
            "X", price=52.5, bar_closed=False, bar_time="t",
            trail_stop_pct=2.0, take_profit_pct=8.0,
            emergency_stop_pct=4.0,
        )
        assert exit_reason is not None
        assert exit_reason.startswith("EMERGENCY_STOP")


class TestTickTier2:
    def test_take_profit_only_at_bar_close(self, tmp_path):
        cf = _make_cf(tmp_path)
        sim = ShadowTradeSimulator(cf)
        trade_id = sim.open("AAPL", "LONG", 100.0, 10.0, "t",
                            None, 2.0)
        # Above take-profit target but bar not closed → no exit
        out = sim.tick("AAPL", price=110.0, bar_closed=False,
                       bar_time="t", trail_stop_pct=2.0,
                       take_profit_pct=8.0, emergency_stop_pct=20.0)
        assert out is None
        # Now bar closes → take-profit fires
        out = sim.tick("AAPL", price=110.0, bar_closed=True,
                       bar_time="t", trail_stop_pct=2.0,
                       take_profit_pct=8.0, emergency_stop_pct=20.0)
        assert out is not None
        assert out.startswith("TAKE_PROFIT")
        row = _row(cf._db_path, trade_id)
        assert row["pnl"] == pytest.approx(100.0)

    def test_trail_stop_trails_peak(self, tmp_path):
        cf = _make_cf(tmp_path)
        sim = ShadowTradeSimulator(cf)
        sim.open("AAPL", "LONG", 100.0, 10.0, "t", None, 2.0)
        # Walk price up to 110 on a bar close — peak advances, trail stop = 107.8
        sim.tick("AAPL", price=110.0, bar_closed=True, bar_time="t",
                 trail_stop_pct=2.0, take_profit_pct=50.0,
                 emergency_stop_pct=20.0)
        # Now back down to 108 (above trail stop) — still open
        out = sim.tick("AAPL", price=108.0, bar_closed=True, bar_time="t",
                       trail_stop_pct=2.0, take_profit_pct=50.0,
                       emergency_stop_pct=20.0)
        assert out is None
        # Drop to 107 (below 107.8 trail) — trail stop fires
        out = sim.tick("AAPL", price=107.0, bar_closed=True, bar_time="t",
                       trail_stop_pct=2.0, take_profit_pct=50.0,
                       emergency_stop_pct=20.0)
        assert out is not None
        assert out.startswith("TRAIL_STOP")

    def test_peak_persisted_so_restart_is_safe(self, tmp_path):
        cf = _make_cf(tmp_path)
        sim = ShadowTradeSimulator(cf)
        trade_id = sim.open("AAPL", "LONG", 100.0, 10.0, "t", None, 2.0)
        sim.tick("AAPL", price=110.0, bar_closed=True, bar_time="t",
                 trail_stop_pct=2.0, take_profit_pct=50.0,
                 emergency_stop_pct=20.0)
        row = _row(cf._db_path, trade_id)
        assert row["peak_price"] == pytest.approx(110.0)
        assert row["trail_stop"] == pytest.approx(107.8)

    def test_no_position_returns_none(self, tmp_path):
        cf = _make_cf(tmp_path)
        sim = ShadowTradeSimulator(cf)
        out = sim.tick("FOO", price=100.0, bar_closed=True, bar_time="t",
                       trail_stop_pct=2.0, take_profit_pct=8.0,
                       emergency_stop_pct=20.0)
        assert out is None


class TestReloadFromDb:
    def test_restart_recovers_open_positions(self, tmp_path):
        cf = _make_cf(tmp_path)
        sim = ShadowTradeSimulator(cf)
        trade_id = sim.open("AAPL", "LONG", 100.0, 10.0, "t", None, 2.0)
        sim.tick("AAPL", price=110.0, bar_closed=True, bar_time="t",
                 trail_stop_pct=2.0, take_profit_pct=50.0,
                 emergency_stop_pct=20.0)
        # New simulator instance pointed at same DB
        sim2 = ShadowTradeSimulator(cf)
        assert "AAPL" in sim2.open_positions()
        # Reloaded state has the peak and trail from disk
        reloaded = sim2.open_positions()["AAPL"]
        assert reloaded.trade_id == trade_id
        assert reloaded.peak_price == pytest.approx(110.0)
        assert reloaded.trail_stop == pytest.approx(107.8)

    def test_closed_positions_not_reloaded(self, tmp_path):
        cf = _make_cf(tmp_path)
        sim = ShadowTradeSimulator(cf)
        sim.open("AAPL", "LONG", 100.0, 10.0, "t", None, 2.0)
        sim.tick("AAPL", price=95.0, bar_closed=False, bar_time="t",
                 trail_stop_pct=2.0, take_profit_pct=8.0,
                 emergency_stop_pct=4.0)  # close via emergency
        sim2 = ShadowTradeSimulator(cf)
        assert sim2.open_positions() == {}


class TestOrchestratorWiring:
    def _orch(self, tmp_path):
        cf = _make_cf(tmp_path)
        sim = ShadowTradeSimulator(cf)
        log = RegimeBlockedEntriesLog(cf._db_path)
        store = MagicMock()
        store.get_latest.return_value = _smoothed("AAPL", "UNCLEAR")
        flags = FeatureFlags({
            "enable_classifier_shadow": True,
            "enable_regime_filter_live": True,
        })
        orch = RegimeOrchestrator(
            flags=flags, smoothing_store=store,
            blocked_entries_log=log,
            shadow_trade_simulator=sim,
        )
        return orch, sim, log, cf

    def test_filter_block_opens_shadow_and_attaches_trade_id(self,
                                                              tmp_path):
        orch, sim, log, cf = self._orch(tmp_path)
        inst = {"symbol": "AAPL", "qty": 10.0, "trail_stop_pct": 2.0}
        allowed = orch.apply_regime_filter(inst, 1, "HIGH", 100.0,
                                           "2026-06-01T16:00Z")
        assert allowed is False
        # Shadow position exists
        assert "AAPL" in sim.open_positions()
        # blocked_entries row has the shadow trade id attached
        with sqlite3.connect(log._db_path) as conn:
            row = conn.execute(
                "SELECT shadow_trade_id FROM regime_blocked_entries"
            ).fetchone()
        assert row[0] is not None
        # It matches the simulator's open state
        assert row[0] == sim.open_positions()["AAPL"].trade_id

    def test_tick_through_orchestrator_closes_shadow(self, tmp_path):
        orch, sim, log, cf = self._orch(tmp_path)
        inst = {"symbol": "AAPL", "qty": 10.0, "trail_stop_pct": 2.0}
        orch.apply_regime_filter(inst, 1, "HIGH", 100.0, "t")
        orch.on_instrument_tick(inst, price=95.0, bar_closed=False,
                                trail_stop_pct=2.0, take_profit_pct=8.0,
                                emergency_stop_pct=4.0)
        # Position closed via emergency stop
        assert "AAPL" not in sim.open_positions()
        with sqlite3.connect(cf._db_path) as conn:
            row = conn.execute(
                "SELECT status, exit_reason, pnl FROM "
                "shadow_hypothetical_trades"
            ).fetchone()
        assert row[0] == "CLOSED"
        assert row[1].startswith("EMERGENCY_STOP")
        assert row[2] == pytest.approx(-50.0)
