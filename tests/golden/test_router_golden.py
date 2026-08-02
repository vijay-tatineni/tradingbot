"""
Golden-file tests for router — §16.3.

Every row of the §9.8 routing table has a fixture.
"""
import json
import pytest
from datetime import datetime, timezone
from pathlib import Path

from bot.regime.models import SmoothedRegimeState
from bot.regime.router import route

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "router"


def _load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


def _smoothed(regime):
    return SmoothedRegimeState(
        instrument="BARC.L",
        effective_regime=regime,
        source_regime=regime,
        days_in_regime=5,
        last_changed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        confidence=0.9,
        pending_regime=None,
        pending_days=0,
    )


class TestRouterGolden:
    @pytest.fixture
    def fixture_data(self):
        return _load_fixture("all_rows.json")

    @pytest.mark.parametrize("case_idx", range(7))
    def test_routing_table_row(self, fixture_data, case_idx):
        case = fixture_data["test_cases"][case_idx]
        smoothed = _smoothed(case["regime"])
        overlays = case["overlays"]
        flags = case["flags"]

        result = route(smoothed, overlays, flags)

        assert result.selected_engine == case["expected_engine"], (
            f"Case '{case['name']}': expected engine {case['expected_engine']}, "
            f"got {result.selected_engine}"
        )
        assert result.allow_new_entries == case["expected_allow"], (
            f"Case '{case['name']}': expected allow={case['expected_allow']}, "
            f"got {result.allow_new_entries}"
        )

        if "expected_block_reason" in case and case["expected_block_reason"] is not None:
            assert result.block_reason == case["expected_block_reason"], (
                f"Case '{case['name']}': expected block_reason='{case['expected_block_reason']}', "
                f"got '{result.block_reason}'"
            )
        elif "expected_block_reason_contains" in case:
            assert case["expected_block_reason_contains"] in result.block_reason, (
                f"Case '{case['name']}': block_reason should contain "
                f"'{case['expected_block_reason_contains']}', got '{result.block_reason}'"
            )
