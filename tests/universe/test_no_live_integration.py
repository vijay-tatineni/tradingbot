"""Regression / safety: the shadow foundation is NON-INTEGRATED, so the live
strategy path is provably unchanged.

  * No live module imports bot.universe (structural proof of zero coupling).
  * Adding enable_dynamic_universe_shadow leaves all pre-existing flags' resolution
    byte-identical and adds no dependency.
  * The universe package imports no broker/data-provider module.
"""
import pathlib

from bot.regime.flags import DEPENDENCIES, FeatureFlags, KNOWN_FLAGS, SAFE_DEFAULTS

REPO = pathlib.Path(__file__).resolve().parents[2]

# Pre-existing flag defaults (the 14 flags that existed before the shadow foundation).
PREEXISTING_DEFAULTS = {
    "enable_classifier_shadow": True, "enable_classifier_live": False,
    "enable_persistence_shadow": True, "enable_persistence_live": False,
    "enable_router_shadow": True, "enable_router_live": False,
    "enable_event_overlays_shadow": True, "enable_event_overlays_live": False,
    "enable_mean_reversion_shadow": True, "enable_mean_reversion_live": False,
    "enable_position_tagged_exit_policy": False, "enable_calendar_ui": False,
    "data_quality_strict_mode": False, "enable_regime_filter_live": False,
}


def _live_source_files():
    files = [REPO / "main.py", REPO / "api_server.py"]
    files += [p for p in (REPO / "bot").glob("*.py")]
    files += list((REPO / "bot" / "plugins").glob("*.py"))
    files += list((REPO / "bot" / "regime").glob("*.py"))
    return [p for p in files if p.exists()]


def test_no_live_module_imports_bot_universe():
    offenders = []
    for p in _live_source_files():
        text = p.read_text()
        if "bot.universe" in text or "from bot import universe" in text:
            offenders.append(str(p.relative_to(REPO)))
    assert offenders == [], f"live modules import bot.universe: {offenders}"


def test_new_flag_registered_default_false():
    assert "enable_dynamic_universe_shadow" in KNOWN_FLAGS
    assert SAFE_DEFAULTS["enable_dynamic_universe_shadow"] is False
    assert FeatureFlags({}).get("enable_dynamic_universe_shadow") is False


def test_preexisting_flag_resolution_unchanged():
    # With an empty config, every pre-existing flag resolves to its documented default.
    flags = FeatureFlags({})
    for name, expected in PREEXISTING_DEFAULTS.items():
        assert flags.get(name) is expected
    # Enabling the new flag does not perturb any pre-existing flag.
    flags2 = FeatureFlags({"enable_dynamic_universe_shadow": True,
                           "enable_classifier_shadow": True})
    for name, expected in PREEXISTING_DEFAULTS.items():
        assert flags2.get(name) is expected


def test_new_flag_has_no_dependency():
    # It must not be wired into the regime dependency graph.
    assert "enable_dynamic_universe_shadow" not in DEPENDENCIES
    for parents in DEPENDENCIES.values():
        assert "enable_dynamic_universe_shadow" not in parents


def test_universe_package_imports_no_broker():
    forbidden = ("bot.brokers", "ib_insync", "trading_ig", "bot.connection")
    offenders = []
    for p in (REPO / "bot" / "universe").glob("*.py"):
        text = p.read_text()
        for f in forbidden:
            if f in text:
                offenders.append(f"{p.name}:{f}")
    assert offenders == [], f"universe package references broker modules: {offenders}"
