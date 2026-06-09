"""Structural eligibility predicate: blocking reasons, corp-action honesty,
sector-unknown is non-blocking but recorded."""
from bot.universe import params
from bot.universe.eligibility import structural_eligibility
from bot.universe.models import Reason


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


def test_corp_action_unavailable_is_blocking_not_silent():
    r = structural_eligibility(_ok_snapshot(corp_action_status="unavailable"))
    assert not r.passes
    assert Reason.CORP_ACTION_DATA_UNAVAILABLE in r.reason_codes


def test_corp_action_anomaly_blocks():
    r = structural_eligibility(_ok_snapshot(corp_action_status="anomaly"))
    assert not r.passes and Reason.CORP_ACTION_ANOMALY in r.reason_codes


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
