"""Tests for scripts/eval_classifier_forward_returns.

No network and no API. All bar data is synthetic; the classifier is
mocked when the backfill orchestration is exercised.
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_DIR / "scripts" / "eval_classifier_forward_returns.py"

spec = importlib.util.spec_from_file_location("efr", SCRIPT_PATH)
efr = importlib.util.module_from_spec(spec)
sys.modules["efr"] = efr
spec.loader.exec_module(efr)


def _bars(n_rows: int, start_price: float = 100.0,
          drift: float = 0.0005) -> pd.DataFrame:
    rng = np.random.default_rng(seed=7)
    idx = pd.date_range(end="2026-06-01", periods=n_rows, freq="B")
    close = start_price * np.cumprod(1 + rng.normal(drift, 0.01, n_rows))
    high = close * (1 + np.abs(rng.normal(0, 0.005, n_rows)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n_rows)))
    open_ = close + rng.normal(0, 0.5, n_rows)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close,
                         "volume": rng.integers(1_000_000, 5_000_000,
                                                n_rows)},
                        index=idx)


def _linear_bars(n_rows: int, start_price: float = 100.0,
                 step: float = 1.0) -> pd.DataFrame:
    """Strictly-increasing closes with stable highs/lows."""
    idx = pd.date_range(end="2026-06-01", periods=n_rows, freq="B")
    close = np.arange(n_rows, dtype=float) * step + start_price
    high = close + 0.2
    low = close - 0.2
    return pd.DataFrame({"open": close, "high": high, "low": low,
                         "close": close, "volume": [1_000_000] * n_rows},
                        index=idx)


class TestComputeForwardMetrics:
    def test_returns_none_when_no_forward_bars(self):
        df = _bars(50)
        last_date = df.index[-1].strftime("%Y-%m-%d")
        assert efr.compute_forward_metrics(df, last_date,
                                           atr_at_classification=1.0) is None

    def test_returns_none_when_only_19_forward(self):
        df = _bars(50)
        # Pick a date with exactly 19 bars after — fall short by 1
        target_idx = len(df) - efr.FORWARD_DAYS
        target_date = df.index[target_idx].strftime("%Y-%m-%d")
        result = efr.compute_forward_metrics(df, target_date,
                                             atr_at_classification=1.0)
        assert result is None

    def test_linear_bars_give_clean_metrics(self):
        # Stable +1/day so the forward 20-day move is +20 exactly,
        # range efficiency is high.
        df = _linear_bars(100, start_price=100.0, step=1.0)
        target_idx = len(df) - efr.FORWARD_DAYS - 1
        target_date = df.index[target_idx].strftime("%Y-%m-%d")
        result = efr.compute_forward_metrics(df, target_date,
                                             atr_at_classification=2.0)
        assert result is not None
        # Forward 20-day net change is exactly 20.0
        assert result["net_change"] == pytest.approx(20.0)
        # ATR=2.0 → directional move in ATR units = 10.0
        assert result["directional_move_atr"] == pytest.approx(10.0)
        assert result["abs_move_atr"] == pytest.approx(10.0)

    def test_falls_back_to_first_bar_after_target_date(self):
        df = _linear_bars(60)
        # Target date one weekend-day later than a present bar
        target_idx = 10
        present_date = df.index[target_idx]
        # Add 1 calendar day — will not match the index but should
        # fall through to the first bar at or after.
        target_date = (present_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        result = efr.compute_forward_metrics(df, target_date,
                                             atr_at_classification=1.0)
        assert result is not None

    def test_none_atr_skips_normalised_metrics(self):
        df = _linear_bars(60)
        target_date = df.index[10].strftime("%Y-%m-%d")
        result = efr.compute_forward_metrics(df, target_date,
                                             atr_at_classification=None)
        assert result["directional_move_atr"] is None
        assert result["abs_move_atr"] is None
        # Range efficiency still computable
        assert result["forward_range_efficiency"] is not None


class TestAggregateByRegime:
    def _sample(self, regime, dir_atr, abs_atr=None, range_eff=0.5):
        return {
            "raw_regime": regime,
            "directional_move_atr": dir_atr,
            "abs_move_atr": abs(dir_atr) if abs_atr is None else abs_atr,
            "forward_range_efficiency": range_eff,
        }

    def test_empty_samples_returns_zeros(self):
        out = efr.aggregate_by_regime([])
        for r in efr.REGIMES:
            assert out["per_regime"][r]["n"] == 0
        assert out["n_total_samples"] == 0

    def test_means_match_simple_input(self):
        samples = [
            self._sample("TRENDING", +1.0),
            self._sample("TRENDING", +3.0),
            self._sample("RANGING", +0.0),
            self._sample("UNCLEAR", -0.5),
        ]
        out = efr.aggregate_by_regime(samples)
        assert out["per_regime"]["TRENDING"]["n"] == 2
        assert out["per_regime"]["TRENDING"]["directional_move_atr_mean"] \
            == pytest.approx(2.0)
        assert out["per_regime"]["RANGING"]["n"] == 1
        assert out["n_total_samples"] == 4

    def test_effect_size_positive_when_trending_larger(self):
        samples = (
            [self._sample("TRENDING", +x) for x in [1.0, 1.5, 2.0, 2.5]]
            + [self._sample("RANGING", x) for x in [0.0, -0.1, 0.1, 0.0]]
        )
        out = efr.aggregate_by_regime(samples)
        es = out["effect_size_trending_vs_other"]
        assert es["mean_diff_directional"] > 0
        assert es["cohens_d_directional"] is not None
        assert es["cohens_d_directional"] > 0
        # large effect for clearly separated buckets
        assert es["cohens_d_directional"] > 0.8

    def test_effect_size_none_when_no_other_samples(self):
        samples = [self._sample("TRENDING", +1.0),
                   self._sample("TRENDING", +2.0)]
        out = efr.aggregate_by_regime(samples)
        es = out["effect_size_trending_vs_other"]
        assert es["mean_diff_directional"] is None

    def test_invalid_regime_ignored(self):
        samples = [
            self._sample("TRENDING", +1.0),
            self._sample("BANANAS", +99.0),
        ]
        out = efr.aggregate_by_regime(samples)
        assert out["n_total_samples"] == 1


class TestTradingDayIndices:
    def test_walks_back_from_eligible_endpoint(self):
        df = _bars(200)
        # lookback=50, step=5, forward=20
        # last_eligible = 200 - 20 - 1 = 179
        # earliest = max(0, 200 - 50) = 150
        # indices: 179, 174, 169, 164, 159, 154 (≥150) → 6 indices
        idx = efr._trading_day_indices(df, lookback_days=50, step=5,
                                       forward_days=20)
        assert idx[-1] == 179
        assert idx[0] == 154
        assert len(idx) == 6
        # ascending order
        assert idx == sorted(idx)

    def test_returns_empty_when_too_short(self):
        df = _bars(10)
        assert efr._trading_day_indices(df, lookback_days=5, step=2,
                                        forward_days=20) == []


class TestLiveMode:
    def test_pairs_cache_rows_with_forward_metrics(self, tmp_path,
                                                    monkeypatch):
        import json as _j
        db = tmp_path / "regime.db"
        conn = sqlite3.connect(str(db))
        conn.execute("""CREATE TABLE regime_classification_cache(
            instrument TEXT, trading_date TEXT,
            input_hash TEXT, classification_json TEXT, created_at TEXT,
            PRIMARY KEY (instrument, trading_date, input_hash))""")
        # Seed: a classification at day 30, df has 100 bars → forward
        # window fits.
        df = _linear_bars(100)
        target_idx = 30
        target_date = df.index[target_idx].strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO regime_classification_cache VALUES (?,?,?,?,?)",
            ("FAKE", target_date, "h",
             _j.dumps({"raw_regime": "TRENDING", "confidence": 0.8,
                       "features": {"atr_14": 2.0}}), "t"),
        )
        conn.commit()
        conn.close()

        # Mock fetch_daily_bars to return our synthetic frame.
        monkeypatch.setattr(efr, "fetch_daily_bars", lambda _: df)
        instruments = [{"symbol": "FAKE", "currency": "USD", "market": None}]
        result = efr.run_live_mode(db, instruments)
        assert len(result["samples"]) == 1
        sample = result["samples"][0]
        assert sample["raw_regime"] == "TRENDING"
        # linear bars with step=1, ATR=2 → directional move = 20/2 = 10
        assert sample["directional_move_atr"] == pytest.approx(10.0)

    def test_skips_rows_without_forward_window(self, tmp_path, monkeypatch):
        import json as _j
        db = tmp_path / "regime.db"
        conn = sqlite3.connect(str(db))
        conn.execute("""CREATE TABLE regime_classification_cache(
            instrument TEXT, trading_date TEXT,
            input_hash TEXT, classification_json TEXT, created_at TEXT,
            PRIMARY KEY (instrument, trading_date, input_hash))""")
        df = _linear_bars(40)
        # Classification on the last day → no forward bars available.
        target_date = df.index[-1].strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO regime_classification_cache VALUES (?,?,?,?,?)",
            ("FAKE", target_date, "h",
             _j.dumps({"raw_regime": "TRENDING",
                       "features": {"atr_14": 1.0}}), "t"),
        )
        conn.commit()
        conn.close()
        monkeypatch.setattr(efr, "fetch_daily_bars", lambda _: df)
        instruments = [{"symbol": "FAKE", "currency": "USD", "market": None}]
        result = efr.run_live_mode(db, instruments)
        assert result["samples"] == []
        assert result["skipped"] == 1


class TestBackfillMode:
    def test_calls_classifier_for_each_backfill_point(self, tmp_path,
                                                       monkeypatch):
        # No cache rows needed; backfill computes features from bars.
        db = tmp_path / "regime.db"
        # Stub the cost tracker so budget check passes
        mock_ct = MagicMock()
        mock_ct.is_budget_exceeded.return_value = False
        mock_ct.get_daily_spend.side_effect = [0.0, 0.10]
        monkeypatch.setattr(efr, "CostTracker", lambda _: mock_ct)

        # Stub classifier: every call returns TRENDING
        from collections import namedtuple
        Stub = namedtuple("Stub", "raw_regime confidence")
        mock_clf = MagicMock()
        mock_clf.is_available.return_value = True
        mock_clf.classify.return_value = Stub("TRENDING", 0.8)
        monkeypatch.setattr(efr, "RegimeClassifier",
                            lambda cache, ct: mock_clf)

        df = _linear_bars(260)
        monkeypatch.setattr(efr, "fetch_daily_bars", lambda _: df)

        instruments = [{"symbol": "FAKE", "currency": "USD", "market": None}]
        result = efr.run_backfill_mode(db, instruments,
                                       lookback_days=50, step=5)
        assert result["samples"], "should have at least one sample"
        # Every sample should be TRENDING (from the mock)
        assert all(s["raw_regime"] == "TRENDING" for s in result["samples"])
        # Classifier was called once per backfill index
        assert mock_clf.classify.call_count == len(result["samples"])
        # Cost block present
        assert result["cost"]["cost_total"] == pytest.approx(0.10)

    def test_aborts_when_budget_exceeded(self, tmp_path, monkeypatch):
        db = tmp_path / "regime.db"
        mock_ct = MagicMock()
        mock_ct.is_budget_exceeded.return_value = True
        monkeypatch.setattr(efr, "CostTracker", lambda _: mock_ct)

        instruments = [{"symbol": "FAKE", "currency": "USD", "market": None}]
        result = efr.run_backfill_mode(db, instruments,
                                       lookback_days=50, step=5)
        assert "budget" in result["error"].lower()
        assert result["samples"] == []


class TestRenderReport:
    def test_no_samples_renders_zeros(self):
        stats = efr.aggregate_by_regime([])
        md = efr.render_report(stats, "live", {"samples": []},
                               "2026-06-01T00:00:00Z")
        assert "Per-regime forward metrics" in md
        assert "TRENDING | 0" in md.replace("  ", "")

    def test_with_samples_includes_effect_size(self):
        samples = (
            [{"raw_regime": "TRENDING", "directional_move_atr": +x,
              "abs_move_atr": x, "forward_range_efficiency": 0.5}
             for x in [1.0, 1.5, 2.0, 2.5]]
            + [{"raw_regime": "RANGING", "directional_move_atr": x,
                "abs_move_atr": abs(x), "forward_range_efficiency": 0.2}
               for x in [0.0, -0.1, 0.1, 0.0]]
        )
        stats = efr.aggregate_by_regime(samples)
        md = efr.render_report(stats, "live",
                               {"samples": samples, "skipped": 0},
                               "2026-06-01T00:00:00Z")
        assert "Cohen's d" in md
        # Mean diff positive (TRENDING > RANGING) → printed with + sign
        assert "+1" in md or "+2" in md or "+0" in md
