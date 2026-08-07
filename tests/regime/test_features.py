"""Unit tests for regime feature computation — §9.1."""
import numpy as np
import pandas as pd
import pytest

from bot.regime.features import compute_regime_features


def _make_trending_df(n=250):
    """Generate upward-trending OHLCV data."""
    np.random.seed(42)
    close = 100 + np.cumsum(np.random.normal(0.5, 1.0, n))
    high = close + np.abs(np.random.normal(1, 0.5, n))
    low = close - np.abs(np.random.normal(1, 0.5, n))
    open_ = close + np.random.normal(0, 0.5, n)
    volume = np.random.randint(100000, 1000000, n)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    })


def _make_ranging_df(n=250):
    """Generate ranging OHLCV data oscillating around 100."""
    np.random.seed(42)
    close = 100 + 5 * np.sin(np.linspace(0, 20 * np.pi, n)) + np.random.normal(0, 1, n)
    high = close + np.abs(np.random.normal(1, 0.5, n))
    low = close - np.abs(np.random.normal(1, 0.5, n))
    open_ = close + np.random.normal(0, 0.5, n)
    volume = np.random.randint(100000, 1000000, n)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    })


class TestFeatures:
    def test_returns_all_keys(self):
        df = _make_trending_df()
        features = compute_regime_features(df)
        expected_keys = {
            "adx_14", "atr_14", "atr_pct",
            "ma_200_slope_pct_per_day", "range_efficiency",
            "realized_volatility_20d",
            "close_above_ma200", "distance_to_ma200_pct",
        }
        assert set(features.keys()) == expected_keys

    def test_trending_data_high_range_efficiency(self):
        df = _make_trending_df()
        features = compute_regime_features(df)
        assert features["range_efficiency"] > 0.1

    def test_ranging_data_low_range_efficiency(self):
        df = _make_ranging_df()
        features = compute_regime_features(df)
        assert features["range_efficiency"] < 0.3

    def test_atr_positive(self):
        df = _make_trending_df()
        features = compute_regime_features(df)
        assert features["atr_14"] > 0
        assert features["atr_pct"] > 0

    def test_volatility_positive(self):
        df = _make_trending_df()
        features = compute_regime_features(df)
        assert features["realized_volatility_20d"] > 0

    def test_short_data_returns_zeros(self):
        df = pd.DataFrame({
            "open": [100.0], "high": [101.0], "low": [99.0],
            "close": [100.5], "volume": [50000],
        })
        features = compute_regime_features(df)
        assert features["adx_14"] == 0.0
        assert features["ma_200_slope_pct_per_day"] == 0.0
        assert features["close_above_ma200"] is None

    def test_ma200_slope_trending(self):
        df = _make_trending_df()
        features = compute_regime_features(df)
        assert features["ma_200_slope_pct_per_day"] > 0
