"""Shadow evaluator: flag-off zero-effect, idempotency, hypothetical breakout +
contention (slot/sector/heat), IBKR-only routing, no broker calls, restart
recovery, candidate TTL, EXIT_ONLY exclusion."""
import sqlite3

from bot.universe import params
from bot.universe.evaluator import ShadowEvaluator
from bot.universe.registry import Registry
from bot.universe.seed import canonical_id, seed_registry
from bot.universe.models import State
from tests.universe._fixtures import (
    OFF, ON, SpyProvider, StubPositionProvider, inst, make_bars, write_configs,
)


def _seed(tmp_path, symbols):
    p1, p2 = write_configs(tmp_path, [inst(s) for s in symbols], [])
    db = str(tmp_path / "universe.db")
    seed_registry(db, p1, p2)
    return db


def _state_rows(db):
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM universe_state").fetchone()[0]
    h = con.execute("SELECT COUNT(*) FROM universe_state_history").fetchone()[0]
    con.close()
    return n, h


def _uptrend_source(sector="Tech", volume=2_000_000.0):
    return {"bars": make_bars(volume=volume), "corp_action_status": "ok",
            "sector": sector, "spread": 0.01}


def test_flag_off_is_zero_effect(tmp_path):
    db = _seed(tmp_path, ["AAPL", "MSFT"])
    reg = Registry(db)
    prov = SpyProvider({canonical_id("AAPL", "USD", "NASDAQ"): _uptrend_source()})
    pos = StubPositionProvider({})
    ev = ShadowEvaluator(reg, prov, OFF, equity=100_000, position_provider=pos)
    r = ev.maybe_run("2026-06-10")
    assert r == {"ran": False, "reason": "flag_off", "evaluated": 0}
    assert prov.calls == []                 # bars provider never invoked
    assert pos.calls == []                  # position provider never invoked either
    assert _state_rows(db) == (0, 0)        # no DB writes whatsoever


def test_idempotent_same_day(tmp_path):
    db = _seed(tmp_path, ["AAPL", "MSFT"])
    reg = Registry(db)
    ev = ShadowEvaluator(reg, SpyProvider({}), ON, equity=100_000)
    r1 = ev.maybe_run("2026-06-10")
    assert r1["evaluated"] == 2
    r2 = ev.maybe_run("2026-06-10")
    assert r2["evaluated"] == 0             # all idempotent-skipped
    _, h = _state_rows(db)
    assert h == 2                            # no duplicate history rows


def test_entry_hysteresis_two_sessions_to_eligible(tmp_path):
    db = _seed(tmp_path, ["AAPL"])
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    prov = SpyProvider({cid: _uptrend_source()})
    ev = ShadowEvaluator(reg, prov, ON, equity=100_000)
    o1 = [o for o in ev.maybe_run("2026-06-10")["outcomes"] if o["canonical_instrument_id"] == cid][0]
    assert o1["new_state"] == State.WATCHLIST.value and o1["entry_signal"] is True
    r2 = ev.maybe_run("2026-06-11")
    o2 = [o for o in r2["outcomes"] if o["canonical_instrument_id"] == cid][0]
    assert o2["new_state"] == State.ENTRY_ELIGIBLE.value
    assert cid in [s["canonical_instrument_id"] for s in r2["selected"]]


def test_ibkr_only_routing_in_selection(tmp_path):
    db = _seed(tmp_path, ["AAPL"])
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    prov = SpyProvider({cid: _uptrend_source()})
    ev = ShadowEvaluator(reg, prov, ON, equity=100_000)
    ev.maybe_run("2026-06-10")
    sel = ev.maybe_run("2026-06-11")["selected"]
    assert sel and all(s["hypothetical_order"]["primary_gateway"] == "IBKR" for s in sel)
    # IG remains blocked regardless
    assert reg.get_gateway_ig(cid) is None  # no IG config in this fixture → none


def test_slot_contention_orders_by_adv(tmp_path):
    syms = [f"S{i}" for i in range(6)]
    db = _seed(tmp_path, syms)
    reg = Registry(db)
    # distinct ADV via volume so ordering is deterministic; all different sectors
    sources = {}
    for i, s in enumerate(syms):
        sources[canonical_id(s, "USD", "NASDAQ")] = _uptrend_source(
            sector=f"sec{i}", volume=1_000_000.0 * (i + 1))
    ev = ShadowEvaluator(reg, SpyProvider(sources), ON, equity=100_000)
    ev.maybe_run("2026-06-10")
    r = ev.maybe_run("2026-06-11")
    assert len(r["selected"]) == params.MAX_OPEN_POSITIONS       # 5 slots
    assert len(r["rejected"]) == 1
    assert r["rejected"][0]["rejected_reason"] == "slot_cap_reached"
    # the rejected one is the lowest ADV (S0, volume*1)
    assert r["rejected"][0]["canonical_instrument_id"] == canonical_id("S0", "USD", "NASDAQ")


def test_shadow_outputs_logged_and_ranked(tmp_path, caplog):
    import logging
    syms = [f"R{i}" for i in range(6)]
    db = _seed(tmp_path, syms)
    reg = Registry(db)
    sources = {canonical_id(s, "USD", "NASDAQ"): _uptrend_source(
        sector=f"sec{i}", volume=1_000_000.0 * (i + 1)) for i, s in enumerate(syms)}
    ev = ShadowEvaluator(reg, SpyProvider(sources), ON, equity=100_000)
    ev.maybe_run("2026-06-10")
    with caplog.at_level(logging.INFO, logger="universe.evaluator"):
        r = ev.maybe_run("2026-06-11")
    # selected carry deterministic 1..5 slot ranks
    assert sorted(s["slot_rank"] for s in r["selected"]) == [1, 2, 3, 4, 5]
    # §9: hypothetical selections AND rejection reason are logged
    assert "SELECT" in caplog.text
    assert "REJECT" in caplog.text and "reason=slot_cap_reached" in caplog.text
    assert "gateway=IBKR" in caplog.text


def test_sector_cap(tmp_path):
    syms = ["A1", "A2", "A3"]
    db = _seed(tmp_path, syms)
    reg = Registry(db)
    sources = {canonical_id(s, "USD", "NASDAQ"): _uptrend_source(
        sector="Tech", volume=1_000_000.0 * (i + 1)) for i, s in enumerate(syms)}
    ev = ShadowEvaluator(reg, SpyProvider(sources), ON, equity=100_000)
    ev.maybe_run("2026-06-10")
    r = ev.maybe_run("2026-06-11")
    assert len(r["selected"]) == params.MAX_POSITIONS_PER_SECTOR  # capped at 2 / sector
    assert any(x["rejected_reason"] == "sector_cap_reached" for x in r["rejected"])


def test_heat_and_risk_within_caps(tmp_path):
    syms = [f"H{i}" for i in range(5)]
    db = _seed(tmp_path, syms)
    reg = Registry(db)
    sources = {canonical_id(s, "USD", "NASDAQ"): _uptrend_source(sector=f"x{i}")
               for i, s in enumerate(syms)}
    ev = ShadowEvaluator(reg, SpyProvider(sources), ON, equity=100_000)
    ev.maybe_run("2026-06-10")
    r = ev.maybe_run("2026-06-11")
    total_heat = 0.0
    for s in r["selected"]:
        o = s["hypothetical_order"]
        assert o["qty"] > 0
        assert o["notional_usd"] <= params.MAX_NOTIONAL_PCT * 100_000 + 1e-6
        # per-trade risk never exceeds the 0.50% target (floored qty → ≤)
        assert o["risk_usd"] <= params.RISK_PER_TRADE * 100_000 + 1e-6
        total_heat += o["risk_usd"]
    assert total_heat <= params.MAX_PORTFOLIO_HEAT * 100_000 + 1e-6


def test_no_broker_calls_only_injected_provider(tmp_path):
    db = _seed(tmp_path, ["AAPL", "MSFT"])
    reg = Registry(db)
    prov = SpyProvider({canonical_id("AAPL", "USD", "NASDAQ"): _uptrend_source()})
    ev = ShadowEvaluator(reg, prov, ON, equity=100_000)
    ev.maybe_run("2026-06-10")
    # the ONLY data seam used is the injected provider; it was called once per instrument
    assert set(prov.calls) == {canonical_id("AAPL", "USD", "NASDAQ"),
                               canonical_id("MSFT", "USD", "NASDAQ")}


def test_restart_recovery_idempotent_and_continues(tmp_path):
    db = _seed(tmp_path, ["AAPL"])
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    src = {cid: _uptrend_source()}
    ev1 = ShadowEvaluator(Registry(db), SpyProvider(src), ON, equity=100_000)
    ev1.maybe_run("2026-06-10")
    # simulate restart: brand-new Registry + Evaluator over the same db
    ev2 = ShadowEvaluator(Registry(db), SpyProvider(src), ON, equity=100_000)
    assert ev2.maybe_run("2026-06-10")["evaluated"] == 0   # same day → idempotent
    r = ev2.maybe_run("2026-06-11")                         # next day continues
    o = [x for x in r["outcomes"] if x["canonical_instrument_id"] == cid][0]
    assert o["new_state"] == State.ENTRY_ELIGIBLE.value     # hysteresis survived restart


def test_candidate_ttl_expiry(tmp_path):
    db = _seed(tmp_path, ["AAPL"])
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    reg.add_candidate({"candidate_id": "c-old", "canonical_instrument_id": cid,
                       "source": "MANUAL", "expires_after_trading_date": "2026-06-05"})
    reg.add_candidate({"candidate_id": "c-live", "canonical_instrument_id": cid,
                       "source": "TTI", "expires_after_trading_date": "2026-12-31"})
    ev = ShadowEvaluator(reg, SpyProvider({}), ON, equity=100_000)
    r = ev.maybe_run("2026-06-10")
    assert r["expired_candidates"] == 1
    active_ids = {c["candidate_id"] for c in reg.active_candidates()}
    assert active_ids == {"c-live"}


def test_exit_only_instrument_not_selected_as_candidate(tmp_path):
    db = _seed(tmp_path, ["AAPL"])
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    # pre-seed an EXIT_ONLY state (hypothetical open position, exits-only)
    reg.upsert_state({"canonical_instrument_id": cid, "current_state": "EXIT_ONLY",
                      "consecutive_passes": 2, "consecutive_failures": 0,
                      "cooldown_until": "0", "evaluator_version": "dyn_universe_shadow_v1"})
    prov = SpyProvider({cid: _uptrend_source()})   # strong breakout signal present
    ev = ShadowEvaluator(reg, prov, ON, equity=100_000)
    r = ev.maybe_run("2026-06-10")
    o = [x for x in r["outcomes"] if x["canonical_instrument_id"] == cid][0]
    # has_open carried from EXIT_ONLY; no exit this bar (uptrend) → stays EXIT_ONLY (POSITION_OPEN class)
    assert o["new_state"] in (State.EXIT_ONLY.value, State.POSITION_OPEN.value)
    # and it is NEVER offered as a new-entry candidate (no reversal/pyramiding)
    assert cid not in [s["canonical_instrument_id"] for s in r["selected"]]
