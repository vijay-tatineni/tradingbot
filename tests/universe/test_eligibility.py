"""Structural eligibility predicate: blocking reasons, corp-action honesty,
sector-unknown is non-blocking but recorded."""
import pytest

from bot.universe import params
from bot.universe.eligibility import structural_eligibility
from bot.universe.models import (
    ELIGIBILITY_MODE_PAPER_LIVE, ELIGIBILITY_MODE_SHADOW, Reason,
)


def _ok_snapshot(**over):
    snap = {
        "bar_count": params.MIN_HISTORY_BARS + 10,
        "fresh_bar": True, "ohlc_valid": True, "indicators_available": True,
        "price": 50.0, "adv20_usd": 100_000_000.0,
        "research_mapping_ok": True, "ibkr_mapping_ok": True,
        "cooldown_remaining": 0, "corp_action_status": "ok", "sector": "Tech",
    }
    snap.update(over)
    return snap


def test_fully_eligible_passes():
    r = structural_eligibility(_ok_snapshot())
    assert r.passes is True
    assert Reason.ELIGIBLE in r.reason_codes


def test_insufficient_history_blocks():
    r = structural_eligibility(_ok_snapshot(bar_count=100))
    assert not r.passes and Reason.INSUFFICIENT_HISTORY in r.reason_codes


def test_below_min_price_and_adv_block():
    r = structural_eligibility(_ok_snapshot(price=5.0, adv20_usd=1_000_000.0))
    assert not r.passes
    assert Reason.PRICE_BELOW_MIN in r.reason_codes
    assert Reason.ADV20_BELOW_MIN in r.reason_codes


def test_corp_action_unavailable_default_mode_is_fail_closed():
    # Default mode is the SAFE paper/live policy: unknown corp-action data HARD-BLOCKS.
    # A forgotten/wrong mode argument must therefore fail closed, never silently pass.
    r = structural_eligibility(_ok_snapshot(corp_action_status="unavailable"))
    assert not r.passes
    assert Reason.CORP_ACTION_DATA_UNAVAILABLE in r.reason_codes
    # and the shadow warning code is NOT used in this mode (policies never confused)
    assert Reason.CORP_ACTION_STATUS_UNKNOWN not in r.reason_codes


def test_corp_action_unavailable_paper_live_blocks_explicitly():
    r = structural_eligibility(
        _ok_snapshot(corp_action_status="unavailable"), mode=ELIGIBILITY_MODE_PAPER_LIVE)
    assert not r.passes
    assert Reason.CORP_ACTION_DATA_UNAVAILABLE in r.reason_codes


def test_corp_action_unavailable_shadow_warns_not_blocks():
    # §4 shadow policy: unknown corp-action data is a WARNING, not a block — so
    # scheduling/state/contention/logging can be exercised operationally.
    r = structural_eligibility(
        _ok_snapshot(corp_action_status="unavailable"), mode=ELIGIBILITY_MODE_SHADOW)
    assert r.passes is True                                  # not blocking
    assert Reason.CORP_ACTION_STATUS_UNKNOWN in r.reason_codes   # but loudly recorded
    assert Reason.CORP_ACTION_DATA_UNAVAILABLE not in r.reason_codes  # never the block code
    assert Reason.ELIGIBLE in r.reason_codes


def test_corp_action_anomaly_blocks_in_both_modes():
    # An anomaly is a known problem (not an unknown) → blocks regardless of mode.
    for mode in (ELIGIBILITY_MODE_PAPER_LIVE, ELIGIBILITY_MODE_SHADOW):
        r = structural_eligibility(_ok_snapshot(corp_action_status="anomaly"), mode=mode)
        assert not r.passes and Reason.CORP_ACTION_ANOMALY in r.reason_codes


def test_corp_action_policies_cannot_be_confused():
    # The same unknown input yields DIFFERENT, non-overlapping reason codes and
    # DIFFERENT pass/fail outcomes under the two modes — they can never be conflated.
    snap = _ok_snapshot(corp_action_status="unavailable")
    shadow = structural_eligibility(snap, mode=ELIGIBILITY_MODE_SHADOW)
    paper = structural_eligibility(snap, mode=ELIGIBILITY_MODE_PAPER_LIVE)
    assert shadow.passes and not paper.passes
    assert Reason.CORP_ACTION_STATUS_UNKNOWN in shadow.reason_codes
    assert Reason.CORP_ACTION_DATA_UNAVAILABLE in paper.reason_codes
    assert Reason.CORP_ACTION_STATUS_UNKNOWN not in paper.reason_codes
    assert Reason.CORP_ACTION_DATA_UNAVAILABLE not in shadow.reason_codes


def test_invalid_eligibility_mode_rejected():
    with pytest.raises(ValueError):
        structural_eligibility(_ok_snapshot(), mode="nonsense")


def test_cooldown_blocks():
    r = structural_eligibility(_ok_snapshot(cooldown_remaining=2))
    assert not r.passes and Reason.IN_COOLDOWN in r.reason_codes


def test_missing_mappings_block():
    r = structural_eligibility(_ok_snapshot(research_mapping_ok=False, ibkr_mapping_ok=False))
    assert not r.passes
    assert Reason.RESEARCH_MAPPING_MISSING in r.reason_codes
    assert Reason.IBKR_MAPPING_MISSING in r.reason_codes


def test_unknown_sector_recorded_but_not_blocking():
    r = structural_eligibility(_ok_snapshot(sector=None))
    assert r.passes is True                      # sector unknown does not fail eligibility
    assert Reason.SECTOR_UNKNOWN in r.reason_codes  # but it IS recorded, never silent
