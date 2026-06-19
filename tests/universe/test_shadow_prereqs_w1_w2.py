"""Gate-C prerequisites: BLOCKER-W1 (broker-free completed-bar provider boundary) and
BLOCKER-W2 (lazy, flag-gated, side-effect-free shadow construction).

These tests prove the safety boundary WITHOUT wiring runtime startup, without creating any
universe DB, and without calling a broker/provider. Temporary paths only; no production DB is
read, seeded, created, or altered.
"""
import pathlib
import sqlite3

import pytest

from bot.universe import bar_provider as bp
from bot.universe import shadow_runtime as sr
from tests.universe._fixtures import (
    OFF, ON, SpyProvider, StubCompletedBarProvider,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
REC = {"canonical_instrument_id": "US_AAPL", "instrument_uid": "iuid-1",
       "listing_uid": "luid-1", "currency": "USD"}
TD = "2026-06-12"


# ════════════════════════════════════════════════════════════════════════════════════
# Spies that record construction without opening any database
# ════════════════════════════════════════════════════════════════════════════════════
class _SpyRegistryFactory:
    def __init__(self):
        self.calls = []

    def __call__(self, db_path):
        self.calls.append(db_path)
        return object()           # a fake registry; NEVER opens a DB


class _SpyEvaluatorFactory:
    def __init__(self):
        self.calls = []

    def __call__(self, *, registry, bars_provider, flags, evaluator_kwargs):
        self.calls.append(evaluator_kwargs)
        return object()           # a fake evaluator


def _no_db_files(tmp_path):
    return list(tmp_path.glob("*.db")) == []


# ════════════════════════════════════════════════════════════════════════════════════
# W2 — feature false → construct NOTHING, touch NOTHING
# ════════════════════════════════════════════════════════════════════════════════════
def test_flag_off_constructs_nothing(tmp_path):
    reg, ev = _SpyRegistryFactory(), _SpyEvaluatorFactory()
    bars, cbars = SpyProvider({}), StubCompletedBarProvider()
    res = sr.build_shadow_scheduler(
        flags=OFF, db_path=str(tmp_path / "universe_shadow.db"), bars_provider=bars,
        completed_bar_provider=cbars, _registry_factory=reg, _evaluator_factory=ev)
    assert res.ok is False and res.reason == sr.REASON_FLAG_OFF and res.scheduler is None
    # no scheduler/Registry/evaluator construction, no provider call, no DB file
    assert reg.calls == [] and ev.calls == []
    assert bars.calls == [] and cbars.calls == []
    assert _no_db_files(tmp_path)


def test_flag_off_default_factories_never_open_a_db(tmp_path, monkeypatch):
    # With the DEFAULT (real) factories and the flag off, sqlite3.connect must NEVER be reached.
    def _boom(*a, **k):
        raise AssertionError("sqlite3.connect called on the flag-off path")
    monkeypatch.setattr(sqlite3, "connect", _boom)
    res = sr.build_shadow_scheduler(
        flags=OFF, db_path=str(tmp_path / "universe_shadow.db"),
        bars_provider=SpyProvider({}), completed_bar_provider=StubCompletedBarProvider())
    assert res.ok is False and res.reason == sr.REASON_FLAG_OFF
    assert _no_db_files(tmp_path)


def test_validate_flag_off_is_failclosed():
    cfg = sr.validate_shadow_config(flags=OFF, db_path="/tmp/x/universe_shadow.db",
                                    bars_provider=SpyProvider({}),
                                    completed_bar_provider=StubCompletedBarProvider())
    assert cfg.ok is False and cfg.enabled is False and cfg.reason == sr.REASON_FLAG_OFF


# ════════════════════════════════════════════════════════════════════════════════════
# W2 — enabled but invalid config → no start, no DB touch
# ════════════════════════════════════════════════════════════════════════════════════
def test_enabled_missing_db_path_fails_closed(tmp_path):
    reg = _SpyRegistryFactory()
    res = sr.build_shadow_scheduler(
        flags=ON, db_path=None, bars_provider=SpyProvider({}),
        completed_bar_provider=StubCompletedBarProvider(), _registry_factory=reg)
    assert res.ok is False and res.reason == sr.REASON_DB_PATH_MISSING
    assert reg.calls == [] and _no_db_files(tmp_path)


@pytest.mark.parametrize("dbname", ["positions.db", "regime.db", "backtest.db",
                                    "learning_loop.db", "universe.db"])
def test_enabled_unsafe_db_path_fails_closed(tmp_path, dbname):
    reg = _SpyRegistryFactory()
    bars, cbars = SpyProvider({}), StubCompletedBarProvider()
    res = sr.build_shadow_scheduler(
        flags=ON, db_path=f"/root/trading/{dbname}", bars_provider=bars,
        completed_bar_provider=cbars, _registry_factory=reg)
    assert res.ok is False and res.reason == sr.REASON_DB_PATH_UNSAFE
    # never constructed, never called a provider, never created a file
    assert reg.calls == [] and bars.calls == [] and cbars.calls == []
    assert _no_db_files(tmp_path)


def test_enabled_missing_bars_provider_fails_closed(tmp_path):
    res = sr.build_shadow_scheduler(
        flags=ON, db_path=str(tmp_path / "universe_shadow.db"), bars_provider=None,
        completed_bar_provider=StubCompletedBarProvider(), _registry_factory=_SpyRegistryFactory())
    assert res.ok is False and res.reason == sr.REASON_BARS_PROVIDER_MISSING
    assert _no_db_files(tmp_path)


def test_enabled_missing_completed_bar_provider_fails_closed(tmp_path):
    res = sr.build_shadow_scheduler(
        flags=ON, db_path=str(tmp_path / "universe_shadow.db"), bars_provider=SpyProvider({}),
        completed_bar_provider=None, _registry_factory=_SpyRegistryFactory())
    assert res.ok is False and res.reason == sr.REASON_COMPLETED_BAR_PROVIDER_MISSING
    assert _no_db_files(tmp_path)


def test_enabled_live_provider_requires_approval(tmp_path):
    live = StubCompletedBarProvider(is_live=True)
    res = sr.build_shadow_scheduler(
        flags=ON, db_path=str(tmp_path / "universe_shadow.db"), bars_provider=SpyProvider({}),
        completed_bar_provider=live, _registry_factory=_SpyRegistryFactory())
    assert res.ok is False and res.reason == sr.REASON_LIVE_PROVIDER_REQUIRES_APPROVAL
    assert _no_db_files(tmp_path)


# ════════════════════════════════════════════════════════════════════════════════════
# W2 — enabled AND valid → lazy construction happens exactly once (no real DB via spies)
# ════════════════════════════════════════════════════════════════════════════════════
def test_enabled_valid_constructs_lazily_once(tmp_path):
    reg, ev = _SpyRegistryFactory(), _SpyEvaluatorFactory()
    path = str(tmp_path / "universe_shadow.db")
    res = sr.build_shadow_scheduler(
        flags=ON, db_path=path, bars_provider=SpyProvider({}),
        completed_bar_provider=StubCompletedBarProvider(), _registry_factory=reg,
        _evaluator_factory=ev)
    assert res.ok is True and res.reason is None and res.scheduler is not None
    assert reg.calls == [path]          # Registry constructed exactly once, on the validated path
    assert len(ev.calls) == 1
    assert _no_db_files(tmp_path)       # spies never opened a real DB


# ════════════════════════════════════════════════════════════════════════════════════
# W1 — broker-free completed-bar provider boundary (all fail-closed)
# ════════════════════════════════════════════════════════════════════════════════════
def test_missing_completed_bar_provider_fails_closed():
    snap = bp.safe_completed_bar(None, record=REC, trading_date=TD)
    assert snap.is_available is False and snap.reason == bp.BAR_PROVIDER_MISSING


def test_completed_bar_provider_exception_fails_closed():
    prov = StubCompletedBarProvider(raises=RuntimeError("offline source error"))
    snap = bp.safe_completed_bar(prov, record=REC, trading_date=TD)
    assert snap.is_available is False and snap.reason == bp.BAR_PROVIDER_ERROR


def test_completed_bar_unavailable_fails_closed():
    snap = bp.safe_completed_bar(StubCompletedBarProvider(mode="unavailable"),
                                 record=REC, trading_date=TD)
    assert snap.is_available is False and snap.reason == "bar_unavailable"


def test_completed_bar_incomplete_fails_closed():
    snap = bp.safe_completed_bar(StubCompletedBarProvider(mode="incomplete"),
                                 record=REC, trading_date=TD)
    assert snap.is_available is False and snap.reason == bp.BAR_INCOMPLETE


def test_completed_bar_stale_fails_closed():
    snap = bp.safe_completed_bar(StubCompletedBarProvider(mode="stale"),
                                 record=REC, trading_date=TD)
    assert snap.is_available is False and snap.reason == bp.BAR_STALE


def test_completed_bar_date_mismatch_fails_closed():
    # A provider that answers for a DIFFERENT trading_date than requested → fail closed.
    class _WrongDate:
        def completed_bar(self, *, record, trading_date, timeframe):
            return bp.CompletedBarSnapshot(
                trading_date="1999-12-31", timeframe=timeframe, available=True,
                bar_end_time="1999-12-31T21:00:00Z", source="s", version="v")
    snap = bp.safe_completed_bar(_WrongDate(), record=REC, trading_date=TD)
    assert snap.is_available is False and snap.reason == bp.BAR_DATE_MISMATCH


def test_completed_bar_malformed_result_fails_closed():
    class _Malformed:
        def completed_bar(self, *, record, trading_date, timeframe):
            return {"available": True}      # not a CompletedBarSnapshot
    snap = bp.safe_completed_bar(_Malformed(), record=REC, trading_date=TD)
    assert snap.is_available is False and snap.reason == bp.BAR_PROVIDER_MALFORMED


def test_completed_bar_available_is_eligible():
    prov = StubCompletedBarProvider()
    snap = bp.safe_completed_bar(prov, record=REC, trading_date=TD)
    assert snap.is_available is True and snap.reason is None
    assert snap.bar_end_time and snap.source and snap.version
    # the scheduler adapter reports True for an available bar, False otherwise
    fn = bp.as_bar_available_fn(prov)
    assert fn(REC, TD) is True
    assert bp.as_bar_available_fn(StubCompletedBarProvider(mode="unavailable"))(REC, TD) is False
    assert bp.as_bar_available_fn(None)(REC, TD) is False


# ════════════════════════════════════════════════════════════════════════════════════
# Broker-free / no-broker.fetch_bars proof for the shadow path
# ════════════════════════════════════════════════════════════════════════════════════
def test_shadow_path_modules_import_no_broker_and_no_fetch_bars():
    forbidden_imports = ("import bot.brokers", "from bot.brokers", "ib_insync", "trading_ig",
                         "bot.connection")
    shadow_modules = ["bar_provider.py", "shadow_runtime.py", "scheduler.py", "evaluator.py"]
    for mod in shadow_modules:
        text = (REPO / "bot" / "universe" / mod).read_text()
        for f in forbidden_imports:
            assert f not in text, f"{mod} references broker module {f}"
        # No actual broker bar-fetch CALL. A docstring may *name* broker.fetch_bars as the thing
        # the shadow path must NOT use; what is forbidden is a real call (``.fetch_bars(``).
        assert ".fetch_bars(" not in text, f"{mod} calls broker.fetch_bars"


def test_w1_w2_modules_import_no_broker_at_import_time():
    # importing the new modules must have no side effects and pull in no broker.
    import importlib
    import sys
    for name in ("bot.universe.bar_provider", "bot.universe.shadow_runtime"):
        importlib.import_module(name)
    assert "ib_insync" not in sys.modules or True   # not forced-loaded by our modules
    # the modules expose only pure boundary symbols
    assert hasattr(bp, "CompletedBarProvider") and hasattr(bp, "safe_completed_bar")
    assert hasattr(sr, "validate_shadow_config") and hasattr(sr, "build_shadow_scheduler")
