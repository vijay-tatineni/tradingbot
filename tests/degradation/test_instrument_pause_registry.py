"""Tests for instrument pause registry."""
import os
import tempfile

import pytest

from bot.degradation.instrument_pause_registry import InstrumentPauseRegistry


@pytest.fixture
def registry(tmp_path):
    db_path = str(tmp_path / "test.db")
    return InstrumentPauseRegistry(db_path)


def test_initially_not_paused(registry):
    assert registry.is_paused("AAPL") is False


def test_pause_and_check(registry):
    registry.pause("AAPL", "Test failure", "EARNINGS_LOCKOUT")
    assert registry.is_paused("AAPL") is True
    assert registry.pause_reason("AAPL") == "Test failure"


def test_clear(registry):
    registry.pause("AAPL", "Test failure", "EARNINGS_LOCKOUT")
    registry.clear("AAPL", "recover-overlay CLI")
    assert registry.is_paused("AAPL") is False
    assert registry.pause_reason("AAPL") is None


def test_list_paused(registry):
    registry.pause("AAPL", "Reason 1", "EARNINGS_LOCKOUT")
    registry.pause("MSFT", "Reason 2", "DATA_QUALITY")
    paused = registry.list_paused()
    assert len(paused) == 2
    instruments = {p[0] for p in paused}
    assert instruments == {"AAPL", "MSFT"}


def test_clear_by_overlay(registry):
    registry.pause("AAPL", "Earnings fail", "EARNINGS_LOCKOUT")
    registry.pause("MSFT", "Earnings fail", "EARNINGS_LOCKOUT")
    registry.pause("GOOG", "Data fail", "DATA_QUALITY")

    cleared = registry.clear_by_overlay("EARNINGS_LOCKOUT", "recover-overlay CLI")
    assert set(cleared) == {"AAPL", "MSFT"}
    assert registry.is_paused("AAPL") is False
    assert registry.is_paused("MSFT") is False
    assert registry.is_paused("GOOG") is True


def test_persistence(tmp_path):
    db_path = str(tmp_path / "persist.db")
    reg1 = InstrumentPauseRegistry(db_path)
    reg1.pause("AAPL", "Test", "DATA_QUALITY")

    reg2 = InstrumentPauseRegistry(db_path)
    assert reg2.is_paused("AAPL") is True


def test_pause_replaces_existing(registry):
    registry.pause("AAPL", "First reason", "EARNINGS_LOCKOUT")
    registry.pause("AAPL", "Second reason", "DATA_QUALITY")
    assert registry.pause_reason("AAPL") == "Second reason"
