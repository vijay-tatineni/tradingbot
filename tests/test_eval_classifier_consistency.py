"""Tests for scripts/eval_classifier_consistency.

No live API calls — the API-driving function is tested separately with
a mocked classifier. The pure aggregation + report rendering is tested
in isolation.
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_DIR / "scripts" / "eval_classifier_consistency.py"

spec = importlib.util.spec_from_file_location("ecc", SCRIPT_PATH)
ecc = importlib.util.module_from_spec(spec)
sys.modules["ecc"] = ecc
spec.loader.exec_module(ecc)


class TestScoreCalls:
    def test_all_agree(self):
        calls = [{"raw_regime": "TRENDING", "confidence": 0.8}] * 5
        out = ecc.score_calls(calls)
        assert out["modal_label"] == "TRENDING"
        assert out["modal_agreement"] == 1.0
        assert out["unstable"] is False
        assert out["confidence_mean"] == pytest.approx(0.8)
        assert out["confidence_stdev"] == pytest.approx(0.0)

    def test_split_4_to_1(self):
        calls = [{"raw_regime": "TRENDING", "confidence": 0.8}] * 4 \
              + [{"raw_regime": "RANGING", "confidence": 0.5}]
        out = ecc.score_calls(calls)
        assert out["modal_label"] == "TRENDING"
        assert out["modal_agreement"] == 0.8
        assert out["unstable"] is False  # 80% is the threshold edge

    def test_split_3_to_2_is_unstable(self):
        calls = [{"raw_regime": "TRENDING", "confidence": 0.8}] * 3 \
              + [{"raw_regime": "UNCLEAR", "confidence": 0.5}] * 2
        out = ecc.score_calls(calls)
        assert out["modal_label"] == "TRENDING"
        assert out["modal_agreement"] == pytest.approx(0.6)
        assert out["unstable"] is True

    def test_confidence_stdev_nontrivial(self):
        calls = [
            {"raw_regime": "TRENDING", "confidence": 0.9},
            {"raw_regime": "TRENDING", "confidence": 0.5},
            {"raw_regime": "TRENDING", "confidence": 0.7},
        ]
        out = ecc.score_calls(calls)
        # stdev of {0.9, 0.5, 0.7} is ~0.2
        assert 0.15 < out["confidence_stdev"] < 0.25

    def test_handles_none_confidence(self):
        calls = [
            {"raw_regime": "UNCLEAR", "confidence": None},
            {"raw_regime": "UNCLEAR", "confidence": 0.4},
        ]
        out = ecc.score_calls(calls)
        assert out["confidence_mean"] == pytest.approx(0.4)


class TestLatestFeaturesPerInstrument:
    def test_returns_most_recent_row_per_instrument(self, tmp_path):
        db = tmp_path / "regime.db"
        conn = sqlite3.connect(str(db))
        conn.execute("""CREATE TABLE regime_classification_cache(
            instrument TEXT, trading_date TEXT,
            input_hash TEXT, classification_json TEXT, created_at TEXT,
            PRIMARY KEY (instrument, trading_date, input_hash))""")
        conn.executemany(
            "INSERT INTO regime_classification_cache VALUES (?,?,?,?,?)",
            [
                ("BARC", "2026-05-20", "h1",
                 '{"features": {"adx_14": 10.0}}', "t"),
                ("BARC", "2026-05-29", "h2",
                 '{"features": {"adx_14": 22.0}}', "t"),
                ("ANTO", "2026-05-29", "h3",
                 '{"features": {"adx_14": 31.0}}', "t"),
            ],
        )
        conn.commit()
        conn.close()
        out = ecc.latest_features_per_instrument(db)
        assert set(out.keys()) == {"BARC", "ANTO"}
        # BARC's latest row (2026-05-29) — adx_14 = 22.0
        assert out["BARC"][1]["adx_14"] == 22.0
        assert out["BARC"][0] == "2026-05-29"

    def test_missing_db_returns_empty(self, tmp_path):
        assert ecc.latest_features_per_instrument(tmp_path / "no.db") == {}

    def test_row_without_features_is_skipped(self, tmp_path):
        db = tmp_path / "regime.db"
        conn = sqlite3.connect(str(db))
        conn.execute("""CREATE TABLE regime_classification_cache(
            instrument TEXT, trading_date TEXT,
            input_hash TEXT, classification_json TEXT, created_at TEXT,
            PRIMARY KEY (instrument, trading_date, input_hash))""")
        conn.execute(
            "INSERT INTO regime_classification_cache VALUES (?,?,?,?,?)",
            ("BARC", "2026-05-29", "h", '{"raw_regime": "TRENDING"}', "t"),
        )
        conn.commit()
        conn.close()
        assert ecc.latest_features_per_instrument(db) == {}


class TestRenderReport:
    def test_includes_per_instrument_table(self):
        results = {
            "BARC": {
                "features_from_date": "2026-05-29",
                "calls": [
                    {"raw_regime": "TRENDING", "confidence": 0.8},
                    {"raw_regime": "TRENDING", "confidence": 0.75},
                    {"raw_regime": "TRENDING", "confidence": 0.85},
                    {"raw_regime": "RANGING", "confidence": 0.5},
                    {"raw_regime": "TRENDING", "confidence": 0.8},
                ],
                **ecc.score_calls([
                    {"raw_regime": "TRENDING", "confidence": 0.8},
                    {"raw_regime": "TRENDING", "confidence": 0.75},
                    {"raw_regime": "TRENDING", "confidence": 0.85},
                    {"raw_regime": "RANGING", "confidence": 0.5},
                    {"raw_regime": "TRENDING", "confidence": 0.8},
                ]),
            },
        }
        md = ecc.render_report(results, {"cost_total": 0.21}, "2026-06-01T00:00Z")
        assert "BARC" in md
        assert "TRENDING" in md
        assert "80%" in md  # modal agreement
        assert "$0.21" in md or "0.2100" in md

    def test_error_rows_render_gracefully(self):
        results = {"FOO": {"error": "no cached features"}}
        md = ecc.render_report(results, {"cost_total": 0}, "now")
        assert "no cached features" in md


class TestRunOrchestration:
    """The classifier itself is mocked here — the goal is to verify
    `run()` wires inputs through correctly without burning API calls."""

    def _seed_cache(self, db_path, sym="BARC", features=None):
        import json as _j
        features = features or {"adx_14": 22.0, "atr_14": 1.0, "atr_pct": 1.0,
                                "ma_200_slope_pct_per_day": 0.05,
                                "range_efficiency": 0.4,
                                "realized_volatility_20d": 25.0,
                                "close_above_ma200": True,
                                "distance_to_ma200_pct": 5.0}
        conn = sqlite3.connect(str(db_path))
        conn.execute("""CREATE TABLE IF NOT EXISTS regime_classification_cache(
            instrument TEXT, trading_date TEXT,
            input_hash TEXT, classification_json TEXT, created_at TEXT,
            PRIMARY KEY (instrument, trading_date, input_hash))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS regime_classification_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, instrument TEXT, trading_date TEXT, model TEXT,
            prompt_version TEXT, input_tokens INTEGER, output_tokens INTEGER,
            cost_usd REAL, latency_ms INTEGER, raw_regime TEXT,
            confidence REAL, cache_hit INTEGER, error TEXT)""")
        conn.execute(
            "INSERT INTO regime_classification_cache VALUES (?,?,?,?,?)",
            (sym, "2026-05-29", "h",
             _j.dumps({"features": features}), "t"),
        )
        conn.commit()
        conn.close()

    def test_run_calls_classifier_n_times_per_instrument(self, tmp_path,
                                                         monkeypatch):
        db = tmp_path / "regime.db"
        self._seed_cache(db, "BARC")

        # Mock RegimeClassifier so no real API call happens.
        from collections import namedtuple
        ResultStub = namedtuple("ResultStub", "raw_regime confidence")
        mock_clf = MagicMock()
        mock_clf.is_available.return_value = True
        mock_clf.classify.side_effect = (
            lambda inst, day, feats, force=True:
                ResultStub(raw_regime="TRENDING", confidence=0.8)
        )
        monkeypatch.setattr(ecc, "RegimeClassifier",
                            lambda cache, cost_tracker: mock_clf)

        results, cost_info, err = ecc.run(["BARC"], n_calls=5, prod_db=db)
        assert err is None
        assert results["BARC"]["modal_label"] == "TRENDING"
        assert results["BARC"]["modal_agreement"] == 1.0
        assert mock_clf.classify.call_count == 5

    def test_run_skips_instrument_with_no_cached_features(self, tmp_path,
                                                          monkeypatch):
        db = tmp_path / "regime.db"
        self._seed_cache(db, "BARC")

        mock_clf = MagicMock()
        mock_clf.is_available.return_value = True
        from collections import namedtuple
        Stub = namedtuple("Stub", "raw_regime confidence")
        mock_clf.classify.return_value = Stub("TRENDING", 0.8)
        monkeypatch.setattr(ecc, "RegimeClassifier",
                            lambda cache, cost_tracker: mock_clf)

        results, _, err = ecc.run(["BARC", "MISSING"], n_calls=2, prod_db=db)
        assert err is None
        assert "error" in results["MISSING"]
        assert "error" not in results["BARC"]

    def test_run_aborts_when_budget_exceeded(self, tmp_path, monkeypatch):
        db = tmp_path / "regime.db"
        self._seed_cache(db, "BARC")

        # Make CostTracker.is_budget_exceeded return True
        mock_ct = MagicMock()
        mock_ct.is_budget_exceeded.return_value = True
        monkeypatch.setattr(ecc, "CostTracker", lambda _: mock_ct)

        results, cost_info, err = ecc.run(["BARC"], n_calls=5, prod_db=db)
        assert "budget" in err.lower()
        assert results == {}
