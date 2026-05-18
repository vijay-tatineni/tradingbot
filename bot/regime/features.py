"""
Deterministic regime feature computation — §9.1 of CLAUDE_STRATEGY_SPEC_v3.

Pure functions: DataFrame in, feature dict out. No side effects.
"""
import numpy as np
import pandas as pd


def compute_regime_features(df: pd.DataFrame) -> dict:
    """Compute regime classification features from OHLCV data.

    Args:
        df: DataFrame with columns [open, high, low, close, volume]
            and at least 200 rows for MA200 slope.

    Returns:
        Dict of feature name → value.
    """
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values

    features = {}

    features["adx_14"] = _adx(high, low, close, period=14)
    atr = _atr(high, low, close, period=14)
    features["atr_14"] = atr
    features["atr_pct"] = (atr / close[-1] * 100) if close[-1] != 0 else 0.0

    features["ma_200_slope_pct_per_day"] = _ma_slope(close, ma_period=200, lookback=20)

    features["range_efficiency"] = _range_efficiency(close, high, low, lookback=20)

    features["realized_volatility_20d"] = _realized_volatility(close, lookback=20)

    ma200 = _sma(close, 200)
    if ma200 is not None and ma200 != 0:
        features["close_above_ma200"] = bool(close[-1] > ma200)
        features["distance_to_ma200_pct"] = (close[-1] - ma200) / ma200 * 100
    else:
        features["close_above_ma200"] = None
        features["distance_to_ma200_pct"] = None

    return features


def _sma(data: np.ndarray, period: int):
    if len(data) < period:
        return None
    return float(np.mean(data[-period:]))


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
    if len(high) < period + 1:
        return 0.0
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        ),
    )
    return float(np.mean(tr[-period:]))


def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
    if len(high) < period * 2 + 1:
        return 0.0

    up_move = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        ),
    )

    atr_vals = _ema_array(tr, period)
    plus_di = 100 * _ema_array(plus_dm, period) / np.where(atr_vals > 0, atr_vals, 1)
    minus_di = 100 * _ema_array(minus_dm, period) / np.where(atr_vals > 0, atr_vals, 1)

    di_sum = plus_di + minus_di
    dx = 100 * np.abs(plus_di - minus_di) / np.where(di_sum > 0, di_sum, 1)

    adx_vals = _ema_array(dx, period)
    return float(adx_vals[-1]) if len(adx_vals) > 0 else 0.0


def _ema_array(data: np.ndarray, period: int) -> np.ndarray:
    alpha = 1.0 / period
    result = np.zeros_like(data, dtype=float)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


def _ma_slope(close: np.ndarray, ma_period: int = 200, lookback: int = 20) -> float:
    """Linear regression slope of MA200 over last `lookback` bars, as pct per day."""
    if len(close) < ma_period + lookback:
        return 0.0
    ma_vals = np.convolve(close, np.ones(ma_period) / ma_period, mode="valid")
    if len(ma_vals) < lookback:
        return 0.0
    recent = ma_vals[-lookback:]
    x = np.arange(lookback, dtype=float)
    slope = np.polyfit(x, recent, 1)[0]
    mean_ma = np.mean(recent)
    if mean_ma == 0:
        return 0.0
    return float(slope / mean_ma * 100)


def _range_efficiency(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                      lookback: int = 20) -> float:
    """Net price change / sum of bar ranges over last `lookback` bars."""
    if len(close) < lookback + 1:
        return 0.0
    net_change = abs(close[-1] - close[-lookback - 1])
    bar_ranges = high[-lookback:] - low[-lookback:]
    total_range = float(np.sum(bar_ranges))
    if total_range == 0:
        return 0.0
    return float(net_change / total_range)


def _realized_volatility(close: np.ndarray, lookback: int = 20) -> float:
    if len(close) < lookback + 1:
        return 0.0
    returns = np.diff(np.log(close[-lookback - 1:]))
    return float(np.std(returns) * np.sqrt(252) * 100)
