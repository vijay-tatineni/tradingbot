"""R2A-1 verified broker mappings (P3-7): IBKR status gating, freshness/mismatch
fail-closed behaviour, IG order-routing block, and the deterministic pre-entry gate.

Only VERIFIED_REFERENCE_MATCH (fresh + field-consistent) passes; every other state and
every mismatch blocks NEW entry. No broker is called; the mapping verifier is pure.
"""
from datetime import date, datetime, timedelta

import pytest

from bot.universe.identity import (
    MappingVerificationStatus as MS, verify_ibkr_mapping,
)
from bot.universe.identity_store import IdentityStore
from bot.universe.models import Reason
from bot.universe.registry import Registry
from tests.universe._fixtures import iref

TD = date(2026, 6, 12)
VER = "r2a1_resolver_v1"


def _store(tmp_path):
    return IdentityStore(str(tmp_path / "universe.db"))


def _verified_listing():
    return {"currency": "USD", "exchange": "XNAS", "mic": "XNAS"}


def _fresh_mapping(**over):
    m = {"verification_status": MS.VERIFIED_REFERENCE_MATCH.value, "conid": "265598",
         "currency": "USD", "exchange": "XNAS",
         "verified_at": "2026-06-01T00:00:00", "reverify_after_date": "2026-08-30"}
    m.update(over)
    return m


# ── only VERIFIED_REFERENCE_MATCH passes; every other enum value blocks ──────────
def test_only_reference_match_passes():
    v = verify_ibkr_mapping(_fresh_mapping(), _verified_listing(), TD)
    assert v.passes is True and v.reason_code is None


@pytest.mark.parametrize("status,reason", [
    (MS.VERIFIED_CONFIGURED.value, Reason.IBKR_MAPPING_NOT_REFERENCE_VERIFIED),
    (MS.UNVERIFIED.value, Reason.IBKR_MAPPING_UNVERIFIED),
    (MS.STALE.value, Reason.IBKR_MAPPING_STALE),
    (MS.REJECTED.value, Reason.IBKR_MAPPING_REJECTED),
    (MS.AMBIGUOUS.value, Reason.IBKR_MAPPING_AMBIGUOUS),
])
def test_every_non_reference_match_state_blocks(status, reason):
    v = verify_ibkr_mapping(_fresh_mapping(verification_status=status), _verified_listing(), TD)
    assert v.passes is False and v.reason_code == reason


def test_unknown_or_missing_status_fails_closed():
    assert verify_ibkr_mapping({"verification_status": "WAT"}, _verified_listing(), TD).reason_code \
        == Reason.IBKR_MAPPING_UNVERIFIED
    assert verify_ibkr_mapping(None, _verified_listing(), TD).reason_code \
        == Reason.IBKR_MAPPING_UNVERIFIED


# ── freshness / mismatch fail closed ────────────────────────────────────────────
def test_missing_conid_blocks_mismatch():
    v = verify_ibkr_mapping(_fresh_mapping(conid=None), _verified_listing(), TD)
    assert v.reason_code == Reason.IBKR_MAPPING_MISMATCH


def test_expired_reverify_is_stale():
    v = verify_ibkr_mapping(_fresh_mapping(reverify_after_date="2026-06-11"), _verified_listing(), TD)
    assert v.reason_code == Reason.IBKR_MAPPING_STALE


def test_missing_reverify_date_is_stale():
    v = verify_ibkr_mapping(_fresh_mapping(reverify_after_date=None), _verified_listing(), TD)
    assert v.reason_code == Reason.IBKR_MAPPING_STALE


def test_future_verified_at_is_stale():
    v = verify_ibkr_mapping(_fresh_mapping(verified_at="2026-06-20T00:00:00"),
                            _verified_listing(), TD)
    assert v.reason_code == Reason.IBKR_MAPPING_STALE


def test_wrong_currency_blocks_mismatch():
    v = verify_ibkr_mapping(_fresh_mapping(currency="EUR"), _verified_listing(), TD)
    assert v.reason_code == Reason.IBKR_MAPPING_MISMATCH


def test_wrong_exchange_mic_blocks_mismatch():
    v = verify_ibkr_mapping(_fresh_mapping(exchange="XLON"), _verified_listing(), TD)
    assert v.reason_code == Reason.IBKR_MAPPING_MISMATCH


# ── end-to-end gate via the store ───────────────────────────────────────────────
def _seed_resolved(tmp_path, cid="US_AAPL", **iref_over):
    reg = Registry(str(tmp_path / "universe.db"))
    reg.upsert_canonical({"canonical_instrument_id": cid, "display_symbol": "AAPL",
                          "currency": "USD", "exchange": "XNAS", "primary_gateway": "IBKR"})
    st = IdentityStore(reg.db_path)
    st.resolve_identity_atomic(iref(**iref_over), TD, VER, canonical_instrument_id=cid)
    return reg, st


def test_gate_passes_for_verified_identity_and_reference_match(tmp_path):
    reg, st = _seed_resolved(tmp_path)
    gate = st.entry_identity_gate(reg.get_canonical("US_AAPL"), TD)
    assert gate.passes is True and gate.reason_codes == []


def test_gate_blocks_when_mapping_downgraded(tmp_path):
    reg, st = _seed_resolved(tmp_path)
    rec = reg.get_canonical("US_AAPL")
    iuid, luid = rec["instrument_uid"], st.active_listings(rec["instrument_uid"])[0]["listing_uid"]
    st.upsert_ibkr_mapping(iuid, luid, {"conid": "1",
                                        "verification_status": MS.VERIFIED_CONFIGURED.value})
    gate = st.entry_identity_gate(rec, TD)
    assert gate.passes is False
    assert Reason.IBKR_MAPPING_NOT_REFERENCE_VERIFIED in gate.reason_codes


def test_gate_blocks_when_no_active_listing(tmp_path):
    reg, st = _seed_resolved(tmp_path)
    rec = reg.get_canonical("US_AAPL")
    st.close_listing_atomic(st.active_listings(rec["instrument_uid"])[0]["listing_uid"], TD)
    gate = st.entry_identity_gate(rec, TD)
    assert gate.passes is False and Reason.LISTING_UNVERIFIED in gate.reason_codes


def test_gate_blocks_expired_mapping_dynamically(tmp_path):
    reg, st = _seed_resolved(tmp_path)
    rec = reg.get_canonical("US_AAPL")
    # Evaluate a year later: the auto-set reverify_after_date (verified_at + 90d) has lapsed.
    gate = st.entry_identity_gate(rec, date(2027, 6, 12))
    assert gate.passes is False and Reason.IBKR_MAPPING_STALE in gate.reason_codes


# ── IG routing is blocked unconditionally in this tranche ────────────────────────
def test_ig_mapping_is_persisted_order_routing_blocked(tmp_path):
    reg, st = _seed_resolved(tmp_path, epic="IX.D.AAPL.CASH.IP")
    rec = reg.get_canonical("US_AAPL")
    iuid = rec["instrument_uid"]
    luid = st.active_listings(iuid)[0]["listing_uid"]
    ig = st.get_ig_mapping(iuid, luid)
    assert ig is not None and ig["epic"] == "IX.D.AAPL.CASH.IP"
    assert ig["order_routing_blocked"] == 1
    # IG mapping never makes the instrument order-eligible; it is reference/shadow only.
    assert ig["verification_status"] == MS.UNVERIFIED.value


def test_gate_blocks_ig_primary_gateway(tmp_path):
    reg, st = _seed_resolved(tmp_path)
    rec = dict(reg.get_canonical("US_AAPL"))
    rec["primary_gateway"] = "IG"          # defensive: IG routing never authorized here
    gate = st.entry_identity_gate(rec, TD)
    assert gate.passes is False and Reason.IG_ORDER_ROUTING_BLOCKED in gate.reason_codes
