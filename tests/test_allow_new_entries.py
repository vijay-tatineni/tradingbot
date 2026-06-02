"""
tests/test_allow_new_entries.py

Tests for the `allow_new_entries` per-instrument flag (exits-only mode).

Behaviour under test (bot/layer1.py:_process_instrument):
  - allow_new_entries=false + flat  -> no new position opened, suppression logged
  - allow_new_entries=false + open  -> exit management (trail/TP/emergency) still runs
  - allow_new_entries=true/absent   -> unchanged behaviour (entries fire)

The flag gates ONLY the flat (no-position) entry branch. The pos != 0 exit
block is deliberately untouched, so an open position keeps its stops managed.
"""

import json
import os
import tempfile
import types
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from bot.indicators import Indicators


# ── harness ──────────────────────────────────────────────────────────

def _make_config_obj():
    from bot.config import Config
    data = {
        "settings": {
            "host": "127.0.0.1", "port": 4000, "client_id": 1,
            "account": "TEST", "check_interval_mins": 1,
            "portfolio_loss_limit": 1000, "web_dir": "web",
            "default_target_notional": 1000,
            "max_open_positions": 10, "max_entries_per_cycle": 2,
            "rsi_period": 14, "rsi_oversold": 35, "rsi_overbought": 70,
            "williams_r_period": 14, "williams_r_mid": -50,
            "williams_r_oversold": -80, "williams_r_overbought": -20,
            "adx_period": 14, "adx_threshold": 20,
            "ma200_period": 200, "alligator_min_gap_pct": 0.003,
        },
        "layer1_active": [
            {"symbol": "TEST", "name": "Test", "sec_type": "STK",
             "exchange": "SMART", "currency": "USD", "qty": 1, "enabled": True},
        ],
        "layer2_accumulation": [],
    }
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(data, f)
        path = f.name
    cfg = Config(path)
    os.unlink(path)
    return cfg


def _make_bundle():
    """A real IndicatorBundle so the log f-strings format cleanly."""
    rng = np.random.default_rng(42)
    prices = 100 + rng.standard_normal(250).cumsum()
    prices = np.maximum(prices, 10)
    df = pd.DataFrame({
        "datetime": pd.date_range("2024-01-01", periods=250, freq="4h"),
        "open": prices,
        "high": prices + rng.uniform(0.5, 2.0, 250),
        "low": prices - rng.uniform(0.5, 2.0, 250),
        "close": prices + rng.uniform(-1.0, 1.0, 250),
        "volume": rng.integers(1000, 10000, 250),
    })
    cfg = _make_config_obj()
    bundle = Indicators(cfg).calculate(
        df, indicator_settings=cfg.get_indicator_settings({"symbol": "TEST"})
    )
    assert bundle is not None
    return df, bundle


def _make_active_trading(signal, pos_qty, *, allow_new_entries=None,
                         is_watching=False, emergency_exit=None,
                         smart_exit=None):
    """Construct an ActiveTrading wired with mocks, and the inst dict.

    signal             : engine signal (1 / 0 / -1)
    pos_qty            : current position qty (0 = flat)
    allow_new_entries  : value for the flag (None = key absent)
    emergency_exit     : tracker.check_emergency_stop return
    smart_exit         : tracker.check_exit return (bar-close trail/TP)
    """
    from bot.layer1 import ActiveTrading

    cfg = _make_config_obj()
    df, bundle = _make_bundle()

    at = ActiveTrading(cfg, broker=MagicMock(), plugins=[])
    # Per-cycle counters normally set by run(); set here since we call
    # _process_instrument directly.
    at._entries_this_cycle = 0
    at._open_count = 0

    # Market open + bars/indicators
    at.hours = MagicMock()
    at.hours.is_open.return_value = True
    at.hours.status.return_value = "OPEN"
    at.broker.fetch_bars.return_value = df
    at.indics = MagicMock()
    at.indics.calculate.return_value = bundle

    # Engine signal
    at.engine = MagicMock()
    at.engine.evaluate.return_value = types.SimpleNamespace(
        signal=signal, confidence='HIGH', reason='')

    # Position info (flat or open)
    pos_info = types.SimpleNamespace(qty=pos_qty, unreal_pnl=0.0,
                                     price=bundle.price)
    at.broker.get_position_info.return_value = pos_info

    # Tracker
    at.tracker = MagicMock()
    at.tracker.is_watching.return_value = is_watching
    at.tracker.check_emergency_stop.return_value = emergency_exit
    at.tracker.check_exit.return_value = smart_exit
    at.tracker.get_stop_level.return_value = 0.0
    at.tracker.get_peak.return_value = 0.0
    at.tracker.watch_info.return_value = None
    at.tracker.open.get.return_value = types.SimpleNamespace(
        entry_price=bundle.price, side='LONG')

    # Isolate entry-gating from the downstream guards (they all pass).
    at._regime_filter_allows = MagicMock(return_value=True)
    at._validate_entry = MagicMock(return_value=True)
    at._llm_sentiment_check = MagicMock(return_value=True)
    at._broadcast_signal_to_shadow = MagicMock()
    # Capture `action` without building a full row.
    at._build_row = MagicMock(return_value={})

    # broker order calls succeed if reached
    fill = types.SimpleNamespace(fill_price=bundle.price, filled_qty=1)
    at.broker.handle_signal.return_value = ("BOUGHT 1 @ 100", fill)
    at.broker.place_order.return_value = fill
    at.broker.close_position.return_value = fill

    inst = {"symbol": "TEST", "name": "Test", "sec_type": "STK",
            "exchange": "SMART", "currency": "USD", "qty": 1,
            "contract": MagicMock(), "long_only": True}
    if allow_new_entries is not None:
        inst["allow_new_entries"] = allow_new_entries
    return at, inst


def _action_of(at):
    """The `action` string passed to the mocked _build_row (6th positional)."""
    assert at._build_row.called
    return at._build_row.call_args[0][5]


# ── 1. flat + allow_new_entries=false -> no entry, logged ─────────────

@patch("bot.layer1.calculate_qty", return_value=1)
@patch("bot.layer1.is_bar_close", return_value=False)
def test_flat_suppressed_no_entry(_bc, _qty):
    at, inst = _make_active_trading(signal=1, pos_qty=0,
                                    allow_new_entries=False)
    at._process_instrument(inst)

    # No order placed by any path
    at.broker.handle_signal.assert_not_called()
    at.broker.place_order.assert_not_called()
    # And the suppression is surfaced in the action
    assert _action_of(at) == "ENTRY SUPPRESSED (allow_new_entries=false)"


@patch("bot.layer1.calculate_qty", return_value=1)
@patch("bot.layer1.is_bar_close", return_value=False)
def test_flat_suppressed_logs(_bc, _qty):
    """The suppression emits a distinct log line."""
    at, inst = _make_active_trading(signal=1, pos_qty=0,
                                    allow_new_entries=False)
    with patch("bot.layer1.log") as mock_log:
        at._process_instrument(inst)
    logged = " ".join(str(c.args[0]) for c in mock_log.call_args_list if c.args)
    assert "entry suppressed" in logged
    assert "allow_new_entries=false" in logged


# ── 2. open position + allow_new_entries=false -> exits still run ─────

@patch("bot.layer1.calculate_qty", return_value=1)
@patch("bot.layer1.is_bar_close", return_value=False)
def test_open_emergency_stop_still_fires(_bc, _qty):
    """Emergency stop is evaluated and fires for a disabled-entry instrument
    that holds an open position."""
    at, inst = _make_active_trading(
        signal=0, pos_qty=10, allow_new_entries=False,
        emergency_exit="EMERGENCY_STOP -10.0%")
    at._process_instrument(inst)

    # Exit logic ran...
    at.tracker.check_emergency_stop.assert_called()
    # ...and actually closed the position.
    at.broker.close_position.assert_called_once()
    assert "EMERGENCY_STOP" in _action_of(at)


@patch("bot.layer1.calculate_qty", return_value=1)
@patch("bot.layer1.is_bar_close", return_value=True)
def test_open_trail_tp_still_evaluated(_bc, _qty):
    """On bar close, trail/TP (check_exit) is evaluated and fires for a
    disabled-entry instrument with an open position."""
    at, inst = _make_active_trading(
        signal=0, pos_qty=10, allow_new_entries=False,
        emergency_exit=None, smart_exit="TAKE_PROFIT +8.0%")
    at._process_instrument(inst)

    at.tracker.check_emergency_stop.assert_called()
    at.tracker.check_exit.assert_called()
    at.broker.close_position.assert_called_once()
    assert "TAKE_PROFIT" in _action_of(at)


# ── 3. allow_new_entries true / absent -> unchanged ──────────────────

@patch("bot.layer1.calculate_qty", return_value=1)
@patch("bot.layer1.is_bar_close", return_value=False)
def test_flat_entry_fires_when_flag_true(_bc, _qty):
    at, inst = _make_active_trading(signal=1, pos_qty=0,
                                    allow_new_entries=True)
    at._process_instrument(inst)
    at.broker.handle_signal.assert_called_once()
    assert _action_of(at) != "ENTRY SUPPRESSED (allow_new_entries=false)"


@patch("bot.layer1.calculate_qty", return_value=1)
@patch("bot.layer1.is_bar_close", return_value=False)
def test_flat_entry_fires_when_flag_absent(_bc, _qty):
    """Backward compat: no key present -> entries fire as before."""
    at, inst = _make_active_trading(signal=1, pos_qty=0,
                                    allow_new_entries=None)
    assert "allow_new_entries" not in inst
    at._process_instrument(inst)
    at.broker.handle_signal.assert_called_once()


# ── 4. the suppressed signal is one that WOULD have fired ────────────

@patch("bot.layer1.calculate_qty", return_value=1)
@patch("bot.layer1.is_bar_close", return_value=False)
def test_suppressed_signal_would_have_entered(_bc, _qty):
    """Control: same flat BUY signal with the flag enabled DOES enter,
    proving the suppression in test_flat_suppressed_no_entry blocked a
    real entry rather than a no-op cycle."""
    at_on, inst_on = _make_active_trading(signal=1, pos_qty=0,
                                          allow_new_entries=True)
    at_on._process_instrument(inst_on)
    at_on.broker.handle_signal.assert_called_once()

    at_off, inst_off = _make_active_trading(signal=1, pos_qty=0,
                                            allow_new_entries=False)
    at_off._process_instrument(inst_off)
    at_off.broker.handle_signal.assert_not_called()
