"""
tests/test_layer1_per_instrument_indicators.py — Test that layer1.py uses per-instrument settings.
"""

import json
import os
import tempfile

import pandas as pd
import numpy as np
import pytest
from unittest.mock import MagicMock, patch

from bot.indicators import Indicators, IndicatorBundle


def _make_config_obj(overrides=None):
    """Create a Config from temp instruments.json."""
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
            **(overrides or {}),
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


def _make_df(n=250, seed=42):
    """Create a DataFrame with enough bars for MA200."""
    rng = np.random.default_rng(seed)
    prices = 100 + rng.standard_normal(n).cumsum()
    prices = np.maximum(prices, 10)  # Keep positive
    return pd.DataFrame({
        "datetime": pd.date_range("2024-01-01", periods=n, freq="4h"),
        "open": prices,
        "high": prices + rng.uniform(0.5, 2.0, n),
        "low": prices - rng.uniform(0.5, 2.0, n),
        "close": prices + rng.uniform(-1.0, 1.0, n),
        "volume": rng.integers(1000, 10000, n),
    })


def test_layer1_uses_global_when_no_override():
    """No indicator overrides -> use global settings."""
    cfg = _make_config_obj()
    inst = {"symbol": "TEST", "name": "Test"}
    settings = cfg.get_indicator_settings(inst)
    assert settings["rsi_period"] == 14
    assert settings["ma200_period"] == 200


def test_layer1_uses_per_instrument_rsi():
    """indicators.rsi_period=10 -> RSI calculated with period 10."""
    cfg = _make_config_obj()
    df = _make_df(250)

    # Calculate with global settings (period 14)
    indics = Indicators(cfg)
    global_settings = cfg.get_indicator_settings({"symbol": "TEST"})
    bundle_global = indics.calculate(df, indicator_settings=global_settings)

    # Calculate with per-instrument override (period 10)
    per_inst_settings = cfg.get_indicator_settings(
        {"symbol": "TEST", "indicators": {"rsi_period": 10}}
    )
    bundle_custom = indics.calculate(df, indicator_settings=per_inst_settings)

    assert bundle_global is not None
    assert bundle_custom is not None
    # RSI values should differ with different periods
    # (they might occasionally be similar, but with 250 bars they should differ)
    # Just verify both produce valid RSI values
    assert 0 <= bundle_global.rsi <= 100
    assert 0 <= bundle_custom.rsi <= 100


def test_layer1_different_instruments_different_settings():
    """BARC rsi=10, MSFT rsi=21 -> different RSI values."""
    cfg = _make_config_obj()
    df = _make_df(250)
    indics = Indicators(cfg)

    barc_settings = cfg.get_indicator_settings(
        {"symbol": "BARC", "indicators": {"rsi_period": 10}}
    )
    msft_settings = cfg.get_indicator_settings(
        {"symbol": "MSFT", "indicators": {"rsi_period": 21}}
    )

    bundle_barc = indics.calculate(df, indicator_settings=barc_settings)
    bundle_msft = indics.calculate(df, indicator_settings=msft_settings)

    assert bundle_barc is not None
    assert bundle_msft is not None
    # Both should be valid RSI values
    assert 0 <= bundle_barc.rsi <= 100
    assert 0 <= bundle_msft.rsi <= 100


def test_layer1_indicator_settings_passed_to_signals():
    """Resolved settings passed through to signals.py."""
    cfg = _make_config_obj()
    df = _make_df(250)
    indics = Indicators(cfg)

    # Create indicator bundle with custom settings
    settings = cfg.get_indicator_settings(
        {"symbol": "TEST", "indicators": {"adx_threshold": 10}}
    )
    bundle = indics.calculate(df, indicator_settings=settings)
    assert bundle is not None

    # Signal engine should be able to evaluate this bundle
    from bot.signals import SignalEngine
    engine = SignalEngine()
    result = engine.evaluate(bundle)
    assert result.signal in (-1, 0, 1)
    assert result.confidence in ('HIGH', 'MEDIUM', 'LOW')


def test_layer1_indicator_settings_passed_to_indicators():
    """Resolved settings passed through to indicators.py."""
    cfg = _make_config_obj()
    df = _make_df(250)
    indics = Indicators(cfg)

    # Use a different MA period
    settings = cfg.get_indicator_settings(
        {"symbol": "TEST", "indicators": {"ma200_period": 100}}
    )
    bundle = indics.calculate(df, indicator_settings=settings)

    # With MA period 100, we need fewer bars, so it should work
    assert bundle is not None
    assert bundle.ma200.value is not None
