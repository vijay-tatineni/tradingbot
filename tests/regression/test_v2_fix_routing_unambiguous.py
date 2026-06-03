"""
Regression: v2 rewrote routing table to be unambiguous.

Bug: v1 routing table had ambiguous rows for RANGING with overlays.
Fix: v2 made every combination explicit. Specifically, RANGING + no overlays +
     mean_reversion_live=False must produce NoOpEngine + allow=False.
Spec: §9.8, v2 changelog.
"""
from datetime import datetime, timezone

from bot.regime.models import SmoothedRegimeState
from bot.regime.router import route


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


def test_ranging_no_overlays_mr_not_live_is_noop():
    """The specific v2 fix: RANGING without mean-reversion must use NoOp."""
    r = route(_smoothed("RANGING"), [], {"enable_mean_reversion_live": False})
    assert r.selected_engine == "NoOpEngine"
    assert r.allow_new_entries is False
    assert "Mean-reversion not enabled live" in r.block_reason


def test_every_routing_row_produces_expected():
    """Verify all 7 rows of the §9.8 table."""
    cases = [
        ("TRENDING", [], {}, "TripleConfirmationEngine", True),
        ("TRENDING", [{"overlay_name": "E"}], {}, "TripleConfirmationEngine", False),
        ("RANGING", [], {"enable_mean_reversion_live": True}, "MeanReversionEngine", True),
        ("RANGING", [], {"enable_mean_reversion_live": False}, "NoOpEngine", False),
        ("RANGING", [{"overlay_name": "E"}], {"enable_mean_reversion_live": True},
         "MeanReversionEngine", False),
        ("RANGING", [{"overlay_name": "E"}], {"enable_mean_reversion_live": False},
         "NoOpEngine", False),
        ("UNCLEAR", [], {}, "NoOpEngine", False),
    ]
    for regime, overlays, flags, exp_engine, exp_allow in cases:
        r = route(_smoothed(regime), overlays, flags)
        assert r.selected_engine == exp_engine, f"Failed for {regime}, overlays={overlays}"
        assert r.allow_new_entries == exp_allow, f"Failed for {regime}, overlays={overlays}"
