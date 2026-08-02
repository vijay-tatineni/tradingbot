"""Tests for shadow simulator — §11.3, §11.5."""
import asyncio
import pytest
from unittest.mock import MagicMock

from bot.shadow.counterfactual_logger import CounterfactualLogger
from bot.shadow.simulator import (
    ShadowSimulator, ShadowWorkItem,
    MAX_QUEUE_SIZE, STALE_BAR_ABANDON_THRESHOLD,
    DATA_QUALITY_ABANDON_THRESHOLD,
)


@pytest.fixture
def cf_logger(tmp_path):
    return CounterfactualLogger(str(tmp_path / "test.db"))


@pytest.fixture
def simulator(cf_logger):
    return ShadowSimulator(cf_logger)


def _make_work(instrument="AAPL", bar_time="2026-05-18T14:00:00",
               price=150.0, action=None, live_action=None,
               overlay_ctx=None):
    return ShadowWorkItem(
        instrument=instrument,
        bar_time=bar_time,
        price=price,
        indicators={},
        overlay_ctx=overlay_ctx or {},
        action=action,
        live_action=live_action,
    )


def test_submit_returns_true(simulator):
    work = _make_work()
    assert simulator.submit(work) is True


def test_shadow_lag_tracks_queue(simulator):
    assert simulator.shadow_lag == 0
    simulator.submit(_make_work())
    assert simulator.shadow_lag == 1


def test_queue_overflow_drops_oldest(simulator):
    for i in range(MAX_QUEUE_SIZE + 5):
        simulator.submit(_make_work(bar_time=f"2026-05-18T{i:02d}:00:00"))
    assert simulator.shadow_lag == MAX_QUEUE_SIZE
    assert simulator.dropped_count == 5


def test_queue_overflow_warn(cf_logger):
    sent = []
    sim = ShadowSimulator(cf_logger, send_fn=sent.append)
    for i in range(55):
        sim.submit(_make_work(bar_time=f"2026-05-18T{i:02d}:00:00"))
    assert any("Shadow lag" in m for m in sent)


def test_process_logs_decision(cf_logger):
    sim = ShadowSimulator(cf_logger)
    work = _make_work(action="BUY", live_action="HOLD")
    asyncio.new_event_loop().run_until_complete(sim._process(work))
    import sqlite3
    conn = sqlite3.connect(cf_logger._db_path)
    cursor = conn.execute("SELECT COUNT(*) FROM shadow_decisions")
    count = cursor.fetchone()[0]
    conn.close()
    assert count == 1


def test_process_detects_disagreement(cf_logger):
    sim = ShadowSimulator(cf_logger)
    work = _make_work(action="HOLD", live_action="BUY")
    asyncio.new_event_loop().run_until_complete(sim._process(work))
    import sqlite3
    conn = sqlite3.connect(cf_logger._db_path)
    cursor = conn.execute("SELECT disagreement_type FROM shadow_decisions")
    row = cursor.fetchone()
    conn.close()
    assert row[0] == "regime_would_block"


def test_process_opens_hypothetical_on_buy(cf_logger):
    sim = ShadowSimulator(cf_logger)
    work = _make_work(action="BUY", price=150.0)
    work.engine_selected = "TripleConfirmationEngine"
    work.regime = "TRENDING"
    asyncio.new_event_loop().run_until_complete(sim._process(work))
    trades = cf_logger.get_open_hypotheticals("AAPL")
    assert len(trades) == 1
    assert trades[0]["status"] == "OPEN"


def test_process_no_hypothetical_with_overlay_active(cf_logger):
    from datetime import datetime, timezone, timedelta
    sim = ShadowSimulator(cf_logger)
    now = datetime(2026, 5, 18, 14, 0, 0, tzinfo=timezone.utc)
    stale_time = now - timedelta(minutes=45)
    work = _make_work(
        action="BUY",
        overlay_ctx={"last_bar_time": stale_time},
    )
    asyncio.new_event_loop().run_until_complete(sim._process(work))
    trades = cf_logger.get_open_hypotheticals("AAPL")
    assert len(trades) == 0


def test_check_abandonment_delisted(simulator, cf_logger):
    cf_logger.open_hypothetical(
        trade_id="SH-001", instrument="AAPL",
        bar_time="2026-05-18T14:00:00",
        engine="TripleConfirmationEngine", regime="TRENDING",
        price=150.0, quantity=10.0,
    )
    simulator.check_abandonment("AAPL", is_delisted=True)
    trades = cf_logger.get_open_hypotheticals("AAPL")
    assert len(trades) == 0


def test_check_abandonment_stale_data(simulator, cf_logger):
    cf_logger.open_hypothetical(
        trade_id="SH-001", instrument="AAPL",
        bar_time="2026-05-18T14:00:00",
        engine="TripleConfirmationEngine", regime="TRENDING",
        price=150.0, quantity=10.0,
    )
    simulator.check_abandonment("AAPL", bars_since_data=6)
    trades = cf_logger.get_open_hypotheticals("AAPL")
    assert len(trades) == 0


def test_check_abandonment_not_stale_enough(simulator, cf_logger):
    cf_logger.open_hypothetical(
        trade_id="SH-001", instrument="AAPL",
        bar_time="2026-05-18T14:00:00",
        engine="TripleConfirmationEngine", regime="TRENDING",
        price=150.0, quantity=10.0,
    )
    simulator.check_abandonment("AAPL", bars_since_data=3)
    trades = cf_logger.get_open_hypotheticals("AAPL")
    assert len(trades) == 1


def test_stop_sets_running_false(simulator):
    simulator.stop()
    assert simulator._running is False
