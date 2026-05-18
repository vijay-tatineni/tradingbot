"""Unit tests for feature flags — §6."""
import pytest

from bot.regime.flags import FeatureFlags, ConfigError, SAFE_DEFAULTS


class TestFlagDefaults:
    def test_empty_config_uses_safe_defaults(self):
        flags = FeatureFlags({})
        assert flags.enable_classifier_live is False
        assert flags.enable_classifier_shadow is True
        assert flags.enable_router_live is False
        assert flags.enable_calendar_ui is True

    def test_all_safe_defaults_loaded(self):
        flags = FeatureFlags({})
        for name, default in SAFE_DEFAULTS.items():
            assert flags.get(name) == default


class TestFlagValidation:
    def test_unknown_flag_raises(self):
        with pytest.raises(ConfigError, match="Unknown feature flag"):
            FeatureFlags({"enable_regime_block_no_trade": True})

    def test_typo_rejected(self):
        with pytest.raises(ConfigError, match="Unknown feature flag"):
            FeatureFlags({"enabel_classifier_live": True})


class TestDependencyGraph:
    def test_router_live_requires_classifier_live(self):
        with pytest.raises(ConfigError, match="requires"):
            FeatureFlags({
                "enable_router_live": True,
                "enable_classifier_live": False,
            })

    def test_router_live_requires_persistence_live(self):
        with pytest.raises(ConfigError, match="requires"):
            FeatureFlags({
                "enable_router_live": True,
                "enable_classifier_live": True,
                "enable_persistence_live": False,
            })

    def test_mean_reversion_live_requires_router_live(self):
        with pytest.raises(ConfigError, match="requires"):
            FeatureFlags({
                "enable_mean_reversion_live": True,
                "enable_router_live": False,
            })

    def test_overlays_live_requires_overlays_shadow(self):
        with pytest.raises(ConfigError, match="requires"):
            FeatureFlags({
                "enable_event_overlays_live": True,
                "enable_event_overlays_shadow": False,
            })

    def test_valid_full_chain(self):
        flags = FeatureFlags({
            "enable_classifier_shadow": True,
            "enable_classifier_live": True,
            "enable_persistence_shadow": True,
            "enable_persistence_live": True,
            "enable_router_shadow": True,
            "enable_router_live": True,
            "enable_mean_reversion_live": True,
        })
        assert flags.enable_router_live is True
        assert flags.enable_mean_reversion_live is True

    def test_calendar_ui_no_dependencies(self):
        flags = FeatureFlags({"enable_calendar_ui": True})
        assert flags.enable_calendar_ui is True


class TestStartupSummary:
    def test_summary_contains_flags(self):
        flags = FeatureFlags({})
        summary = flags.startup_summary()
        assert "classifier:" in summary
        assert "shadow=true" in summary
        assert "live=false" in summary
        assert "SHADOW" in summary

    def test_summary_shows_live_mode(self):
        flags = FeatureFlags({
            "enable_classifier_live": True,
            "enable_classifier_shadow": True,
        })
        summary = flags.startup_summary()
        assert "LIVE" in summary


class TestAccessPatterns:
    def test_get_method(self):
        flags = FeatureFlags({})
        assert flags.get("enable_classifier_shadow") is True

    def test_get_unknown_raises(self):
        flags = FeatureFlags({})
        with pytest.raises(ConfigError):
            flags.get("nonexistent_flag")

    def test_attribute_access(self):
        flags = FeatureFlags({})
        assert flags.enable_classifier_shadow is True

    def test_as_dict(self):
        flags = FeatureFlags({})
        d = flags.as_dict()
        assert isinstance(d, dict)
        assert "enable_classifier_shadow" in d
