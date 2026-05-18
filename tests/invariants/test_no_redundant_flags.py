"""
Invariant: config rejects the removed flag enable_regime_block_no_trade.

§6 v2 fix — this flag was redundant with router behaviour and removed.
Typo protection enforces unknown flag rejection.
"""
import pytest
from bot.regime.flags import FeatureFlags, ConfigError


def test_redundant_flag_rejected():
    """Config must reject enable_regime_block_no_trade (removed in v2)."""
    with pytest.raises(ConfigError, match="Unknown feature flag"):
        FeatureFlags({"enable_regime_block_no_trade": True})


def test_any_unknown_flag_rejected():
    """Any unrecognized flag name must be rejected."""
    with pytest.raises(ConfigError, match="Unknown feature flag"):
        FeatureFlags({"totally_made_up_flag": True})
