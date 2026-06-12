"""Registry seed: idempotency, IBKR-primary, IG unverified+blocked, hard-disabled
preservation, XAU/XAG never enabled, and source config files never rewritten."""
import hashlib

from bot.universe.registry import Registry
from bot.universe.seed import canonical_id, seed_registry
from tests.universe._fixtures import inst, write_configs


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _setup(tmp_path):
    ibkr = [
        inst("AAPL"), inst("MSFT"),
        inst("BARC", currency="GBP", exchange="SMART"),
        inst("SU", currency="EUR", exchange="SMART"),
        inst("XAUUSD", currency="USD", exchange="SMART", hard_disabled=True,
             disabled_reason="no_cfd_market_data_paper_account"),
    ]
    ig = [
        {"symbol": "AAPL", "ig_epic": "UA.D.AAPL.CASH.IP", "currency": "USD", "exchange": "NASDAQ"},
        {"symbol": "NBIS", "ig_epic": "UD.D.YNDX.CASH.IP", "currency": "USD", "exchange": "NASDAQ"},
    ]
    p1, p2 = write_configs(tmp_path, ibkr, ig)
    db = str(tmp_path / "universe.db")
    return db, p1, p2


def test_seed_idempotent_and_no_config_rewrite(tmp_path):
    db, p1, p2 = _setup(tmp_path)
    before = (_sha(p1), _sha(p2))
    s1 = seed_registry(db, p1, p2)
    s2 = seed_registry(db, p1, p2)
    assert s1 == s2                      # rerun produces identical summary
    assert (_sha(p1), _sha(p2)) == before  # config files untouched
    reg = Registry(db)
    # rerun did not duplicate canonical rows (5 IBKR + NBIS which is IG-only here)
    assert len(reg.all_canonical()) == 6


def test_ibkr_primary_for_all(tmp_path):
    db, p1, p2 = _setup(tmp_path)
    seed_registry(db, p1, p2)
    reg = Registry(db)
    for c in reg.all_canonical():
        assert c["primary_gateway"] == "IBKR"


def test_hard_disabled_preserved_and_never_enabled(tmp_path):
    db, p1, p2 = _setup(tmp_path)
    seed_registry(db, p1, p2)
    reg = Registry(db)
    xau = reg.get_canonical(canonical_id("XAUUSD", "USD", "SMART"))
    assert xau["hard_disabled"] == 1
    assert xau["administratively_active"] == 0     # never enabled
    assert xau["disabled_reason"] == "no_cfd_market_data_paper_account"


def test_ig_mappings_unverified_and_routing_blocked(tmp_path):
    db, p1, p2 = _setup(tmp_path)
    seed_registry(db, p1, p2)
    reg = Registry(db)
    for sym, ccy in (("AAPL", "USD"), ("NBIS", "USD")):
        ig = reg.get_gateway_ig(canonical_id(sym, ccy, "NASDAQ"))
        assert ig is not None
        assert ig["verification_status"] == "UNVERIFIED"
        assert ig["order_routing_blocked"] == 1


def test_ig_mapping_cannot_be_made_order_capable(tmp_path):
    """Even an explicit attempt to unblock an IG mapping is overridden by the registry."""
    db, p1, p2 = _setup(tmp_path)
    seed_registry(db, p1, p2)
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    reg.upsert_gateway_ig({"canonical_instrument_id": cid, "epic": "X",
                           "verification_status": "VERIFIED_REFERENCE_MATCH",
                           "order_routing_blocked": 0})
    assert reg.get_gateway_ig(cid)["order_routing_blocked"] == 1  # forced blocked


def test_nbis_epic_recorded_as_legacy_unverified(tmp_path):
    db, p1, p2 = _setup(tmp_path)
    seed_registry(db, p1, p2)
    reg = Registry(db)
    ig = reg.get_gateway_ig(canonical_id("NBIS", "USD", "NASDAQ"))
    assert ig["epic"] == "UD.D.YNDX.CASH.IP"   # legacy stem preserved as metadata
    assert ig["order_routing_blocked"] == 1


def test_seed_against_real_repo_configs_leaves_them_unchanged(tmp_path):
    """Seeding from the live repo configs must not rewrite them."""
    from bot.universe.db import REPO_ROOT
    real_ibkr = REPO_ROOT / "instruments.json"
    real_ig = REPO_ROOT / "instruments_ig.json"
    before = (_sha(real_ibkr), _sha(real_ig))
    db = str(tmp_path / "universe.db")
    summary = seed_registry(db, real_ibkr, real_ig)
    assert (_sha(real_ibkr), _sha(real_ig)) == before
    assert summary["hard_disabled"] == 2          # XAUUSD + XAGUSD
    assert summary["ibkr_primary_overlaps"] == 10  # the ten approved overlaps
