"""Tests for §15.2 regime-specific Telegram alerts."""
from unittest.mock import MagicMock

from bot.regime.alerts import (
    send_hard_degradation_alert,
    send_db_logging_paused_alert,
    send_daily_regime_summary,
)


def test_hard_degradation_alert():
    telegram = MagicMock()
    telegram.send.return_value = True
    result = send_hard_degradation_alert(
        telegram, component="classifier",
        trigger_reason="3 consecutive failures",
        action_taken="Disabled enable_classifier_live",
    )
    assert result is True
    msg = telegram.send.call_args[0][0]
    assert "Hard Degradation" in msg
    assert "classifier" in msg


def test_hard_degradation_no_telegram():
    assert send_hard_degradation_alert(None, "x", "y", "z") is False


def test_db_logging_paused_alert():
    telegram = MagicMock()
    telegram.send.return_value = True
    result = send_db_logging_paused_alert(telegram, "Disk full")
    assert result is True
    assert "DB Logging Paused" in telegram.send.call_args[0][0]


def test_daily_regime_summary():
    telegram = MagicMock()
    telegram.send.return_value = True
    instruments = ["AAPL", "MSFT"]
    regime_states = {
        "AAPL": {"regime": "TRENDING", "days_in_regime": 5},
        "MSFT": {"regime": "RANGING", "days_in_regime": 2},
    }
    result = send_daily_regime_summary(
        telegram, instruments, regime_states,
        shadow_stats={"agreements": 45, "disagreements": 5},
    )
    assert result is True
    msg = telegram.send.call_args[0][0]
    assert "TRENDING" in msg
    assert "RANGING" in msg
    assert "90%" in msg


def test_daily_summary_no_shadow_stats():
    telegram = MagicMock()
    telegram.send.return_value = True
    send_daily_regime_summary(telegram, ["AAPL"], {"AAPL": {"regime": "UNCLEAR", "days_in_regime": 1}})
    msg = telegram.send.call_args[0][0]
    assert "UNCLEAR" in msg
    assert "Shadow" not in msg


def test_daily_summary_missing_instrument():
    telegram = MagicMock()
    telegram.send.return_value = True
    send_daily_regime_summary(telegram, ["AAPL"], {})
    msg = telegram.send.call_args[0][0]
    assert "no data" in msg
