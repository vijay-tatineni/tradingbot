"""
Invariant: selected_engine == "NoOpEngine" ⇒ allow_new_entries == False.

§9.8 routing invariant. Pinned test — if this fails, the router is broken.
"""
import pytest
from datetime import datetime, timezone

from bot.regime.models import SmoothedRegimeState
from bot.regime.router import route


def _smoothed(regime):
    return SmoothedRegimeState(
        instrument="TEST",
        effective_regime=regime,
        source_regime=regime,
        days_in_regime=5,
        last_changed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        confidence=0.9,
        pending_regime=None,
        pending_days=0,
    )


def _overlay(name="TEST_OVERLAY"):
    return {"overlay_name": name, "is_active": True,
            "expires_at": None, "reason": "test"}


@pytest.mark.parametrize("regime,overlays,flags", [
    ("UNCLEAR", [], {}),
    ("UNCLEAR", [_overlay()], {}),
    ("UNCLEAR", [], {"enable_mean_reversion_live": True}),
    ("RANGING", [], {"enable_mean_reversion_live": False}),
    ("RANGING", [_overlay()], {"enable_mean_reversion_live": False}),
])
def test_noop_never_allows_new_entries(regime, overlays, flags):
    """For every input that produces NoOpEngine, allow_new_entries must be False."""
    result = route(_smoothed(regime), overlays, flags)
    if result.selected_engine == "NoOpEngine":
        assert result.allow_new_entries is False, (
            f"INVARIANT VIOLATED: NoOpEngine selected for regime={regime} "
            f"but allow_new_entries={result.allow_new_entries}"
        )
