"""
tests/test_per_instrument_indicators.py — Test per-instrument indicator resolution.
"""

import json
import os
import tempfile

import pytest


def _make_config(settings, instruments=None):
    """Create a Config object from settings dict and optional instruments."""
    from bot.config import Config

    data = {
        "settings": {
            "host": "127.0.0.1", "port": 4000, "client_id": 1,
            "account": "TEST", "check_interval_mins": 1,
            "portfolio_loss_limit": 1000, "web_dir": "web",
            "rsi_period": 14, "rsi_oversold": 35, "rsi_overbought": 70,
            "williams_r_period": 14, "williams_r_mid": -50,
            "williams_r_oversold": -80, "williams_r_overbought": -20,
            "adx_period": 14, "adx_threshold": 20,
            "ma200_period": 200, "alligator_min_gap_pct": 0.003,
            **settings,
        },
        "layer1_active": instruments or [
            {"symbol": "TEST", "name": "Test", "sec_type": "STK",
             "exchange": "SMART", "currency": "USD", "qty": 1, "enabled": True}
        ],
        "layer2_accumulation": [],
    }
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(data, f)
        path = f.name

    try:
        cfg = Config(path)
        cfg._raw = data  # Ensure raw data is correct
        return cfg, data
    finally:
        os.unlink(path)


def test_global_defaults_when_no_override():
    """Instrument without 'indicators' block gets all global defaults."""
    cfg, _ = _make_config({"rsi_period": 14})
    inst = {"symbol": "TEST", "name": "Test"}
    result = cfg.get_indicator_settings(inst)
    assert result["rsi_period"] == 14
    assert result["adx_threshold"] == 20
    assert result["ma200_period"] == 200


def test_per_instrument_override_single_field():
    """One field overridden, rest from global."""
    cfg, _ = _make_config({"rsi_period": 14, "adx_threshold": 20})
    inst = {"symbol": "TEST", "indicators": {"rsi_period": 10}}
    result = cfg.get_indicator_settings(inst)
    assert result["rsi_period"] == 10
    assert result["adx_threshold"] == 20


def test_per_instrument_override_multiple_fields():
    """Multiple fields overridden."""
    cfg, _ = _make_config({})
    inst = {"symbol": "TEST", "indicators": {"rsi_period": 10, "adx_threshold": 15}}
    result = cfg.get_indicator_settings(inst)
    assert result["rsi_period"] == 10
    assert result["adx_threshold"] == 15


def test_partial_override_inherits_rest():
    """Override 2 of 11 indicator settings. The remaining 9 from global."""
    cfg, _ = _make_config({})
    inst = {"symbol": "TEST", "indicators": {"rsi_period": 7, "ma200_period": 100}}
    result = cfg.get_indicator_settings(inst)
    assert result["rsi_period"] == 7
    assert result["ma200_period"] == 100
    assert result["rsi_oversold"] == 35
    assert result["rsi_overbought"] == 70
    assert result["williams_r_period"] == 14
    assert result["williams_r_mid"] == -50
    assert result["adx_period"] == 14
    assert result["adx_threshold"] == 20
    assert result["alligator_min_gap_pct"] == 0.003


def test_empty_indicators_block_uses_globals():
    """Instrument has indicators: {} (empty dict). Same as no indicators block."""
    cfg, _ = _make_config({})
    inst = {"symbol": "TEST", "indicators": {}}
    result = cfg.get_indicator_settings(inst)
    assert result["rsi_period"] == 14
    assert result["adx_threshold"] == 20


def test_null_override_reverts_to_global():
    """Setting a field to null in per-instrument should use global."""
    cfg, _ = _make_config({"rsi_period": 14})
    inst = {"symbol": "TEST", "indicators": {"rsi_period": None}}
    result = cfg.get_indicator_settings(inst)
    assert result["rsi_period"] == 14  # null -> use global


def test_all_fields_overridable():
    """Every indicator field can be overridden per-instrument."""
    cfg, _ = _make_config({})
    inst = {"symbol": "TEST", "indicators": {
        "rsi_period": 7,
        "rsi_oversold": 25,
        "rsi_overbought": 75,
        "williams_r_period": 21,
        "adx_period": 10,
        "adx_threshold": 30,
        "ma200_period": 150,
    }}
    result = cfg.get_indicator_settings(inst)
    assert result["rsi_period"] == 7
    assert result["rsi_oversold"] == 25
    assert result["rsi_overbought"] == 75
    assert result["williams_r_period"] == 21
    assert result["adx_period"] == 10
    assert result["adx_threshold"] == 30
    assert result["ma200_period"] == 150


def test_two_instruments_different_overrides():
    """BARC has rsi_period=10, MSFT has rsi_period=21."""
    cfg, _ = _make_config({})
    barc = {"symbol": "BARC", "indicators": {"rsi_period": 10}}
    msft = {"symbol": "MSFT", "indicators": {"rsi_period": 21}}
    assert cfg.get_indicator_settings(barc)["rsi_period"] == 10
    assert cfg.get_indicator_settings(msft)["rsi_period"] == 21


def test_instrument_without_indicators_key():
    """Instrument dict has no 'indicators' key at all. Should not raise."""
    cfg, _ = _make_config({})
    inst = {"symbol": "TEST", "name": "Test"}
    result = cfg.get_indicator_settings(inst)
    assert "rsi_period" in result
    assert result["rsi_period"] == 14


def test_global_defaults_when_settings_missing():
    """If global settings are missing some indicator fields, use hardcoded fallbacks."""
    cfg, _ = _make_config({})
    # Remove some settings from the raw data
    cfg._raw["settings"].pop("rsi_period", None)
    cfg._raw["settings"].pop("adx_threshold", None)
    inst = {"symbol": "TEST"}
    result = cfg.get_indicator_settings(inst)
    assert result["rsi_period"] == 14  # hardcoded fallback
    assert result["adx_threshold"] == 20


def test_rsi_period_valid_values():
    """RSI period must be positive integer. Test: 7, 10, 14, 21."""
    cfg, _ = _make_config({})
    for val in [7, 10, 14, 21]:
        inst = {"symbol": "TEST", "indicators": {"rsi_period": val}}
        result = cfg.get_indicator_settings(inst)
        assert result["rsi_period"] == val
        assert result["rsi_period"] > 0


def test_rsi_oversold_below_overbought():
    """rsi_oversold must be less than rsi_overbought."""
    cfg, _ = _make_config({})
    inst = {"symbol": "TEST", "indicators": {"rsi_oversold": 35, "rsi_overbought": 70}}
    result = cfg.get_indicator_settings(inst)
    assert result["rsi_oversold"] < result["rsi_overbought"]


def test_adx_threshold_positive():
    """adx_threshold must be > 0. Test: 15, 20, 25, 30."""
    cfg, _ = _make_config({})
    for val in [15, 20, 25, 30]:
        inst = {"symbol": "TEST", "indicators": {"adx_threshold": val}}
        result = cfg.get_indicator_settings(inst)
        assert result["adx_threshold"] == val
        assert result["adx_threshold"] > 0


def test_ma_period_positive():
    """ma200_period must be > 0. Test: 100, 150, 200."""
    cfg, _ = _make_config({})
    for val in [100, 150, 200]:
        inst = {"symbol": "TEST", "indicators": {"ma200_period": val}}
        result = cfg.get_indicator_settings(inst)
        assert result["ma200_period"] == val
        assert result["ma200_period"] > 0


def test_williams_r_period_positive():
    """williams_r_period must be positive integer."""
    cfg, _ = _make_config({})
    for val in [7, 10, 14, 21]:
        inst = {"symbol": "TEST", "indicators": {"williams_r_period": val}}
        result = cfg.get_indicator_settings(inst)
        assert result["williams_r_period"] == val
        assert result["williams_r_period"] > 0
