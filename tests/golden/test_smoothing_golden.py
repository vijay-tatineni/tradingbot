"""
Golden-file tests for smoothing — §16.3.

Fixed sequence of RegimeClassification inputs → fixed SmoothedRegimeState outputs.
Covers persistence, hysteresis, fallback retention, first-run promotion.
"""
import json
import pytest
from datetime import datetime, timezone
from pathlib import Path

from bot.regime.models import RegimeClassification, SmoothedRegimeState
from bot.regime.smoothing import initial_state, update, update_first_run

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "smoothing"


def _load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


def _make_classification(regime, confidence, instrument="BARC.L"):
    return RegimeClassification(
        instrument=instrument,
        classified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        trading_date="2026-01-01",
        raw_regime=regime,
        confidence=confidence,
        rationale="Golden test",
        features={},
        model_version="test",
        prompt_version="V1",
        input_hash="golden",
    )


def _make_state(regime, days, instrument="BARC.L", pending=None, pending_days=0):
    return SmoothedRegimeState(
        instrument=instrument,
        effective_regime=regime,
        source_regime=regime,
        days_in_regime=days,
        last_changed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        confidence=0.9,
        pending_regime=pending,
        pending_days=pending_days,
    )


class TestSmoothingGolden:
    def test_persistence_sequence(self):
        fixture = _load_fixture("persistence_sequence.json")
        state = initial_state("BARC.L")

        for i, (cls_data, expected) in enumerate(zip(
            fixture["classifications"], fixture["expected_states"]
        )):
            c = _make_classification(cls_data["raw_regime"], cls_data["confidence"])
            if i == 0 and state.days_in_regime == 0:
                state = update_first_run(state, c)
            else:
                state = update(state, c)

            assert state.effective_regime == expected["effective_regime"], f"Step {i}"
            assert state.days_in_regime == expected["days_in_regime"], f"Step {i}"
            assert state.pending_regime == expected.get("pending_regime"), f"Step {i}"

    def test_pending_promotion(self):
        fixture = _load_fixture("pending_promotion.json")
        state = _make_state(
            fixture["initial_regime"],
            fixture["initial_days"],
        )

        for i, (cls_data, expected) in enumerate(zip(
            fixture["classifications"], fixture["expected_states"]
        )):
            c = _make_classification(cls_data["raw_regime"], cls_data["confidence"])
            state = update(state, c)

            assert state.effective_regime == expected["effective_regime"], f"Step {i}"
            assert state.days_in_regime == expected["days_in_regime"], f"Step {i}"
            assert state.pending_regime == expected.get("pending_regime"), f"Step {i}"
            if "pending_days" in expected:
                assert state.pending_days == expected["pending_days"], f"Step {i}"

    def test_hysteresis_block(self):
        fixture = _load_fixture("hysteresis_block.json")
        state = _make_state(fixture["initial_regime"], fixture["initial_days"])

        for i, (cls_data, expected) in enumerate(zip(
            fixture["classifications"], fixture["expected_states"]
        )):
            c = _make_classification(cls_data["raw_regime"], cls_data["confidence"])
            state = update(state, c)
            assert state.effective_regime == expected["effective_regime"], f"Step {i}"
            assert state.pending_regime == expected.get("pending_regime"), f"Step {i}"

    def test_fallback_retention(self):
        fixture = _load_fixture("fallback_retention.json")
        state = _make_state(fixture["initial_regime"], fixture["initial_days"])

        for i, (cls_data, expected) in enumerate(zip(
            fixture["classifications"], fixture["expected_states"]
        )):
            c = _make_classification(cls_data["raw_regime"], cls_data["confidence"])
            state = update(state, c)
            assert state.effective_regime == expected["effective_regime"], f"Step {i}"
            assert state.days_in_regime == expected["days_in_regime"], f"Step {i}"
