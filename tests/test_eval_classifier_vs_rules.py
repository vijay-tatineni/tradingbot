"""Tests for scripts/eval_classifier_vs_rules.

Pure-analysis script — no live API, no real DB. Tests target the rule
function, the comparison aggregator, and the report renderer.
"""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_DIR / "scripts" / "eval_classifier_vs_rules.py"

spec = importlib.util.spec_from_file_location("evr", SCRIPT_PATH)
evr = importlib.util.module_from_spec(spec)
sys.modules["evr"] = evr
spec.loader.exec_module(evr)


class TestClassifyByRules:
    def test_trending_when_both_thresholds_pass(self):
        assert evr.classify_by_rules(
            {"adx_14": 26.0, "range_efficiency": 0.6}) == "TRENDING"

    def test_ranging_when_both_thresholds_pass(self):
        assert evr.classify_by_rules(
            {"adx_14": 19.5, "range_efficiency": 0.25}) == "RANGING"

    def test_unclear_when_only_one_trending_threshold_passes(self):
        # ADX high, range eff low → mixed signal → UNCLEAR
        assert evr.classify_by_rules(
            {"adx_14": 30.0, "range_efficiency": 0.4}) == "UNCLEAR"
        # Range eff high, ADX low → mixed → UNCLEAR
        assert evr.classify_by_rules(
            {"adx_14": 18.0, "range_efficiency": 0.6}) == "UNCLEAR"

    def test_unclear_in_dead_zone(self):
        # ADX 20-25, range eff 0.3-0.5 → middle ground → UNCLEAR
        assert evr.classify_by_rules(
            {"adx_14": 22.0, "range_efficiency": 0.4}) == "UNCLEAR"

    def test_boundary_values_fall_through_to_unclear(self):
        # Strictly > / strictly < — exact thresholds don't fire.
        assert evr.classify_by_rules(
            {"adx_14": 25.0, "range_efficiency": 0.6}) == "UNCLEAR"
        assert evr.classify_by_rules(
            {"adx_14": 26.0, "range_efficiency": 0.5}) == "UNCLEAR"
        assert evr.classify_by_rules(
            {"adx_14": 20.0, "range_efficiency": 0.25}) == "UNCLEAR"

    def test_missing_keys_return_unclear(self):
        assert evr.classify_by_rules({}) == "UNCLEAR"
        assert evr.classify_by_rules(None) == "UNCLEAR"
        assert evr.classify_by_rules(
            {"adx_14": 30.0}) == "UNCLEAR"
        assert evr.classify_by_rules(
            {"range_efficiency": 0.6}) == "UNCLEAR"
        # Explicit None values also yield UNCLEAR
        assert evr.classify_by_rules(
            {"adx_14": None, "range_efficiency": 0.6}) == "UNCLEAR"


class TestLoadClassifications:
    def _build_db(self, tmp_path, rows):
        db = tmp_path / "regime.db"
        conn = sqlite3.connect(str(db))
        conn.execute("""CREATE TABLE regime_classification_cache(
            instrument TEXT, trading_date TEXT,
            input_hash TEXT, classification_json TEXT, created_at TEXT,
            PRIMARY KEY (instrument, trading_date, input_hash))""")
        conn.executemany(
            "INSERT INTO regime_classification_cache VALUES (?,?,?,?,?)",
            rows,
        )
        conn.commit()
        conn.close()
        return db

    def test_returns_rows_sorted_by_date(self, tmp_path):
        db = self._build_db(tmp_path, [
            ("BARC", "2026-05-21", "h1",
             json.dumps({"raw_regime": "RANGING", "confidence": 0.7,
                         "features": {"adx_14": 19, "range_efficiency": 0.2}}),
             "t"),
            ("BARC", "2026-05-19", "h2",
             json.dumps({"raw_regime": "TRENDING", "confidence": 0.8,
                         "features": {"adx_14": 28, "range_efficiency": 0.55}}),
             "t"),
        ])
        rows = evr.load_classifications(db)
        assert len(rows) == 2
        # Sorted ascending by trading_date
        assert rows[0]["trading_date"] == "2026-05-19"
        assert rows[0]["raw_regime"] == "TRENDING"
        assert rows[1]["features"]["adx_14"] == 19

    def test_missing_db_returns_empty(self, tmp_path):
        assert evr.load_classifications(tmp_path / "no.db") == []

    def test_invalid_json_blob_skipped(self, tmp_path):
        db = self._build_db(tmp_path, [
            ("BARC", "2026-05-21", "h1", "not-json-at-all", "t"),
            ("BARC", "2026-05-22", "h2",
             json.dumps({"raw_regime": "UNCLEAR", "features": {}}), "t"),
        ])
        rows = evr.load_classifications(db)
        assert len(rows) == 1


class TestBuildComparison:
    def _row(self, claude, features, inst="BARC", day="2026-05-29"):
        return {
            "instrument": inst,
            "trading_date": day,
            "raw_regime": claude,
            "confidence": 0.7,
            "features": features,
        }

    def test_perfect_agreement(self):
        rows = [
            self._row("TRENDING", {"adx_14": 28, "range_efficiency": 0.6}),
            self._row("RANGING", {"adx_14": 19, "range_efficiency": 0.2}),
            self._row("UNCLEAR", {"adx_14": 22, "range_efficiency": 0.4}),
        ]
        c = evr.build_comparison(rows)
        assert c["agreement_rate"] == 1.0
        assert c["n_agree"] == 3
        assert c["disagreements"] == []
        # Diagonal == row count
        for r in evr.REGIMES:
            row_total = sum(c["confusion"][r].values())
            assert c["confusion"][r][r] == row_total

    def test_one_disagreement_captured_in_list(self):
        rows = [
            # Rules say TRENDING (ADX 28, eff 0.6) but Claude says UNCLEAR
            self._row("UNCLEAR", {"adx_14": 28, "range_efficiency": 0.6},
                      inst="MSFT", day="2026-05-23"),
        ]
        c = evr.build_comparison(rows)
        assert c["agreement_rate"] == 0.0
        assert c["confusion"]["TRENDING"]["UNCLEAR"] == 1
        assert len(c["disagreements"]) == 1
        d = c["disagreements"][0]
        assert d["instrument"] == "MSFT"
        assert d["rules_regime"] == "TRENDING"
        assert d["claude_regime"] == "UNCLEAR"
        assert d["adx_14"] == 28

    def test_invalid_claude_regime_skipped(self):
        rows = [
            self._row(None, {"adx_14": 28, "range_efficiency": 0.6}),
            self._row("BANANAS", {"adx_14": 28, "range_efficiency": 0.6}),
            self._row("TRENDING", {"adx_14": 28, "range_efficiency": 0.6}),
        ]
        c = evr.build_comparison(rows)
        assert c["n_total"] == 3
        assert c["n_valid"] == 1

    def test_confusion_matrix_sums_to_n_valid(self):
        rows = [
            self._row("TRENDING", {"adx_14": 28, "range_efficiency": 0.6}),
            self._row("RANGING", {"adx_14": 28, "range_efficiency": 0.6}),
            self._row("UNCLEAR", {"adx_14": 19, "range_efficiency": 0.2}),
            self._row("TRENDING", {"adx_14": 22, "range_efficiency": 0.4}),
        ]
        c = evr.build_comparison(rows)
        total = sum(c["confusion"][r][col]
                    for r in evr.REGIMES for col in evr.REGIMES)
        assert total == c["n_valid"]


class TestRenderReport:
    def test_renders_full_table(self):
        rows = [
            {"instrument": "BARC", "trading_date": "2026-05-29",
             "raw_regime": "TRENDING", "confidence": 0.8,
             "features": {"adx_14": 28.0, "range_efficiency": 0.6,
                          "ma_200_slope_pct_per_day": 0.1, "atr_pct": 1.2}},
            {"instrument": "ANTO", "trading_date": "2026-05-29",
             "raw_regime": "UNCLEAR", "confidence": 0.6,
             "features": {"adx_14": 28.0, "range_efficiency": 0.6,
                          "ma_200_slope_pct_per_day": 0.0, "atr_pct": 2.0}},
        ]
        c = evr.build_comparison(rows)
        md = evr.render_report(c, "2026-06-01T00:00:00Z")
        assert "Confusion matrix" in md
        assert "Disagreements" in md
        assert "ANTO" in md  # the disagreement row
        # Agreement is 1/2 = 50%
        assert "50.0%" in md

    def test_no_disagreements_renders_clean_message(self):
        rows = [
            {"instrument": "BARC", "trading_date": "2026-05-29",
             "raw_regime": "TRENDING", "confidence": 0.8,
             "features": {"adx_14": 28.0, "range_efficiency": 0.6}},
        ]
        c = evr.build_comparison(rows)
        md = evr.render_report(c, "2026-06-01T00:00:00Z")
        assert "agreed on every evaluable row" in md
