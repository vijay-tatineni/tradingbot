"""
Regression: v2 removed redundant enable_regime_block_no_trade flag.

Bug: v1 had a redundant flag that duplicated router behaviour.
Fix: v2 removed it. Config rejects unknown flags via typo protection.
Spec: §6, v2 changelog.
"""
import pytest
from bot.regime.flags import FeatureFlags, ConfigError


def test_redundant_flag_rejected():
    with pytest.raises(ConfigError, match="Unknown feature flag"):
        FeatureFlags({"enable_regime_block_no_trade": True})
