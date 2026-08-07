"""Tests for scripts/eval_classifier_deep_dive.

Pure-aggregation tests. The forward-metric attachment path itself is
covered by tests/test_eval_classifier_forward_returns.py — here we
hand-build sample dicts and verify each of the three sections.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_DIR / "scripts" / "eval_classifier_deep_dive.py"

spec = importlib.util.spec_from_file_location("edd", SCRIPT_PATH)
edd = importlib.util.module_from_spec(spec)
sys.modules["edd"] = edd
spec.loader.exec_module(edd)


def _s(regime, dir_atr, conf=0.7, inst="BARC", abs_atr=None):
    return {
        "instrument": inst,
        "trading_date": "2026-03-01",
        "raw_regime": regime,
        "confidence": conf,
        "directional_move_atr": dir_atr,
        "abs_move_atr": abs(dir_atr) if abs_atr is None else abs_atr,
        "forward_range_efficiency": 0.4,
    }


class TestSection1TrendingVsUnclear:
    def test_zero_samples_returns_none_buckets(self):
        out = edd.section1_trending_vs_unclear([])
        assert out["n_trending"] == 0
        assert out["n_unclear"] == 0
        assert out["mean_directional_trending"] is None
        assert out["cohens_d_directional"] is None

    def test_only_trending_returns_partial(self):
        samples = [_s("TRENDING", 1.0), _s("TRENDING", 2.0)]
        out = edd.section1_trending_vs_unclear(samples)
        assert out["n_trending"] == 2
        assert out["n_unclear"] == 0
        assert out["mean_directional_unclear"] is None
        assert out["cohens_d_directional"] is None

    def test_compare_clean_separation(self):
        # TRENDING clearly above UNCLEAR
        samples = (
            [_s("TRENDING", 2.0), _s("TRENDING", 2.5),
             _s("TRENDING", 3.0), _s("TRENDING", 2.5)]
            + [_s("UNCLEAR", -0.1), _s("UNCLEAR", 0.0),
               _s("UNCLEAR", 0.1), _s("UNCLEAR", -0.05)]
        )
        out = edd.section1_trending_vs_unclear(samples)
        assert out["mean_directional_trending"] > 0
        assert abs(out["mean_directional_unclear"]) < 0.1
        # large positive d
        assert out["cohens_d_directional"] > 1.0

    def test_excludes_ranging_samples(self):
        samples = [
            _s("TRENDING", 1.0), _s("TRENDING", 2.0),
            _s("UNCLEAR", 0.0), _s("UNCLEAR", 0.0),
            _s("RANGING", 5.0), _s("RANGING", 5.0),  # ignored
        ]
        out = edd.section1_trending_vs_unclear(samples)
        assert out["n_trending"] == 2
        assert out["n_unclear"] == 2  # RANGING not counted


class TestSection2ConfidenceSplits:
    def test_threshold_inclusive_at_0_75(self):
        # confidence == threshold should land in "high"
        samples = [
            _s("TRENDING", 1.0, conf=0.75),
            _s("TRENDING", 0.5, conf=0.74),
        ]
        out = edd.section2_confidence_splits(samples)
        assert out["TRENDING"]["n_high"] == 1
        assert out["TRENDING"]["n_low"] == 1

    def test_empty_bucket_renders_zeros(self):
        out = edd.section2_confidence_splits([])
        for r in edd.REGIMES:
            assert out[r]["n_high"] == 0
            assert out[r]["n_low"] == 0
            assert out[r]["mean_directional_high"] is None
            assert out[r]["mean_directional_low"] is None

    def test_only_high_conf_gives_none_d(self):
        samples = [_s("TRENDING", 1.0, conf=0.9)] * 3
        out = edd.section2_confidence_splits(samples)
        assert out["TRENDING"]["n_high"] == 3
        assert out["TRENDING"]["n_low"] == 0
        assert out["TRENDING"]["cohens_d_directional"] is None

    def test_high_outperforms_low_yields_positive_d(self):
        samples = (
            [_s("TRENDING", 3.0, conf=0.9),
             _s("TRENDING", 2.5, conf=0.85),
             _s("TRENDING", 2.7, conf=0.92)]
            + [_s("TRENDING", 0.2, conf=0.6),
               _s("TRENDING", 0.1, conf=0.55),
               _s("TRENDING", 0.0, conf=0.5)]
        )
        out = edd.section2_confidence_splits(samples)
        assert out["TRENDING"]["mean_directional_high"] > \
            out["TRENDING"]["mean_directional_low"]
        assert out["TRENDING"]["cohens_d_directional"] > 1.0

    def test_confidence_none_is_skipped(self):
        samples = [
            _s("TRENDING", 1.0, conf=None),
            _s("TRENDING", 2.0, conf=0.8),
        ]
        out = edd.section2_confidence_splits(samples)
        # Sample with None confidence excluded
        assert out["TRENDING"]["n_high"] == 1
        assert out["TRENDING"]["n_low"] == 0


class TestSection3PerInstrument:
    def test_counts_per_instrument(self):
        samples = [
            _s("TRENDING", 1.0, inst="BARC"),
            _s("RANGING", 0.5, inst="BARC"),
            _s("UNCLEAR", 0.0, inst="BARC"),
            _s("TRENDING", 2.0, inst="AAPL"),
        ]
        out, _ = edd.section3_per_instrument(samples, consistency={})
        assert out["BARC"]["counts"] == {"TRENDING": 1, "RANGING": 1,
                                          "UNCLEAR": 1}
        assert out["AAPL"]["counts"] == {"TRENDING": 1}

    def test_mean_only_at_or_above_min_n(self):
        # 2 TRENDING for BARC → mean stays None (below min_n=3)
        # 4 RANGING for BARC → mean computed
        samples = [
            _s("TRENDING", 1.0, inst="BARC"),
            _s("TRENDING", 2.0, inst="BARC"),
            _s("RANGING", 0.5, inst="BARC"),
            _s("RANGING", 0.4, inst="BARC"),
            _s("RANGING", 0.6, inst="BARC"),
            _s("RANGING", 0.7, inst="BARC"),
        ]
        out, _ = edd.section3_per_instrument(samples, consistency={})
        assert out["BARC"]["per_regime"]["TRENDING"]["mean_directional"] is None
        assert out["BARC"]["per_regime"]["RANGING"]["mean_directional"] \
            == pytest.approx(0.55)

    def test_flag_fires_when_above_threshold(self):
        # ANTO: 3 TRENDING with mean |move| = 4.0
        # AAPL: 5 TRENDING with mean |move| = 1.0 (aggregate baseline)
        # Aggregate mean |move| for TRENDING = (3*4 + 5*1) / 8 = 17/8 = 2.125
        # ANTO/TRENDING mean |move| = 4.0 → 4.0 / 2.125 = 1.88× → flag (>=1.5)
        samples = (
            [_s("TRENDING", 4.0, inst="ANTO")] * 3
            + [_s("TRENDING", 1.0, inst="AAPL")] * 5
        )
        out, agg = edd.section3_per_instrument(samples, consistency={})
        assert agg["TRENDING"] == pytest.approx(17 / 8)
        assert any("TRENDING" in f for f in out["ANTO"]["flags"])
        assert out["AAPL"]["flags"] == []

    def test_flag_skipped_if_below_min_n(self):
        # ANTO has only 2 TRENDING samples → no flag despite high |move|
        samples = (
            [_s("TRENDING", 4.0, inst="ANTO")] * 2
            + [_s("TRENDING", 1.0, inst="AAPL")] * 5
        )
        out, _ = edd.section3_per_instrument(samples, consistency={})
        assert out["ANTO"]["flags"] == []
        assert out["ANTO"]["per_regime"]["TRENDING"]["mean_directional"] is None

    def test_consistency_merged_in(self):
        samples = [_s("TRENDING", 1.0, inst="BARC")]
        consistency = {"BARC": {"modal_label": "RANGING",
                                "modal_agreement": 0.9,
                                "n_calls": 10}}
        out, _ = edd.section3_per_instrument(samples, consistency)
        assert out["BARC"]["modal_label"] == "RANGING"
        assert out["BARC"]["modal_agreement"] == 0.9

    def test_zero_aggregate_doesnt_divide_by_zero(self):
        # All abs moves 0 → agg_abs[TRENDING] = 0; flag never fires
        samples = [_s("TRENDING", 0.0, inst="BARC", abs_atr=0)] * 4
        out, agg = edd.section3_per_instrument(samples, consistency={})
        assert agg["TRENDING"] == 0
        assert out["BARC"]["flags"] == []


class TestLogLoaders:
    def _seed(self, db_path, rows):
        import sqlite3
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("""CREATE TABLE regime_classification_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, instrument TEXT, trading_date TEXT, model TEXT,
                prompt_version TEXT, input_tokens INTEGER,
                output_tokens INTEGER, cost_usd REAL, latency_ms INTEGER,
                raw_regime TEXT, confidence REAL, cache_hit INTEGER,
                error TEXT)""")
            for r in rows:
                conn.execute(
                    "INSERT INTO regime_classification_log "
                    "(ts, instrument, trading_date, model, prompt_version, "
                    "input_tokens, output_tokens, cost_usd, latency_ms, "
                    "raw_regime, confidence, cache_hit, error) "
                    "VALUES (?,?,?,'m','V1',0,0,0.01,0,?,?,?,?)",
                    r,
                )

    def test_backfill_excludes_today_trading_date(self, tmp_path):
        db = tmp_path / "regime.db"
        self._seed(db, [
            # (ts, instrument, trading_date, raw_regime, conf, cache_hit, error)
            ("2026-06-01T10:00:00", "BARC", "2026-03-15", "TRENDING",
             0.8, 0, None),
            ("2026-06-01T10:01:00", "BARC", "2026-06-01", "RANGING",
             0.7, 0, None),  # consistency row — excluded
            ("2026-06-01T10:02:00", "BARC", "2026-04-01", "UNCLEAR",
             0.6, 1, None),  # cache hit — excluded
            ("2026-06-01T10:03:00", "BARC", "2026-04-05", "TRENDING",
             0.8, 0, "api_failure"),  # error — excluded
            ("2026-05-31T10:00:00", "BARC", "2026-03-15", "TRENDING",
             0.8, 0, None),  # yesterday — excluded
        ])
        rows = edd.load_backfill_meta(db, "2026-06-01")
        assert len(rows) == 1
        assert rows[0]["raw_regime"] == "TRENDING"
        assert rows[0]["trading_date"] == "2026-03-15"

    def test_consistency_includes_today_trading_date_only(self, tmp_path):
        db = tmp_path / "regime.db"
        self._seed(db, [
            ("2026-06-01T10:00:00", "BARC", "2026-06-01", "RANGING",
             0.7, 0, None),
            ("2026-06-01T10:01:00", "BARC", "2026-06-01", "RANGING",
             0.7, 0, None),
            ("2026-06-01T10:02:00", "BARC", "2026-06-01", "UNCLEAR",
             0.6, 0, None),
            ("2026-06-01T10:03:00", "BARC", "2026-03-15", "TRENDING",
             0.8, 0, None),  # backfill — excluded
        ])
        out = edd.load_consistency_meta(db, "2026-06-01")
        assert out["BARC"]["n_calls"] == 3
        assert out["BARC"]["modal_label"] == "RANGING"
        assert out["BARC"]["modal_agreement"] == pytest.approx(2 / 3)


class TestRenderReport:
    def test_zero_samples_renders_insufficient(self):
        s1 = edd.section1_trending_vs_unclear([])
        s2 = edd.section2_confidence_splits([])
        s3, agg = edd.section3_per_instrument([], consistency={})
        md = edd.render_report(s1, s2, s3, agg, "2026-06-01T00:00:00Z",
                               n_samples=0)
        assert "Insufficient samples" in md
        assert "insufficient data" in md

    def test_includes_section_headings(self):
        s1 = edd.section1_trending_vs_unclear([])
        s2 = edd.section2_confidence_splits([])
        s3, agg = edd.section3_per_instrument([], consistency={})
        md = edd.render_report(s1, s2, s3, agg, "now", n_samples=0)
        assert "Section 1" in md
        assert "Section 2" in md
        assert "Section 3" in md
