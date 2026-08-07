"""Tests for counterfactual logger — §11.2."""
import pytest

from bot.shadow.counterfactual_logger import CounterfactualLogger


@pytest.fixture
def logger(tmp_path):
    return CounterfactualLogger(str(tmp_path / "test.db"))


def test_log_decision(logger):
    row_id = logger.log_decision(
        instrument="AAPL",
        bar_time="2026-05-18T14:00:00",
        live_engine="TripleConfirmationEngine",
        live_action="BUY",
        shadow_regime="TRENDING",
        shadow_confidence=0.92,
        shadow_engine="TripleConfirmationEngine",
        shadow_action="BUY",
    )
    assert row_id >= 1


def test_log_decision_with_disagreement(logger):
    row_id = logger.log_decision(
        instrument="AAPL",
        bar_time="2026-05-18T14:00:00",
        live_engine="TripleConfirmationEngine",
        live_action="BUY",
        shadow_engine="NoOpEngine",
        shadow_action="NO_ENTRY",
        disagreement_type="regime_would_block",
    )
    assert row_id >= 1


def test_log_decision_minimal(logger):
    row_id = logger.log_decision(
        instrument="AAPL",
        bar_time="2026-05-18T14:00:00",
    )
    assert row_id >= 1


def test_open_hypothetical(logger):
    logger.open_hypothetical(
        trade_id="SH-001",
        instrument="AAPL",
        bar_time="2026-05-18T14:00:00",
        engine="TripleConfirmationEngine",
        regime="TRENDING",
        price=150.0,
        quantity=10.0,
        stop=145.0,
    )
    trades = logger.get_open_hypotheticals("AAPL")
    assert len(trades) == 1
    assert trades[0]["id"] == "SH-001"
    assert trades[0]["status"] == "OPEN"


def test_close_hypothetical(logger):
    logger.open_hypothetical(
        trade_id="SH-001", instrument="AAPL",
        bar_time="2026-05-18T14:00:00",
        engine="TripleConfirmationEngine", regime="TRENDING",
        price=150.0, quantity=10.0,
    )
    logger.close_hypothetical(
        trade_id="SH-001",
        bar_time="2026-05-18T16:00:00",
        exit_price=155.0,
        exit_reason="stop_hit",
        pnl=50.0,
        pnl_pct=3.33,
    )
    trades = logger.get_open_hypotheticals("AAPL")
    assert len(trades) == 0


def test_abandon_hypothetical(logger):
    logger.open_hypothetical(
        trade_id="SH-002", instrument="MSFT",
        bar_time="2026-05-18T14:00:00",
        engine="MeanReversionEngine", regime="RANGING",
        price=400.0, quantity=5.0,
    )
    logger.abandon_hypothetical("SH-002", "data_stale_5_bars")
    trades = logger.get_open_hypotheticals("MSFT")
    assert len(trades) == 0


def test_get_open_hypotheticals_all(logger):
    logger.open_hypothetical(
        trade_id="SH-A", instrument="AAPL",
        bar_time="2026-05-18T14:00:00",
        engine="TripleConfirmationEngine", regime="TRENDING",
        price=150.0, quantity=10.0,
    )
    logger.open_hypothetical(
        trade_id="SH-B", instrument="MSFT",
        bar_time="2026-05-18T14:00:00",
        engine="MeanReversionEngine", regime="RANGING",
        price=400.0, quantity=5.0,
    )
    all_open = logger.get_open_hypotheticals()
    assert len(all_open) == 2


def test_table_names(logger):
    assert logger.table_names() == {"shadow_decisions", "shadow_hypothetical_trades"}


def test_overlays_active_serialization(logger):
    logger.log_decision(
        instrument="AAPL",
        bar_time="2026-05-18T14:00:00",
        shadow_overlays_active=["DATA_QUALITY", "MACRO_LOCKOUT"],
    )
    # Just verify no error on insert with list


def test_flag_snapshot_serialization(logger):
    logger.log_decision(
        instrument="AAPL",
        bar_time="2026-05-18T14:00:00",
        flag_snapshot={"enable_classifier_live": True, "enable_router_live": False},
    )
