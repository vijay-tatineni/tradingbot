"""Gate C runtime wiring: default-off, fail-closed wiring of the Dynamic Universe shadow
scheduler into ``main.py`` (``init_shadow_runtime`` + the ``TradingBot`` per-cycle seam).

These tests prove the wiring is INERT unless explicitly enabled AND validly configured:
  * flag off  → no ``bot.universe`` import through startup, no ``build_shadow_scheduler`` call,
                no ``sqlite3.connect`` / ``migrate()`` / DB file, no provider call, no scheduler;
  * flag on but invalid config / missing provider / live provider / build exception → fail
                closed (no DB touch, no crash, stable reason, scheduler None);
  * the per-cycle seam is a guarded no-op when no scheduler was constructed (production), and
                when a scheduler IS present it is fed an empty (placeholder) record set and
                touches no broker / order;
  * ``api_server.py`` is left untouched.

No production DB is read, seeded, created, or altered; temporary paths only.
"""
import ast
import pathlib
import sqlite3
import sys

import pytest

import main
from tests.universe._fixtures import OFF, ON, StubCompletedBarProvider

REPO = pathlib.Path(__file__).resolve().parents[2]
MAIN_PY = REPO / "main.py"
API_SERVER_PY = REPO / "api_server.py"

SAFE_DB = "universe_shadow_test.db"  # a NON-production basename (validation must accept it)


class _SpyBuilder:
    """Records every call to the W2 ``build_shadow_scheduler`` seam and returns a configurable
    result — so wiring tests never open a real DB or construct a real Registry."""
    def __init__(self, result=None, raises=None):
        self.result = result
        self.raises = raises
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return self.result


class _Result:
    """Stand-in for ShadowBuildResult (ok / reason / scheduler)."""
    def __init__(self, ok, reason=None, scheduler=None):
        self.ok = ok
        self.reason = reason
        self.scheduler = scheduler


class _FakeScheduler:
    """Records maybe_run(records); never imports a broker or constructs an order."""
    def __init__(self):
        self.runs = []

    def maybe_run(self, records):
        self.runs.append(records)
        return {"ran": False, "reason": "fake"}


def _emit_recorder():
    events = []
    return events, (lambda code, reason=None: events.append((code, reason)))


# ════════════════════════════════════════════════════════════════════════════════════
# 1. main.py has no TOP-LEVEL bot.universe import (lazy-only contract)
# ════════════════════════════════════════════════════════════════════════════════════
def test_main_has_no_top_level_bot_universe_import():
    """Static: parse main.py and assert NO module-level (column-0) import of bot.universe.
    The W1/W2 boundary may only be imported lazily, inside init_shadow_runtime, flag-gated."""
    tree = ast.parse(MAIN_PY.read_text())
    for node in tree.body:  # module-level statements only
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("bot.universe") for a in node.names), \
                "top-level 'import bot.universe...' is forbidden in main.py"
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("bot.universe"), \
                "top-level 'from bot.universe...' is forbidden in main.py"


def test_importing_main_does_not_import_bot_universe():
    """Functional: in a FRESH interpreter, ``import main`` must pull in NO bot.universe module
    (a real subprocess so the result is independent of whatever the in-process suite imported)."""
    import subprocess
    code = (
        "import sys\n"
        "import main\n"
        "bad = [m for m in sys.modules if m == 'bot.universe' or m.startswith('bot.universe.')]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                       capture_output=True, text=True)
    assert r.returncode == 0, \
        f"importing main imported bot.universe (no top-level coupling allowed):\n{r.stderr}"


# ════════════════════════════════════════════════════════════════════════════════════
# 2. Feature OFF → total no-op (no import, no build, no DB, no provider, no scheduler)
# ════════════════════════════════════════════════════════════════════════════════════
def test_flag_off_no_builder_call_and_no_bot_universe_import():
    sys.modules.pop("bot.universe.shadow_runtime", None)
    spy = _SpyBuilder(result=_Result(ok=True, scheduler=_FakeScheduler()))
    events, emit = _emit_recorder()

    rt = main.init_shadow_runtime(OFF, db_path=SAFE_DB,
                                  bars_provider=StubCompletedBarProvider(),
                                  completed_bar_provider=StubCompletedBarProvider(),
                                  builder=spy, emit=emit)

    assert rt.ready is False and rt.reason == "flag_off" and rt.scheduler is None
    assert spy.calls == [], "flag off must NOT call build_shadow_scheduler"
    assert "bot.universe.shadow_runtime" not in sys.modules, \
        "flag off must NOT import the bot.universe boundary"
    assert events == [(main.SHADOW_RUNTIME_DISABLED, "flag_off")]


def test_flag_off_no_sqlite_connect_no_migrate_no_dbfile(tmp_path, monkeypatch):
    """With the flag off, even a connect/migrate landmine must never fire and no DB appears."""
    def _boom(*a, **k):
        raise AssertionError("sqlite3.connect must not be called when the flag is off")
    monkeypatch.setattr(sqlite3, "connect", _boom)
    db = tmp_path / SAFE_DB

    rt = main.init_shadow_runtime(OFF, db_path=str(db),
                                  bars_provider=StubCompletedBarProvider(),
                                  completed_bar_provider=StubCompletedBarProvider())

    assert rt.ready is False and rt.scheduler is None
    assert not db.exists()
    assert list(tmp_path.iterdir()) == [], "flag off must create no files"


def test_flag_off_with_default_providers_is_noop():
    """The production wiring passes db_path/providers=None; flag off → ready False, no scheduler."""
    rt = main.init_shadow_runtime(OFF, db_path=None, bars_provider=None,
                                  completed_bar_provider=None)
    assert rt.ready is False and rt.reason == "flag_off" and rt.scheduler is None


# ════════════════════════════════════════════════════════════════════════════════════
# 3. Feature ON but not validly configured → fail closed (no DB touch, no crash)
# ════════════════════════════════════════════════════════════════════════════════════
def test_enabled_missing_db_path_fails_closed(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("no DB connect on the missing-path fail-closed path")
    monkeypatch.setattr(sqlite3, "connect", _boom)
    events, emit = _emit_recorder()

    rt = main.init_shadow_runtime(ON, db_path=None,
                                  bars_provider=StubCompletedBarProvider(),
                                  completed_bar_provider=StubCompletedBarProvider(), emit=emit)

    assert rt.ready is False and rt.reason == "shadow_db_path_missing" and rt.scheduler is None
    assert (main.SHADOW_RUNTIME_CONFIG_INVALID, "shadow_db_path_missing") in events
    assert (main.SHADOW_RUNTIME_NOT_STARTED, "shadow_db_path_missing") in events


@pytest.mark.parametrize("dbname", ["positions.db", "regime.db", "backtest.db",
                                    "learning_loop.db", "universe.db"])
def test_enabled_unsafe_db_path_fails_closed(tmp_path, monkeypatch, dbname):
    def _boom(*a, **k):
        raise AssertionError("no DB connect on the unsafe-path fail-closed path")
    monkeypatch.setattr(sqlite3, "connect", _boom)
    db = tmp_path / dbname
    events, emit = _emit_recorder()

    rt = main.init_shadow_runtime(ON, db_path=str(db),
                                  bars_provider=StubCompletedBarProvider(),
                                  completed_bar_provider=StubCompletedBarProvider(), emit=emit)

    assert rt.ready is False and rt.reason == "shadow_db_path_unsafe" and rt.scheduler is None
    assert not db.exists()
    assert (main.SHADOW_RUNTIME_DB_PATH_UNSAFE, "shadow_db_path_unsafe") in events


def test_enabled_missing_bars_provider_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no connect")))
    db = tmp_path / SAFE_DB
    events, emit = _emit_recorder()

    rt = main.init_shadow_runtime(ON, db_path=str(db), bars_provider=None,
                                  completed_bar_provider=StubCompletedBarProvider(), emit=emit)

    assert rt.ready is False and rt.reason == "bars_provider_missing" and rt.scheduler is None
    assert not db.exists()
    assert (main.SHADOW_RUNTIME_PROVIDER_MISSING, "bars_provider_missing") in events


def test_enabled_missing_completed_bar_provider_fails_closed(tmp_path):
    db = tmp_path / SAFE_DB
    rt = main.init_shadow_runtime(ON, db_path=str(db),
                                  bars_provider=StubCompletedBarProvider(),
                                  completed_bar_provider=None)
    assert rt.ready is False and rt.reason == "completed_bar_provider_missing"
    assert rt.scheduler is None and not db.exists()


def test_enabled_production_default_config_fails_closed():
    """The ACTUAL production wiring shape (no dynamic_universe_shadow config block): db_path None
    AND providers None. Fail-closed stops at the FIRST unmet gate — the missing shadow DB path —
    so the reason is shadow_db_path_missing (not provider). Scheduler None; nothing constructed."""
    rt = main.init_shadow_runtime(ON, db_path=None, bars_provider=None,
                                  completed_bar_provider=None)
    assert rt.ready is False and rt.reason == "shadow_db_path_missing" and rt.scheduler is None


def test_enabled_live_provider_without_approval_fails_closed(tmp_path):
    db = tmp_path / SAFE_DB
    rt = main.init_shadow_runtime(ON, db_path=str(db),
                                  bars_provider=StubCompletedBarProvider(is_live=True),
                                  completed_bar_provider=StubCompletedBarProvider())
    assert rt.ready is False and rt.reason == "live_provider_requires_approval"
    assert rt.scheduler is None and not db.exists()


def test_build_exception_fails_closed(tmp_path):
    """A provider/builder exception must fail closed (no crash, no scheduler)."""
    spy = _SpyBuilder(raises=RuntimeError("provider blew up"))
    events, emit = _emit_recorder()
    db = tmp_path / SAFE_DB

    rt = main.init_shadow_runtime(ON, db_path=str(db),
                                  bars_provider=StubCompletedBarProvider(),
                                  completed_bar_provider=StubCompletedBarProvider(),
                                  builder=spy, emit=emit)

    assert rt.ready is False and rt.reason == "build_exception" and rt.scheduler is None
    assert (main.SHADOW_RUNTIME_CONFIG_INVALID, "build_exception") in events
    assert not db.exists()


# ════════════════════════════════════════════════════════════════════════════════════
# 4. Feature ON + valid (stub builder) → ready, scheduler constructed (shadow-only)
# ════════════════════════════════════════════════════════════════════════════════════
def test_enabled_valid_returns_ready_scheduler():
    sched = _FakeScheduler()
    spy = _SpyBuilder(result=_Result(ok=True, scheduler=sched))
    events, emit = _emit_recorder()

    rt = main.init_shadow_runtime(ON, db_path=SAFE_DB,
                                  bars_provider=StubCompletedBarProvider(),
                                  completed_bar_provider=StubCompletedBarProvider(),
                                  builder=spy, emit=emit)

    assert rt.ready is True and rt.reason is None and rt.scheduler is sched
    assert len(spy.calls) == 1
    assert (main.SHADOW_RUNTIME_READY, None) in events


# ════════════════════════════════════════════════════════════════════════════════════
# 5. Per-cycle seam: guarded no-op in production; inert run with empty records otherwise
# ════════════════════════════════════════════════════════════════════════════════════
class _FakeBot:
    """A minimal stand-in to exercise TradingBot's seam methods without a broker/config."""
    def __init__(self, shadow_runtime):
        self.shadow_runtime = shadow_runtime
        self._shadow_records_fn = None

    def _shadow_canonical_records(self):
        return main.TradingBot._shadow_canonical_records(self)


def test_seam_is_noop_when_no_scheduler():
    bot = _FakeBot(main.ShadowRuntimeStartup(ready=False, reason="flag_off", scheduler=None))
    # Must not raise, must not attempt to run anything.
    main.TradingBot._maybe_run_shadow_cycle(bot)


def test_seam_runs_scheduler_with_empty_records_when_ready():
    sched = _FakeScheduler()
    bot = _FakeBot(main.ShadowRuntimeStartup(ready=True, scheduler=sched))
    main.TradingBot._maybe_run_shadow_cycle(bot)
    assert sched.runs == [[]], "seam must feed an EMPTY record set (no candidate ingestion)"


def test_canonical_records_default_is_empty():
    bot = _FakeBot(None)
    assert main.TradingBot._shadow_canonical_records(bot) == []


def test_seam_swallows_scheduler_exception():
    class _Boom:
        def maybe_run(self, records):
            raise RuntimeError("scheduler error")
    bot = _FakeBot(main.ShadowRuntimeStartup(ready=True, scheduler=_Boom()))
    main.TradingBot._maybe_run_shadow_cycle(bot)  # must not propagate


# ════════════════════════════════════════════════════════════════════════════════════
# 6. Broker-free / order-safety / api_server untouched (static structural proofs)
# ════════════════════════════════════════════════════════════════════════════════════
def test_shadow_wiring_modules_import_no_broker_and_no_fetch_bars():
    """The shadow wiring path (W1/W2 boundary) imports no broker and calls no fetch_bars."""
    import bot.universe.shadow_runtime as sr_mod
    import bot.universe.bar_provider as bp_mod
    forbidden_imports = ("import bot.brokers", "from bot.brokers", "ib_insync", "trading_ig",
                         "bot.connection", "place_order")
    for mod in (sr_mod, bp_mod):
        src = pathlib.Path(mod.__file__).read_text()
        for banned in forbidden_imports:
            assert banned not in src, f"{mod.__name__} must not reference {banned!r}"
        # A docstring may NAME broker.fetch_bars as the thing the shadow path must NOT use;
        # what is forbidden is a real CALL — ``.fetch_bars(``.
        assert ".fetch_bars(" not in src, f"{mod.__name__} calls broker.fetch_bars"


def test_wiring_constructs_no_order_object():
    """The seam only calls scheduler.maybe_run([]) — it constructs no order/broker object.
    Proven by driving the seam with a fake scheduler that records calls (no order created)."""
    sched = _FakeScheduler()
    bot = _FakeBot(main.ShadowRuntimeStartup(ready=True, scheduler=sched))
    main.TradingBot._maybe_run_shadow_cycle(bot)
    # The only thing handed to the scheduler is an empty list — no order objects anywhere.
    assert sched.runs == [[]]


def test_api_server_untouched_by_wiring():
    """api_server.py must contain no Dynamic Universe shadow runtime references."""
    src = API_SERVER_PY.read_text()
    for banned in ("shadow_runtime", "build_shadow_scheduler", "init_shadow_runtime",
                   "dynamic_universe", "CompletedBarProvider"):
        assert banned not in src, f"api_server.py must not reference {banned!r}"


def test_main_loop_calls_the_seam():
    """The per-cycle call site is wired into the main loop (so the wiring is reviewable)."""
    src = MAIN_PY.read_text()
    assert "self._maybe_run_shadow_cycle()" in src
