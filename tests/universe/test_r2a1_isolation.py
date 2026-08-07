"""R2A-1 isolation / default-off proof (P3-6 / P3-7).

The canonical-identity gate is DEFAULT-OFF and the identity modules are broker-free and
un-wired into the live path. With the feature flag off the evaluator is a pure no-op
(no identity provider call, no DB write). With the gate enforced, an unverified instrument
is blocked and a verified one is not — and the IdentityReferenceProvider is never called on
the evaluation hot path (resolution is an offline, controlled operation).
"""
import pathlib

import pytest

from bot.universe.evaluator import ShadowEvaluator
from bot.universe.identity_store import IdentityStore
from bot.universe.models import Reason, State
from bot.universe.registry import Registry
from bot.universe.seed import canonical_id, seed_registry
from tests.universe._fixtures import (
    OFF, ON, SpyProvider, StubPositionProvider, inst, iref, make_bars, write_configs,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
TD = "2026-06-12"
VER = "r2a1_resolver_v1"


# ── structural isolation ────────────────────────────────────────────────────────
def test_identity_modules_import_no_broker():
    forbidden = ("bot.brokers", "ib_insync", "trading_ig", "bot.connection", "eodhd")
    for name in ("identity.py", "identity_store.py"):
        text = (REPO / "bot" / "universe" / name).read_text()
        for f in forbidden:
            assert f not in text, f"{name} references broker/data module {f}"


def test_no_live_module_imports_identity():
    live = [REPO / "main.py", REPO / "api_server.py"]
    live += list((REPO / "bot").glob("*.py"))
    live += list((REPO / "bot" / "plugins").glob("*.py"))
    live += list((REPO / "bot" / "regime").glob("*.py"))
    offenders = [str(p.relative_to(REPO)) for p in live if p.exists()
                 and ("bot.universe.identity" in p.read_text())]
    assert offenders == [], f"live modules import the identity layer: {offenders}"


# ── default-off behaviour ─────────────────────────────────────────────────────────
class _ExplodingStore:
    """If the default-off path ever consults the identity gate this blows up the test."""
    def entry_identity_gate(self, *a, **k):
        raise AssertionError("identity gate consulted while enforce_verified_identity=False")


def _seed(tmp_path):
    p1, p2 = write_configs(tmp_path, [inst("AAPL")], [])
    db = str(tmp_path / "universe.db")
    seed_registry(db, p1, p2)
    return db


def test_default_off_never_consults_identity_gate(tmp_path):
    db = _seed(tmp_path)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    bars = SpyProvider({cid: {"bars": make_bars(), "corp_action_status": "ok", "sector": "Tech"}})
    ev = ShadowEvaluator(Registry(db), bars, ON, equity=100_000,
                         position_provider=StubPositionProvider({cid: None}),
                         identity_store=_ExplodingStore())   # enforce defaults to False
    assert ev.enforce_verified_identity is False
    r = ev.maybe_run(TD, only_ids={cid})       # must NOT raise (gate never consulted)
    assert r["ran"] is True


def test_flag_off_writes_no_identity_rows(tmp_path):
    import sqlite3
    db = _seed(tmp_path)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    bars = SpyProvider({cid: {"bars": make_bars(), "corp_action_status": "ok"}})
    ev = ShadowEvaluator(Registry(db), bars, OFF, equity=100_000,
                         position_provider=StubPositionProvider({cid: None}),
                         enforce_verified_identity=True)
    assert ev.maybe_run(TD) == {"ran": False, "reason": "flag_off", "evaluated": 0}
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM instrument_identity").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM instrument_listing").fetchone()[0] == 0
    con.close()


# ── enforced gate integration ─────────────────────────────────────────────────────
def _run(reg, db, cid, enforce, position=None):
    bars = SpyProvider({cid: {"bars": make_bars(), "corp_action_status": "ok", "sector": "Tech"}})
    ev = ShadowEvaluator(reg, bars, ON, equity=100_000,
                         position_provider=StubPositionProvider({cid: position}),
                         enforce_verified_identity=enforce,
                         identity_store=IdentityStore(db))
    r = ev.maybe_run(TD, only_ids={cid})
    return [o for o in r["outcomes"] if o.get("canonical_instrument_id") == cid][0]


def test_enforced_gate_blocks_unverified_instrument(tmp_path):
    db = _seed(tmp_path)
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")     # seeded row: instrument_uid NULL
    o = _run(reg, db, cid, enforce=True, position=None)
    assert Reason.IDENTITY_UNVERIFIED in o["reason_codes"]
    assert o["new_state"] != State.ENTRY_ELIGIBLE.value


def test_enforced_gate_allows_verified_instrument(tmp_path):
    db = _seed(tmp_path)
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    # resolve a verified identity for the seeded row (XNAS/USD with a verified IBKR conId).
    IdentityStore(db).resolve_identity_atomic(
        iref("AAPL", mic="XNAS", currency="USD", exchange="NASDAQ"),
        TD, VER, canonical_instrument_id=cid)
    o = _run(reg, db, cid, enforce=True, position=None)
    # verified ⇒ none of the identity/mapping gate reasons fire
    gate_reasons = {Reason.IDENTITY_UNVERIFIED, Reason.LISTING_UNVERIFIED,
                    Reason.IBKR_MAPPING_UNVERIFIED, Reason.IBKR_MAPPING_NOT_REFERENCE_VERIFIED,
                    Reason.IBKR_MAPPING_STALE, Reason.IBKR_MAPPING_MISMATCH}
    assert not (gate_reasons & set(o["reason_codes"]))


def test_provider_never_called_on_evaluation_hot_path(tmp_path):
    # The IdentityReferenceProvider is an OFFLINE resolution seam — the evaluator never calls
    # it during a run (it reads only persisted identity). Prove the stub stays untouched.
    from tests.universe._fixtures import StubIdentityProvider
    db = _seed(tmp_path)
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    provider = StubIdentityProvider()
    IdentityStore(db).resolve_identity_atomic(
        iref("AAPL", mic="XNAS", currency="USD", exchange="NASDAQ"),
        TD, VER, canonical_instrument_id=cid)
    _run(reg, db, cid, enforce=True, position=None)
    assert provider.calls == []          # evaluator never consulted the reference provider


def test_enforced_gate_does_not_disturb_reconciliation_dominance(tmp_path):
    # Position reconciliation must still dominate: an authoritative-open→flat without closure
    # evidence yields POSITION_RECONCILIATION even with the identity gate enforced and the
    # identity unverified (the gate blocks ENTRY, it never overrides position safety).
    from bot.universe.models import PositionStatus
    db = _seed(tmp_path)
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    reg.upsert_state({"canonical_instrument_id": cid, "current_state": "POSITION_OPEN",
                      "evaluated_trading_date": "2026-06-11", "evaluator_version": VER,
                      "last_authoritative_position_status": "POSITION_OPEN",
                      "last_authoritative_position_id_hash": "h", "position_reconciliation_required": 0})
    o = _run(reg, db, cid, enforce=True, position=PositionStatus.NO_POSITION)
    assert o["new_state"] == State.POSITION_RECONCILIATION.value
