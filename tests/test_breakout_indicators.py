"""Frozen breakout indicators — independent-recomputation parity + the
current-bar-exclusion of the 20-day high (the highest-value correctness catch)."""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from backtest.breakout_strategy import (
    wilder_atr, wilder_adx, compute_indicators, first_valid_signal_index,
    ADX_THRESHOLD,
)
from backtest.database import load_bars


def test_wilder_atr_matches_hand_computation():
    high = [10.5, 11.5, 12.5, 11.8, 13.5, 12.4]
    low = [9.5, 10.2, 11.4, 10.8, 12.2, 11.6]
    close = [10, 11, 12, 11, 13, 12]
    atr = wilder_atr(high, low, close, n=3)
    # TR[1..5] = 1.5,1.5,1.2,2.5,1.4 ; seed ATR[3]=mean(1.5,1.5,1.2)=1.4
    assert np.isnan(atr[0]) and np.isnan(atr[2])
    assert atr[3] == pytest.approx(1.4)
    assert atr[4] == pytest.approx((1.4 * 2 + 2.5) / 3)
    assert atr[5] == pytest.approx((atr[4] * 2 + 1.4) / 3)


def test_high20_excludes_current_bar():
    df = pd.DataFrame({
        "open": range(1, 31), "high": [float(x) for x in range(1, 31)],
        "low": range(1, 31), "close": range(1, 31),
        "datetime": pd.date_range("2025-01-01", periods=30, tz="UTC"),
    })
    out = compute_indicators(df)
    # high is strictly increasing, so the *preceding* 20-bar max at i is high[i-1],
    # which is strictly below the current high[i]. If it included the current bar
    # it would equal high[i].
    for i in range(20, 30):
        assert out["high20_excl"].iloc[i] == pytest.approx(df["high"].iloc[i - 1])
        assert out["high20_excl"].iloc[i] < df["high"].iloc[i]


def test_wilder_adx_range_and_first_defined_index():
    rng = np.random.default_rng(0)
    n = 120
    base = np.cumsum(rng.normal(0, 1, n)) + 100
    high = base + 1.0
    low = base - 1.0
    close = base
    adx = wilder_adx(high, low, close, n=14)
    first = 2 * 14 - 1                       # 27
    assert np.all(np.isnan(adx[:first]))
    assert not np.isnan(adx[first])
    defined = adx[~np.isnan(adx)]
    assert np.all((defined >= 0) & (defined <= 100))


def test_adx_high_in_strong_trend():
    # A clean monotonic uptrend should register a strong ADX (> threshold).
    n = 80
    base = np.arange(n, dtype=float) * 2 + 100
    adx = wilder_adx(base + 0.5, base - 0.5, base, n=14)
    assert np.nanmax(adx) > ADX_THRESHOLD


@pytest.fixture(scope="module")
def conn():
    c = sqlite3.connect("backtest.db")
    yield c
    c.close()


def test_entry_signal_requires_all_four_conditions(conn):
    df = load_bars(conn, "AAPL", "daily")
    ind = compute_indicators(df)
    defined = (ind["sma50"].notna() & ind["sma200"].notna() & ind["atr14"].notna()
               & ind["adx14"].notna() & ind["high20_excl"].notna())
    for _, r in ind[ind["entry_signal"]].iterrows():
        assert r["close"] > r["high20_excl"]
        assert r["close"] > r["sma200"]
        assert r["sma50"] > r["sma200"]
        assert r["adx14"] > ADX_THRESHOLD
    # Every defined bar that is NOT a signal must fail at least one condition.
    nonsig = ind[defined & ~ind["entry_signal"]]
    for _, r in nonsig.iterrows():
        ok = (r["close"] > r["high20_excl"] and r["close"] > r["sma200"]
              and r["sma50"] > r["sma200"] and r["adx14"] > ADX_THRESHOLD)
        assert not ok


@pytest.mark.parametrize("sym,expected", [
    ("AAPL", "2025-01-06"), ("NVTS", "2025-02-06"), ("SGLN", "2025-01-08"),
])
def test_warmup_date_matches_audit(conn, sym, expected):
    ind = compute_indicators(load_bars(conn, sym, "daily"))
    fvi = first_valid_signal_index(ind)
    assert str(ind.iloc[fvi]["datetime"].date()) == expected
