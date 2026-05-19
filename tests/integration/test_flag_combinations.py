"""
§6.3: Flag dependency graph — systematic valid/invalid combination tests.

Validates that every flag combination that violates the dependency
graph is rejected, and that valid chains are accepted.
"""
import pytest

from bot.regime.flags import FeatureFlags, ConfigError


FULL_CHAIN = {
    "enable_classifier_shadow": True,
    "enable_classifier_live": True,
    "enable_persistence_shadow": True,
    "enable_persistence_live": True,
    "enable_router_shadow": True,
    "enable_router_live": True,
    "enable_mean_reversion_shadow": True,
    "enable_mean_reversion_live": True,
    "enable_event_overlays_shadow": True,
    "enable_event_overlays_live": True,
}


def test_full_chain_valid():
    flags = FeatureFlags(FULL_CHAIN)
    assert flags.enable_mean_reversion_live is True
    assert flags.enable_event_overlays_live is True


def test_shadow_only_valid():
    flags = FeatureFlags({})
    for key in flags.as_dict():
        if key.endswith("_live"):
            assert flags.get(key) is False


def test_overlays_independent_of_router():
    """§6.2: overlays_live does NOT require router_live."""
    flags = FeatureFlags({
        "enable_event_overlays_shadow": True,
        "enable_event_overlays_live": True,
        "enable_router_live": False,
    })
    assert flags.enable_event_overlays_live is True
    assert flags.enable_router_live is False


def test_router_without_persistence_fails():
    with pytest.raises(ConfigError, match="requires"):
        FeatureFlags({
            "enable_classifier_shadow": True,
            "enable_classifier_live": True,
            "enable_persistence_shadow": True,
            "enable_persistence_live": False,
            "enable_router_shadow": True,
            "enable_router_live": True,
        })


def test_persistence_without_classifier_fails():
    with pytest.raises(ConfigError, match="requires"):
        FeatureFlags({
            "enable_classifier_shadow": True,
            "enable_classifier_live": False,
            "enable_persistence_shadow": True,
            "enable_persistence_live": True,
        })


def test_mean_reversion_without_router_fails():
    with pytest.raises(ConfigError, match="requires"):
        FeatureFlags({
            "enable_mean_reversion_live": True,
            "enable_router_live": False,
        })


def test_overlays_live_without_shadow_fails():
    with pytest.raises(ConfigError, match="requires"):
        FeatureFlags({
            "enable_event_overlays_live": True,
            "enable_event_overlays_shadow": False,
        })


def test_classifier_live_without_shadow_fails():
    with pytest.raises(ConfigError, match="requires"):
        FeatureFlags({
            "enable_classifier_live": True,
            "enable_classifier_shadow": False,
        })


def test_partial_chain_classifier_only():
    flags = FeatureFlags({
        "enable_classifier_shadow": True,
        "enable_classifier_live": True,
    })
    assert flags.enable_classifier_live is True
    assert flags.enable_persistence_live is False


def test_partial_chain_up_to_persistence():
    flags = FeatureFlags({
        "enable_classifier_shadow": True,
        "enable_classifier_live": True,
        "enable_persistence_shadow": True,
        "enable_persistence_live": True,
    })
    assert flags.enable_persistence_live is True
    assert flags.enable_router_live is False


def test_startup_summary_shadow_mode():
    flags = FeatureFlags({})
    summary = flags.startup_summary()
    assert "SHADOW" in summary
    assert "no live routing" in summary


def test_startup_summary_live_mode():
    flags = FeatureFlags({
        "enable_classifier_shadow": True,
        "enable_classifier_live": True,
    })
    summary = flags.startup_summary()
    assert "LIVE" in summary
