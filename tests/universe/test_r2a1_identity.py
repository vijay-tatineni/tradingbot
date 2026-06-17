"""R2A-1 canonical identity (P3-6): collisions, continuity, conflicts, missing-identity,
provider failures, and atomic/idempotent resolution persistence.

The opaque instrument_uid is derived from the VERIFIED ANCHOR (ISIN/FIGI), never the
ticker, so same-ticker/different-anchor cases never silently merge. All providers here are
broker-free fixtures; no broker, data provider, or live DB is touched.
"""
from datetime import date

import pytest

from bot.universe.db import connect
from bot.universe.identity import (
    IdentityConflictError, IdentityReferenceStatus, IdentityResolutionError,
    derive_instrument_uid, derive_listing_uid,
)
from bot.universe.identity_store import IdentityStore
from tests.universe._fixtures import iref

TD = date(2026, 6, 12)
VER = "r2a1_resolver_v1"


def _store(tmp_path):
    return IdentityStore(str(tmp_path / "universe.db"))


# ── identity collisions: no silent merge ────────────────────────────────────────
def test_same_ticker_different_mic_are_distinct_listings(tmp_path):
    st = _store(tmp_path)
    # Same display ticker "ABC", same ISIN (one economic instrument), two venues.
    a = st.resolve_identity_atomic(
        iref("ABC", isin="GB00ABCDEF01", figi=None, mic="XNAS", currency="USD",
             conid="1", exchange="XNAS"), TD, VER)
    b = st.resolve_identity_atomic(
        iref("ABC", isin="GB00ABCDEF01", figi=None, mic="XLON", currency="GBP",
             conid="2", exchange="XLON"), TD, VER)
    assert a["instrument_uid"] == b["instrument_uid"]        # same economic instrument
    assert a["listing_uid"] != b["listing_uid"]              # DISTINCT venue listings


def test_same_ticker_different_currency_are_distinct_listings(tmp_path):
    st = _store(tmp_path)
    a = st.resolve_identity_atomic(
        iref("XYZ", isin="US1111111111", figi=None, mic="XNAS", currency="USD",
             conid="1"), TD, VER)
    b = st.resolve_identity_atomic(
        iref("XYZ", isin="US1111111111", figi=None, mic="XNAS", currency="EUR",
             conid="2"), TD, VER)
    assert a["instrument_uid"] == b["instrument_uid"]
    assert a["listing_uid"] != b["listing_uid"]              # currency distinguishes listing


def test_same_ticker_different_verified_anchor_is_new_identity_never_merged(tmp_path):
    st = _store(tmp_path)
    # Same ticker + venue + currency, but a DIFFERENT verified ISIN ⇒ different economic
    # instrument ⇒ new instrument_uid + new listing_uid (never collapsed into one identity).
    a = st.resolve_identity_atomic(
        iref("DUP", isin="US2222222222", figi=None, mic="XNAS", currency="USD", conid="1"),
        TD, VER)
    # The first listing must be retired before the ticker is reused for a different security
    # (an active-coordinate collision is otherwise a conflict — see the conflict test).
    st.close_listing_atomic(a["listing_uid"], TD)
    b = st.resolve_identity_atomic(
        iref("DUP", isin="US3333333333", figi=None, mic="XNAS", currency="USD", conid="2"),
        date(2026, 6, 13), VER)
    assert a["instrument_uid"] != b["instrument_uid"]
    assert a["listing_uid"] != b["listing_uid"]


def test_share_classes_are_distinct_instruments(tmp_path):
    st = _store(tmp_path)
    a = st.resolve_identity_atomic(
        iref("BRK.A", isin="US0846701086", figi=None, mic="XNYS", currency="USD", conid="1"),
        TD, VER)
    b = st.resolve_identity_atomic(
        iref("BRK.B", isin="US0846707026", figi=None, mic="XNYS", currency="USD", conid="2"),
        TD, VER)
    assert a["instrument_uid"] != b["instrument_uid"]        # different ISIN ⇒ distinct


def test_ticker_reuse_gets_distinct_uids(tmp_path):
    st = _store(tmp_path)
    first = st.resolve_identity_atomic(
        iref("REUSE", isin="US4444444444", figi=None, mic="XNAS", currency="USD", conid="1",
             effective="2020-01-02", verified="2020-01-02T12:00:00"),
        date(2020, 1, 2), VER)
    st.close_listing_atomic(first["listing_uid"], date(2021, 1, 2))   # delisted
    later = st.resolve_identity_atomic(
        iref("REUSE", isin="US5555555555", figi=None, mic="XNAS", currency="USD", conid="2",
             effective="2026-01-04", verified="2026-01-04T12:00:00"),
        date(2026, 1, 4), VER)
    assert first["instrument_uid"] != later["instrument_uid"]
    assert first["listing_uid"] != later["listing_uid"]
    # the historical (delisted) listing row is retained
    assert st.get_listing(first["listing_uid"])["listing_status"] == "DELISTED"


# ── identity continuity ─────────────────────────────────────────────────────────
def test_ticker_rename_keeps_instrument_uid_and_audits_old_symbol(tmp_path):
    st = _store(tmp_path)
    a = st.resolve_identity_atomic(
        iref("OLD", isin="US6666666666", figi=None, mic="XNAS", currency="USD", conid="1"),
        TD, VER)
    b = st.resolve_identity_atomic(
        iref("NEW", isin="US6666666666", figi=None, mic="XNAS", currency="USD", conid="1"),
        date(2026, 6, 13), VER)
    assert a["instrument_uid"] == b["instrument_uid"]        # same verified anchor
    assert a["listing_uid"] == b["listing_uid"]              # same venue/currency listing
    assert b["renamed"] is True
    assert st.get_listing(b["listing_uid"])["display_symbol"] == "NEW"
    # historical display symbol remains auditable
    events = st.audit_events(a["instrument_uid"])
    assert any(e["event_type"] == "LISTING_RENAMED" for e in events)
    rename = [e for e in events if e["event_type"] == "LISTING_RENAMED"][0]
    assert "OLD" in (rename["detail"] or "")


def test_listing_migration_new_listing_uid_old_retained(tmp_path):
    st = _store(tmp_path)
    a = st.resolve_identity_atomic(
        iref("MIG", isin="US7777777777", figi=None, mic="XNAS", currency="USD", conid="1"),
        TD, VER)
    mig = st.record_listing_migration_atomic(
        a["instrument_uid"], a["listing_uid"],
        iref("MIG", isin="US7777777777", figi=None, mic="XLON", currency="GBP",
             conid="1", exchange="XLON"),
        date(2026, 6, 20), VER)
    assert mig["instrument_uid"] == a["instrument_uid"]      # same instrument_uid
    assert mig["new_listing_uid"] != a["listing_uid"]        # new listing_uid
    old = st.get_listing(a["listing_uid"])
    assert old is not None and old["valid_to"] is not None    # old row retained, closed
    assert old["listing_status"] == "MIGRATED"
    new = st.get_listing(mig["new_listing_uid"])
    assert new["valid_from"] is not None and new["listing_status"] == "ACTIVE"


def test_delisting_retains_historical_listing(tmp_path):
    st = _store(tmp_path)
    a = st.resolve_identity_atomic(
        iref("DEL", isin="US8888888888", figi=None, mic="XNAS", currency="USD", conid="1"),
        TD, VER)
    st.close_listing_atomic(a["listing_uid"], date(2026, 6, 30))
    row = st.get_listing(a["listing_uid"])
    assert row is not None                                   # retained
    assert row["listing_status"] == "DELISTED" and row["valid_to"] is not None


# ── conflicts ─────────────────────────────────────────────────────────────────
def test_same_anchor_conflicting_figi_raises(tmp_path):
    st = _store(tmp_path)
    st.resolve_identity_atomic(
        iref("CFL", isin="US9999999999", figi="BBG000000001", mic="XNAS", currency="USD",
             conid="1"), TD, VER)
    # SAME ISIN (same opaque key) but a CONFLICTING FIGI → IdentityConflictError (never merge).
    with pytest.raises(IdentityConflictError):
        st.resolve_identity_atomic(
            iref("CFL", isin="US9999999999", figi="BBG000000002", mic="XNAS", currency="USD",
                 conid="1"), date(2026, 6, 13), VER)


def test_active_coordinate_reuse_for_different_instrument_raises(tmp_path):
    st = _store(tmp_path)
    st.resolve_identity_atomic(
        iref("AMB", isin="US1212121212", figi=None, mic="XNAS", currency="USD", conid="1"),
        TD, VER)
    # SAME active (display, mic, currency) coordinates but a DIFFERENT verified anchor while
    # the first listing is still ACTIVE → ambiguous → conflict (must not silently merge).
    with pytest.raises(IdentityConflictError):
        st.resolve_identity_atomic(
            iref("AMB", isin="US3434343434", figi=None, mic="XNAS", currency="USD", conid="2"),
            date(2026, 6, 13), VER)


# ── missing identity ────────────────────────────────────────────────────────────
def test_no_verified_anchor_fails_closed(tmp_path):
    st = _store(tmp_path)
    with pytest.raises(IdentityResolutionError):
        st.resolve_identity_atomic(
            iref("NONE", isin=None, figi=None, mic="XNAS", currency="USD"), TD, VER)


def test_canonical_row_without_resolution_is_identity_unverified(tmp_path):
    # A canonical row that never went through resolution has NULL instrument_uid → the gate
    # blocks entry (identity_unverified). Exercised directly via the store gate.
    from bot.universe.models import Reason
    from bot.universe.registry import Registry
    reg = Registry(str(tmp_path / "universe.db"))
    reg.upsert_canonical({"canonical_instrument_id": "US_FOO", "display_symbol": "FOO",
                          "currency": "USD", "exchange": "XNAS", "primary_gateway": "IBKR"})
    st = IdentityStore(reg.db_path)
    rec = reg.get_canonical("US_FOO")
    gate = st.entry_identity_gate(rec, TD)
    assert gate.passes is False
    assert Reason.IDENTITY_UNVERIFIED in gate.reason_codes


# ── provider failures fail closed ───────────────────────────────────────────────
@pytest.mark.parametrize("status", [
    IdentityReferenceStatus.AMBIGUOUS, IdentityReferenceStatus.NOT_FOUND,
    IdentityReferenceStatus.UNVERIFIED, IdentityReferenceStatus.STALE,
])
def test_non_verified_reference_status_fails_closed(tmp_path, status):
    st = _store(tmp_path)
    with pytest.raises(IdentityResolutionError):
        st.resolve_identity_atomic(
            iref("PF", status=status.value), TD, VER)


def test_none_reference_fails_closed(tmp_path):
    st = _store(tmp_path)
    with pytest.raises(IdentityResolutionError):
        st.resolve_identity_atomic(None, TD, VER)


def test_future_effective_date_fails_closed(tmp_path):
    st = _store(tmp_path)
    with pytest.raises(IdentityResolutionError):
        st.resolve_identity_atomic(iref("FUT", effective="2026-06-20"), TD, VER)


def test_future_verified_at_fails_closed(tmp_path):
    st = _store(tmp_path)
    with pytest.raises(IdentityResolutionError):
        st.resolve_identity_atomic(iref("FUT2", verified="2026-06-20T00:00:00"), TD, VER)


# ── atomic persistence + idempotency ────────────────────────────────────────────
def test_identical_resolution_replay_is_idempotent_no_duplicate_rows(tmp_path):
    st = _store(tmp_path)
    r1 = st.resolve_identity_atomic(iref("IDEM", isin="US5656565656", figi=None), TD, VER)
    r2 = st.resolve_identity_atomic(iref("IDEM", isin="US5656565656", figi=None), TD, VER)
    assert r1["instrument_uid"] == r2["instrument_uid"]
    with connect(st.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM instrument_identity").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM instrument_listing").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM ibkr_mapping").fetchone()[0] == 1


@pytest.mark.parametrize("seam", ["after_identity", "after_listing", "at_commit"])
def test_injected_fault_rolls_back_fully_then_retry_succeeds(tmp_path, seam):
    st = _store(tmp_path)

    def boom(s):
        if s == seam:
            raise RuntimeError(f"injected at {seam}")

    with pytest.raises(RuntimeError):
        st.resolve_identity_atomic(iref("ROLL", isin="US7878787878", figi=None), TD, VER,
                                   _fault_hook=boom)
    # full rollback: nothing persisted
    with connect(st.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM instrument_identity").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM instrument_listing").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM identity_audit").fetchone()[0] == 0
    # clean retry fully succeeds
    r = st.resolve_identity_atomic(iref("ROLL", isin="US7878787878", figi=None), TD, VER)
    assert st.get_identity(r["instrument_uid"])["identity_status"] == "VERIFIED"


def test_resolution_links_canonical_row(tmp_path):
    from bot.universe.registry import Registry
    reg = Registry(str(tmp_path / "universe.db"))
    reg.upsert_canonical({"canonical_instrument_id": "US_AAPL", "display_symbol": "AAPL",
                          "currency": "USD", "exchange": "XNAS", "primary_gateway": "IBKR"})
    st = IdentityStore(reg.db_path)
    r = st.resolve_identity_atomic(iref("AAPL"), TD, VER, canonical_instrument_id="US_AAPL")
    rec = reg.get_canonical("US_AAPL")
    assert rec["instrument_uid"] == r["instrument_uid"]
    assert rec["identity_status"] == "VERIFIED"


def test_uid_derivation_is_anchor_based_not_ticker_based():
    # Different tickers, SAME ISIN → SAME instrument_uid (ticker is irrelevant to identity).
    assert derive_instrument_uid(isin="US0378331005") == derive_instrument_uid(isin="us0378331005")
    a = derive_instrument_uid(isin="US0378331005")
    # listing_uid depends on instrument_uid + MIC + currency
    assert derive_listing_uid(a, "XNAS", "USD") != derive_listing_uid(a, "XLON", "USD")
