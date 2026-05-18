"""Tests for recover-overlay CLI."""
import pytest

from bot.degradation.recover_overlay import recover, list_paused
from bot.degradation.instrument_pause_registry import InstrumentPauseRegistry


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


def test_recover_unknown_overlay(db_path, capsys):
    result = recover("NONEXISTENT", db_path)
    assert result is False
    captured = capsys.readouterr()
    assert "Unknown overlay" in captured.out


def test_recover_clears_pauses(db_path, capsys):
    registry = InstrumentPauseRegistry(db_path)
    registry.pause("AAPL", "Test failure", "MACRO_LOCKOUT")
    registry.pause("MSFT", "Test failure", "MACRO_LOCKOUT")

    result = recover("MACRO_LOCKOUT", db_path)
    assert result is True
    captured = capsys.readouterr()
    assert "Self-test passed" in captured.out
    assert "Cleared 2" in captured.out

    assert registry.is_paused("AAPL") is False
    assert registry.is_paused("MSFT") is False


def test_recover_no_active_pauses(db_path, capsys):
    result = recover("DATA_QUALITY", db_path)
    assert result is True
    captured = capsys.readouterr()
    assert "No active pauses" in captured.out


def test_list_paused_empty(db_path, capsys):
    list_paused(db_path)
    captured = capsys.readouterr()
    assert "No instruments currently paused" in captured.out


def test_list_paused_shows_instruments(db_path, capsys):
    registry = InstrumentPauseRegistry(db_path)
    registry.pause("AAPL", "Test", "DATA_QUALITY")
    list_paused(db_path)
    captured = capsys.readouterr()
    assert "AAPL" in captured.out
