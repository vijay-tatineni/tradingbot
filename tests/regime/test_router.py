"""Unit tests for regime router — §9.8."""
import pytest
from datetime import datetime, timezone

from bot.regime.models import SmoothedRegimeState, RoutingDecision
from bot.regime.router import route


def _smoothed(regime="TRENDING", instrument="BARC.L"):
    return SmoothedRegimeState(
        instrument=instrument,
        effective_regime=regime,
        source_regime=regime,
        days_in_regime=5,
        last_changed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        confidence=0.9,
        pending_regime=None,
        pending_days=0,
    )


def _overlay(name="EARNINGS_LOCKOUT"):
    return {"overlay_name": name, "is_active": True,
            "expires_at": None, "reason": f"{name} active"}


class TestRouterTrending:
    def test_trending_no_overlays(self):
        r = route(_smoothed("TRENDING"), [], {})
        assert r.selected_engine == "TripleConfirmationEngine"
        assert r.allow_new_entries is True
        assert r.block_reason is None

    def test_trending_with_overlays(self):
        r = route(_smoothed("TRENDING"), [_overlay()], {})
        assert r.selected_engine == "TripleConfirmationEngine"
        assert r.allow_new_entries is False
        assert "Overlay active" in r.block_reason


class TestRouterRanging:
    def test_ranging_mean_reversion_live(self):
        r = route(_smoothed("RANGING"), [],
                  {"enable_mean_reversion_live": True})
        assert r.selected_engine == "MeanReversionEngine"
        assert r.allow_new_entries is True

    def test_ranging_mean_reversion_not_live(self):
        r = route(_smoothed("RANGING"), [],
                  {"enable_mean_reversion_live": False})
        assert r.selected_engine == "NoOpEngine"
        assert r.allow_new_entries is False
        assert "Mean-reversion not enabled live" in r.block_reason

    def test_ranging_with_overlays_mean_reversion_live(self):
        r = route(_smoothed("RANGING"), [_overlay()],
                  {"enable_mean_reversion_live": True})
        assert r.selected_engine == "MeanReversionEngine"
        assert r.allow_new_entries is False

    def test_ranging_with_overlays_mean_reversion_not_live(self):
        r = route(_smoothed("RANGING"), [_overlay()],
                  {"enable_mean_reversion_live": False})
        assert r.selected_engine == "NoOpEngine"
        assert r.allow_new_entries is False


class TestRouterUnclear:
    def test_unclear_no_overlays(self):
        r = route(_smoothed("UNCLEAR"), [], {})
        assert r.selected_engine == "NoOpEngine"
        assert r.allow_new_entries is False
        assert r.block_reason == "Regime UNCLEAR"

    def test_unclear_with_overlays(self):
        r = route(_smoothed("UNCLEAR"), [_overlay()], {})
        assert r.selected_engine == "NoOpEngine"
        assert r.allow_new_entries is False


class TestRouterInvariant:
    def test_noop_never_allows_entries(self):
        """§9.8 invariant: NoOpEngine ⇒ allow_new_entries == False."""
        test_cases = [
            (_smoothed("UNCLEAR"), [], {}),
            (_smoothed("RANGING"), [], {"enable_mean_reversion_live": False}),
            (_smoothed("RANGING"), [_overlay()], {"enable_mean_reversion_live": False}),
        ]
        for smoothed, overlays, flags in test_cases:
            r = route(smoothed, overlays, flags)
            if r.selected_engine == "NoOpEngine":
                assert r.allow_new_entries is False, (
                    f"NoOpEngine allowed entries for regime={smoothed.effective_regime}"
                )


class TestRouterMetadata:
    def test_overlay_names_captured(self):
        r = route(_smoothed("TRENDING"),
                  [_overlay("EARNINGS_LOCKOUT"), _overlay("MACRO_LOCKOUT")], {})
        assert "EARNINGS_LOCKOUT" in r.active_overlays
        assert "MACRO_LOCKOUT" in r.active_overlays

    def test_flag_snapshot_captured(self):
        flags = {"enable_mean_reversion_live": False, "enable_router_live": True}
        r = route(_smoothed("TRENDING"), [], flags)
        assert r.flag_snapshot == flags
