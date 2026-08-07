"""R2C gate integration — FX-normalized sizing + open-book heat as a NEW-entry gate.

Covers the combined ``evaluate_entry_risk`` decision and the DEFAULT-OFF evaluator wiring:
candidate-effective-but-FX/heat-blocked → no entry; all-pass → eligible only when prior gates
pass; candidate-absent → no entry; existing open position still managed when the gate blocks new
entries; feature disabled → ZERO FX/portfolio provider calls. Broker-free fixtures only.
"""
import sqlite3
from datetime import date, datetime

from bot.universe.evaluator import ShadowEvaluator
from bot.universe.models import Reason, State
from bot.universe.registry import Registry
from bot.universe.risk_gate import evaluate_entry_risk
from bot.universe.seed import canonical_id, seed_registry
from bot.universe.sizing import SizingInputs
from tests.universe._fixtures import (
    OFF, ON, SpyBaseFxRateProvider, SpyProvider, SpyPortfolioRiskProvider, flat,
    fresh_snapshot, inst, make_bars, pos_risk, write_configs,
)

TD1, TD2 = "2026-06-10", "2026-06-11"
ET = datetime(2026, 6, 11, 15, 0, 0)            # injected order-intent time on the selection day


# ── combined evaluate_entry_risk ───────────────────────────────────────
def _inputs(base="USD", inst_ccy="USD"):
    return SizingInputs(base_currency=base, instrument_currency=inst_ccy, entry_price="100",
                        stop_distance="2", equity_base="1000000", instrument_uid="iuid-1",
                        canonical_instrument_id="US_AAPL")


def _decide(*, base="USD", inst_ccy="USD", fx=None, snapshot="fresh", positions=()):
    snap = (fresh_snapshot(base_currency=base, evaluation_time=ET, trading_date=date(2026, 6, 11),
                           positions=positions) if snapshot == "fresh" else snapshot)
    portfolio = SpyPortfolioRiskProvider(snapshot=snap)
    return evaluate_entry_risk(
        _inputs(base, inst_ccy), evaluation_time=ET, evaluation_date=date(2026, 6, 11),
        fx_provider=fx, portfolio_provider=portfolio)


def test_combined_gate_fx_missing_blocks():
    d = _decide(base="EUR", inst_ccy="USD", fx=None)     # cross-currency, no provider
    assert not d.ok and d.decision == "BLOCK" and d.reason == Reason.FX_PROVIDER_UNAVAILABLE


def test_combined_gate_snapshot_missing_blocks():
    d = evaluate_entry_risk(_inputs(), evaluation_time=ET, evaluation_date=date(2026, 6, 11),
                            fx_provider=None, portfolio_provider=None)
    assert not d.ok and d.reason == Reason.PORTFOLIO_SNAPSHOT_MISSING


def test_combined_gate_heat_exceeded_blocks():
    d = _decide(positions=[pos_risk("26000")])           # inherited book over the 25000 limit
    assert not d.ok and d.reason == Reason.OPEN_BOOK_HEAT_EXCEEDED


def test_combined_gate_all_pass_allows():
    d = _decide(positions=[pos_risk("1000")])
    assert d.ok and d.decision == "ALLOW" and d.qty and d.qty > 0
    assert d.proposed_risk_base is not None and d.post_trade_heat_base is not None


def test_combined_gate_cross_currency_all_pass_allows():
    fx = SpyBaseFxRateProvider({("USD", "EUR"): "0.90"})
    d = _decide(base="EUR", inst_ccy="USD", fx=fx, positions=[pos_risk("1000", currency="EUR")])
    assert d.ok and d.fx_rate is not None and d.base_currency == "EUR"
    assert fx.calls and fx.calls[0][:2] == ("USD", "EUR")


# ── evaluator wiring harness ────────────────────────────────────────────
def _seed(tmp_path, symbols, currency="USD"):
    p1, p2 = write_configs(tmp_path, [inst(s, currency=currency) for s in symbols], [])
    db = str(tmp_path / "universe.db")
    seed_registry(db, p1, p2)
    return db


def _uptrend():
    return {"bars": make_bars(volume=2_000_000.0), "corp_action_status": "ok",
            "sector": "Tech", "spread": 0.01}


def _run_two_sessions(reg, prov, *, base_currency, fx_provider, portfolio_provider,
                      enforce=True, flags=ON, equity=1_000_000, **kw):
    ev = ShadowEvaluator(reg, prov, flags, equity=equity, position_provider=flat(),
                         enforce_portfolio_heat=enforce, base_currency=base_currency,
                         base_fx_provider=fx_provider, portfolio_risk_provider=portfolio_provider,
                         risk_evaluation_time=ET, **kw)
    ev.maybe_run(TD1)
    return ev, ev.maybe_run(TD2)


def _snap(base="USD", positions=()):
    return fresh_snapshot(base_currency=base, evaluation_time=ET,
                          trading_date=date(2026, 6, 11), positions=positions)


def test_feature_off_zero_provider_calls(tmp_path):
    # Flag/gate OFF: the two new broker-free seams are NEVER consulted, and selection is
    # unaffected (the instrument still selects via the legacy path).
    db = _seed(tmp_path, ["AAPL"])
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    prov = SpyProvider({cid: _uptrend()})
    fx = SpyBaseFxRateProvider({("USD", "EUR"): "0.90"})        # cross-ccy → would be called if ON
    portfolio = SpyPortfolioRiskProvider(snapshot=_snap("EUR"))
    _ev, r2 = _run_two_sessions(reg, prov, base_currency="EUR", fx_provider=fx,
                                portfolio_provider=portfolio, enforce=False)
    assert fx.calls == [] and portfolio.calls == []
    assert cid in [s["canonical_instrument_id"] for s in r2["selected"]]


def test_gate_on_allows_when_all_pass(tmp_path):
    db = _seed(tmp_path, ["AAPL"])
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    prov = SpyProvider({cid: _uptrend()})
    portfolio = SpyPortfolioRiskProvider(snapshot=_snap("USD", positions=[pos_risk("1000")]))
    _ev, r2 = _run_two_sessions(reg, prov, base_currency="USD", fx_provider=None,
                                portfolio_provider=portfolio)
    assert cid in [s["canonical_instrument_id"] for s in r2["selected"]]
    assert portfolio.calls                                     # the gate consulted the snapshot


def test_gate_on_blocks_when_heat_exceeded(tmp_path):
    db = _seed(tmp_path, ["AAPL"])
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    prov = SpyProvider({cid: _uptrend()})
    portfolio = SpyPortfolioRiskProvider(snapshot=_snap("USD", positions=[pos_risk("26000")]))
    _ev, r2 = _run_two_sessions(reg, prov, base_currency="USD", fx_provider=None,
                                portfolio_provider=portfolio)
    assert cid not in [s["canonical_instrument_id"] for s in r2["selected"]]
    rej = {x["canonical_instrument_id"]: x["rejected_reason"] for x in r2["rejected"]}
    assert rej.get(cid) == Reason.OPEN_BOOK_HEAT_EXCEEDED


def test_gate_on_blocks_when_fx_missing(tmp_path):
    # cross-currency base with NO base FX provider → sizing fails closed → no entry.
    db = _seed(tmp_path, ["AAPL"])
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    prov = SpyProvider({cid: _uptrend()})
    portfolio = SpyPortfolioRiskProvider(snapshot=_snap("EUR"))
    _ev, r2 = _run_two_sessions(reg, prov, base_currency="EUR", fx_provider=None,
                                portfolio_provider=portfolio)
    rej = {x["canonical_instrument_id"]: x["rejected_reason"] for x in r2["rejected"]}
    assert cid not in [s["canonical_instrument_id"] for s in r2["selected"]]
    assert rej.get(cid) == Reason.FX_PROVIDER_UNAVAILABLE


def test_open_position_still_managed_when_gate_blocks_new_entries(tmp_path):
    # AAPL holds an open position; MSFT is a fresh breakout candidate the heat gate blocks.
    # The open position keeps its state/slot (managed) even while new entries are blocked.
    from bot.universe.models import PositionStatus
    from tests.universe._fixtures import StubPositionProvider
    db = _seed(tmp_path, ["AAPL", "MSFT"])
    reg = Registry(db)
    aapl = canonical_id("AAPL", "USD", "NASDAQ")
    msft = canonical_id("MSFT", "USD", "NASDAQ")
    prov = SpyProvider({aapl: _uptrend(), msft: _uptrend()})
    pos = StubPositionProvider({aapl: PositionStatus.POSITION_OPEN, msft: PositionStatus.NO_POSITION})
    portfolio = SpyPortfolioRiskProvider(snapshot=_snap("USD", positions=[pos_risk("26000")]))
    ev = ShadowEvaluator(reg, prov, ON, equity=1_000_000, position_provider=pos,
                         enforce_portfolio_heat=True, base_currency="USD",
                         portfolio_risk_provider=portfolio, risk_evaluation_time=ET)
    ev.maybe_run(TD1)
    r2 = ev.maybe_run(TD2)
    states = {o["canonical_instrument_id"]: o["new_state"] for o in r2["outcomes"]}
    assert states[aapl] == State.POSITION_OPEN.value           # existing position still managed
    assert msft not in [s["canonical_instrument_id"] for s in r2["selected"]]   # new entry blocked


def test_candidate_absent_but_r2c_would_pass_still_no_entry(tmp_path):
    # require_candidate_source + enforce_portfolio_heat: with NO effective candidate the
    # instrument is excluded BEFORE the R2C gate — candidate presence is necessary.
    db = _seed(tmp_path, ["AAPL"])
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")
    prov = SpyProvider({cid: _uptrend()})
    portfolio = SpyPortfolioRiskProvider(snapshot=_snap("USD"))
    _ev, r2 = _run_two_sessions(reg, prov, base_currency="USD", fx_provider=None,
                                portfolio_provider=portfolio, require_candidate_source=True)
    assert cid not in [s["canonical_instrument_id"] for s in r2["selected"]]
    rej = {x["canonical_instrument_id"]: x.get("rejected_reason") for x in r2["rejected"]}
    assert rej.get(cid) in (Reason.CANDIDATE_INACTIVE,)        # candidate gate, not the R2C gate
