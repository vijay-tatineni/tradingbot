"""
Feature flags — §6 of CLAUDE_STRATEGY_SPEC_v3.

All flags default to off or shadow. Missing config keys never enable live behaviour.
Config loader raises on unknown flag names (typo protection).
"""
import logging
from typing import Any

logger = logging.getLogger("regime.flags")

KNOWN_FLAGS = {
    "enable_classifier_shadow",
    "enable_classifier_live",
    "enable_persistence_shadow",
    "enable_persistence_live",
    "enable_router_shadow",
    "enable_router_live",
    "enable_event_overlays_shadow",
    "enable_event_overlays_live",
    "enable_mean_reversion_shadow",
    "enable_mean_reversion_live",
    "enable_position_tagged_exit_policy",
    "enable_calendar_ui",
    "data_quality_strict_mode",
}

SAFE_DEFAULTS = {
    "enable_classifier_shadow": True,
    "enable_classifier_live": False,
    "enable_persistence_shadow": True,
    "enable_persistence_live": False,
    "enable_router_shadow": True,
    "enable_router_live": False,
    "enable_event_overlays_shadow": True,
    "enable_event_overlays_live": False,
    "enable_mean_reversion_shadow": True,
    "enable_mean_reversion_live": False,
    "enable_position_tagged_exit_policy": False,
    "enable_calendar_ui": True,
    "data_quality_strict_mode": False,
}

# §6.3: dependency graph (child → list of parents)
DEPENDENCIES = {
    "enable_classifier_live": ["enable_classifier_shadow"],
    "enable_persistence_live": ["enable_persistence_shadow", "enable_classifier_live"],
    "enable_router_live": [
        "enable_router_shadow",
        "enable_persistence_live",
        "enable_persistence_shadow",
        "enable_classifier_live",
        "enable_classifier_shadow",
    ],
    "enable_mean_reversion_live": ["enable_router_live"],
    "enable_event_overlays_live": ["enable_event_overlays_shadow"],
}


class ConfigError(Exception):
    pass


class FeatureFlags:
    def __init__(self, raw_config: dict[str, Any]):
        self._flags = {}
        self._load(raw_config)

    def _load(self, raw: dict[str, Any]) -> None:
        for key in raw:
            if key not in KNOWN_FLAGS:
                raise ConfigError(
                    f"Unknown feature flag: '{key}'. "
                    f"Known flags: {sorted(KNOWN_FLAGS)}"
                )

        for flag_name in KNOWN_FLAGS:
            if flag_name in raw:
                self._flags[flag_name] = bool(raw[flag_name])
            else:
                self._flags[flag_name] = SAFE_DEFAULTS[flag_name]
                logger.warning(
                    f"Missing flag '{flag_name}' in config, "
                    f"using safe default: {SAFE_DEFAULTS[flag_name]}"
                )

        self._validate_dependencies()

    def _validate_dependencies(self) -> None:
        """§6.3: enforce dependency graph at startup."""
        for child, parents in DEPENDENCIES.items():
            if self._flags.get(child, False):
                for parent in parents:
                    if not self._flags.get(parent, False):
                        raise ConfigError(
                            f"Flag '{child}' requires '{parent}' to be enabled. "
                            f"Dependency chain: {child} → {parent}"
                        )

    def get(self, name: str) -> bool:
        if name not in KNOWN_FLAGS:
            raise ConfigError(f"Unknown flag: '{name}'")
        return self._flags[name]

    def __getattr__(self, name: str) -> bool:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in KNOWN_FLAGS:
            return self._flags[name]
        raise AttributeError(f"No flag named '{name}'")

    def as_dict(self) -> dict[str, bool]:
        return dict(self._flags)

    def startup_summary(self) -> str:
        """§6.4: format flag state for startup logging."""
        lines = ["Flags:"]
        pairs = [
            ("classifier", "enable_classifier_shadow", "enable_classifier_live"),
            ("persistence", "enable_persistence_shadow", "enable_persistence_live"),
            ("router", "enable_router_shadow", "enable_router_live"),
            ("overlays", "enable_event_overlays_shadow", "enable_event_overlays_live"),
            ("mean_reversion", "enable_mean_reversion_shadow", "enable_mean_reversion_live"),
        ]
        for label, shadow_key, live_key in pairs:
            s = str(self._flags[shadow_key]).lower()
            l = str(self._flags[live_key]).lower()
            lines.append(f"  {label}: shadow={s}, live={l}")

        lines.append(f"  position_tagged_exit: {str(self._flags['enable_position_tagged_exit_policy']).lower()}")
        lines.append(f"  calendar_ui: {str(self._flags['enable_calendar_ui']).lower()}")
        lines.append(f"  data_quality_strict: {str(self._flags['data_quality_strict_mode']).lower()}")

        any_live = any(
            self._flags.get(k, False) for k in KNOWN_FLAGS if k.endswith("_live")
        )
        if any_live:
            mode = "LIVE (some live flags enabled)"
        else:
            mode = "SHADOW (no live routing; existing triple-confirmation path in use; overlays advisory only)"
        lines.append(f"Effective mode: {mode}")

        return "\n".join(lines)
