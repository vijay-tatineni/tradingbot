"""Unit tests for scripts/generate_labelling_worksheet.

Network-free: synthetic OHLCV DataFrames, no yfinance calls.
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_DIR / "scripts" / "generate_labelling_worksheet.py"

# Load the script as a module (it's not packaged under bot/)
spec = importlib.util.spec_from_file_location("generate_labelling_worksheet",
                                              SCRIPT_PATH)
glw = importlib.util.module_from_spec(spec)
sys.modules["generate_labelling_worksheet"] = glw
spec.loader.exec_module(glw)


def _synthetic_bars(n_rows: int, start_price: float = 100.0) -> pd.DataFrame:
    """Build a deterministic upward-drifting OHLCV frame with daily index."""
    rng = np.random.default_rng(seed=42)
    idx = pd.date_range(end="2026-06-01", periods=n_rows, freq="B")
    close = start_price * np.cumprod(1 + rng.normal(0.0005, 0.01, n_rows))
    high = close * (1 + np.abs(rng.normal(0, 0.005, n_rows)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n_rows)))
    open_ = close + rng.normal(0, 0.5, n_rows)
    volume = rng.integers(1_000_000, 5_000_000, n_rows)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": volume}, index=idx)


class TestYfSymbolMapping:
    def test_lse_appends_l(self):
        assert glw.yf_symbol({"symbol": "BARC", "currency": "GBP",
                              "market": "LSE"}) == "BARC.L"

    def test_eur_appends_pa(self):
        assert glw.yf_symbol({"symbol": "SU", "currency": "EUR",
                              "market": None}) == "SU.PA"

    def test_usd_unchanged(self):
        assert glw.yf_symbol({"symbol": "AAPL", "currency": "USD",
                              "market": None}) == "AAPL"

    def test_unknown_currency_returns_none(self):
        assert glw.yf_symbol({"symbol": "FOO", "currency": "JPY",
                              "market": None}) is None


class TestBuildWindows:
    def test_too_short_returns_empty(self):
        df = _synthetic_bars(100)
        assert glw.build_windows("FOO", df) == []

    def test_typical_input_produces_expected_window_count(self):
        df = _synthetic_bars(500)
        windows = glw.build_windows("FOO", df)
        # 90-day lookback, size=6, step=5 → 18 windows (incl. snapped tail)
        assert 15 <= len(windows) <= 20

    def test_window_ids_unique_and_prefixed_with_symbol(self):
        df = _synthetic_bars(500)
        windows = glw.build_windows("BARC", df)
        ids = [w["window_id"] for w in windows]
        assert len(set(ids)) == len(ids)
        for wid in ids:
            assert wid.startswith("BARC_")

    def test_placeholders_are_none(self):
        df = _synthetic_bars(500)
        for w in glw.build_windows("FOO", df):
            assert w["my_label"] is None
            assert w["my_confidence"] is None
            assert w["my_notes"] is None

    def test_classifier_features_populated(self):
        df = _synthetic_bars(500)
        windows = glw.build_windows("FOO", df)
        feats = windows[-1]["classifier_features_at_end"]
        # All keys returned by compute_regime_features should appear
        for key in ("adx_14", "atr_14", "atr_pct",
                    "ma_200_slope_pct_per_day", "range_efficiency",
                    "realized_volatility_20d",
                    "close_above_ma200", "distance_to_ma200_pct"):
            assert key in feats

    def test_window_internal_aggregates_consistent(self):
        df = _synthetic_bars(500)
        windows = glw.build_windows("FOO", df)
        last = windows[-1]
        # pct_change should be computable from start/end prices
        expected_pct = (last["end_price"] - last["start_price"]) \
            / last["start_price"] * 100
        assert abs(last["pct_change"] - round(expected_pct, 2)) < 0.01
        # range bounds make sense
        assert last["range_high"] >= last["end_price"]
        assert last["range_low"] <= last["end_price"]


class TestLabelPreservationContract:
    def test_re_load_preserves_existing_labels(self, tmp_path, monkeypatch):
        """A re-run after labels are entered must not clobber them."""
        # Point the script's output path at a temp file with pre-existing labels.
        sample_existing = {
            "instruments": [
                {
                    "instrument": "FOO",
                    "windows": [
                        {"window_id": "FOO_2026-05-01_2026-05-09",
                         "my_label": "TRENDING", "my_confidence": 0.8,
                         "my_notes": "clear uptrend"}
                    ],
                }
            ]
        }
        out = tmp_path / "regime_labels.json"
        out.write_text(__import__("json").dumps(sample_existing))
        monkeypatch.setattr(glw, "OUTPUT_FILE", out)

        preserved = glw._load_existing_labels()
        assert ("FOO", "FOO_2026-05-01_2026-05-09") in preserved
        assert preserved[("FOO", "FOO_2026-05-01_2026-05-09")]["my_label"] \
            == "TRENDING"

    def test_load_existing_skips_windows_without_label(self, tmp_path, monkeypatch):
        sample_existing = {
            "instruments": [
                {
                    "instrument": "FOO",
                    "windows": [
                        {"window_id": "FOO_2026-05-01_2026-05-09",
                         "my_label": None, "my_confidence": None,
                         "my_notes": None}
                    ],
                }
            ]
        }
        out = tmp_path / "regime_labels.json"
        out.write_text(__import__("json").dumps(sample_existing))
        monkeypatch.setattr(glw, "OUTPUT_FILE", out)
        assert glw._load_existing_labels() == {}
