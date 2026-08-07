"""Tests for §6.4 startup logging."""
import pytest
from unittest.mock import MagicMock

from bot.regime.flags import FeatureFlags
from bot.regime.startup import build_startup_message, log_startup, SPEC_VERSION


def test_startup_message_contains_spec_version():
    flags = FeatureFlags({})
    msg = build_startup_message(flags)
    assert SPEC_VERSION in msg
    assert "CogniflowAI" in msg


def test_startup_message_contains_flag_summary():
    flags = FeatureFlags({})
    msg = build_startup_message(flags)
    assert "classifier:" in msg
    assert "SHADOW" in msg


def test_startup_message_with_config_path(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("test: true")
    flags = FeatureFlags({})
    msg = build_startup_message(flags, config_path=str(config_file))
    assert "config.yaml" in msg
    assert "sha256:" in msg


def test_startup_message_missing_config():
    flags = FeatureFlags({})
    msg = build_startup_message(flags, config_path="/nonexistent/config.yaml")
    assert "unreadable" in msg


def test_log_startup_sends_telegram():
    flags = FeatureFlags({})
    mock_telegram = MagicMock()
    msg = log_startup(flags, telegram_alerts=mock_telegram)
    mock_telegram.send.assert_called_once()
    call_arg = mock_telegram.send.call_args[0][0]
    assert "Bot Starting" in call_arg


def test_log_startup_no_telegram():
    flags = FeatureFlags({})
    msg = log_startup(flags, telegram_alerts=None)
    assert "CogniflowAI" in msg


def test_log_startup_returns_message():
    flags = FeatureFlags({})
    msg = log_startup(flags)
    assert isinstance(msg, str)
    assert len(msg) > 0
