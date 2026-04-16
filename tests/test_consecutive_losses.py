"""Tests for Fix 3 — consecutive loss auto-disable."""

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_learning_loop(tmp_dir, instruments_data=None, alerts=None):
    """Create a LearningLoop with a temp DB and config."""
    from bot.plugins.learning_loop import LearningLoop

    # Create temp instruments.json
    if instruments_data is None:
        instruments_data = {
            "settings": {"max_consecutive_losses": 3},
            "layer1_active": [
                {"symbol": "AAPL", "enabled": True, "name": "Apple",
                 "exchange": "SMART", "sec_type": "STK", "currency": "USD"},
            ],
            "layer2_accumulation": [],
        }
    config_path = os.path.join(tmp_dir, 'instruments.json')
    with open(config_path, 'w') as f:
        json.dump(instruments_data, f)

    cfg = MagicMock()
    cfg._raw = instruments_data
    cfg.path = config_path

    ll = LearningLoop(cfg, alerts=alerts)
    ll.db = os.path.join(tmp_dir, 'learning_loop.db')
    ll._init_db()
    return ll, config_path


def _insert_trades(ll, symbol, outcomes):
    """Insert trades with given outcomes (list of 'WIN'/'LOSS')."""
    conn = sqlite3.connect(ll.db)
    conn.execute("PRAGMA journal_mode = WAL")
    for outcome in outcomes:
        conn.execute("""
            INSERT INTO trades (timestamp, symbol, name, action, entry_price,
                                exit_price, qty, pnl_usd, outcome, open, currency)
            VALUES (datetime('now'), ?, 'Test', 'BUY', 100, 95, 10,
                    ?, ?, 0, 'USD')
        """, (symbol, -50 if outcome == 'LOSS' else 50, outcome))
    conn.commit()
    conn.close()


def test_consecutive_losses_counted():
    """3 losses in a row returns 3."""
    with tempfile.TemporaryDirectory() as tmp:
        ll, _ = _make_learning_loop(tmp)
        _insert_trades(ll, "AAPL", ["LOSS", "LOSS", "LOSS"])
        assert ll._check_consecutive_losses("AAPL") == 3


def test_consecutive_losses_reset_on_win():
    """LOSS, LOSS, WIN, LOSS returns 1 (not 3)."""
    with tempfile.TemporaryDirectory() as tmp:
        ll, _ = _make_learning_loop(tmp)
        # Inserted in chronological order, so last inserted is most recent
        _insert_trades(ll, "AAPL", ["LOSS", "LOSS", "WIN", "LOSS"])
        assert ll._check_consecutive_losses("AAPL") == 1


def test_auto_disable_on_threshold():
    """After 3 consecutive losses, instrument is disabled in instruments.json."""
    with tempfile.TemporaryDirectory() as tmp:
        instruments_data = {
            "settings": {"max_consecutive_losses": 3},
            "layer1_active": [
                {"symbol": "AAPL", "enabled": True, "name": "Apple",
                 "exchange": "SMART", "sec_type": "STK", "currency": "USD"},
            ],
            "layer2_accumulation": [],
        }
        ll, config_path = _make_learning_loop(tmp, instruments_data)
        _insert_trades(ll, "AAPL", ["LOSS", "LOSS", "LOSS"])

        # Patch BASE_DIR for _disable_instrument
        with patch('bot.plugins.learning_loop.BASE_DIR', Path(tmp)):
            ll._check_auto_disable("AAPL")

        with open(config_path) as f:
            data = json.load(f)
        inst = next(i for i in data['layer1_active'] if i['symbol'] == 'AAPL')
        assert inst['enabled'] is False
        assert 'consecutive_losses' in inst.get('disabled_reason', '')


def test_alert_sent_on_auto_disable():
    """Telegram alert is sent when instrument is auto-disabled."""
    with tempfile.TemporaryDirectory() as tmp:
        alerts = MagicMock()
        ll, _ = _make_learning_loop(tmp, alerts=alerts)
        _insert_trades(ll, "AAPL", ["LOSS", "LOSS", "LOSS"])

        with patch('bot.plugins.learning_loop.BASE_DIR', Path(tmp)):
            ll._check_auto_disable("AAPL")

        alerts.send.assert_called_once()
        call_msg = alerts.send.call_args[0][0]
        assert "AAPL" in call_msg
        assert "auto-disabled" in call_msg
