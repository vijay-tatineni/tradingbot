"""Daily shadow scheduler: post-close gating, multi-timezone awareness, flag-off
no-op, restart-safe idempotency, no incomplete-bar evaluation."""
from datetime import datetime, timezone

from bot.universe.evaluator import ShadowEvaluator
from bot.universe.registry import Registry
from bot.universe.scheduler import DailyUniverseScheduler
from bot.universe.seed import canonical_id, seed_registry
from tests.universe._fixtures import OFF, ON, SpyProvider, inst, write_configs


def _seed_us_lse(tmp_path):
    p1, p2 = write_configs(tmp_path, [
        inst("AAPL", currency="USD", exchange="NASDAQ"),
        inst("BARC", currency="GBP", exchange="SMART"),
    ], [])
    db = str(tmp_path / "universe.db")
    seed_registry(db, p1, p2)
    return db


def _sched(db, flags, now):
    reg = Registry(db)
    ev = ShadowEvaluator(reg, SpyProvider({}), flags, equity=100_000)
    return DailyUniverseScheduler(ev, flags, now_fn=lambda: now), reg


def test_flag_off_no_op(tmp_path):
    db = _seed_us_lse(tmp_path)
    sched, reg = _sched(db, OFF, datetime(2026, 6, 10, 23, 0, tzinfo=timezone.utc))
    r = sched.maybe_run(reg.all_canonical())
    assert r == {"ran": False, "reason": "flag_off"}


def test_no_completed_sessions_before_close(tmp_path):
    db = _seed_us_lse(tmp_path)
    sched, reg = _sched(db, ON, datetime(2026, 6, 10, 10, 0, tzinfo=timezone.utc))
    r = sched.maybe_run(reg.all_canonical())
    assert r["ran"] is False and r["reason"] == "no_completed_sessions"


def test_multi_timezone_only_lse_completed_at_18utc(tmp_path):
    db = _seed_us_lse(tmp_path)
    sched, reg = _sched(db, ON, datetime(2026, 6, 10, 18, 0, tzinfo=timezone.utc))
    r = sched.maybe_run(reg.all_canonical())
    # LSE (post-close 17:00) is complete; US (22:00) is not yet.
    assert r["scheduled_ids"] == [canonical_id("BARC", "GBP", "SMART")]
    assert r["evaluated"] == 1


def test_both_markets_after_us_close(tmp_path):
    db = _seed_us_lse(tmp_path)
    sched, reg = _sched(db, ON, datetime(2026, 6, 10, 23, 0, tzinfo=timezone.utc))
    r = sched.maybe_run(reg.all_canonical())
    assert set(r["scheduled_ids"]) == {
        canonical_id("AAPL", "USD", "NASDAQ"), canonical_id("BARC", "GBP", "SMART")}
    assert r["evaluated"] == 2


def test_scheduler_idempotent_on_rerun(tmp_path):
    db = _seed_us_lse(tmp_path)
    now = datetime(2026, 6, 10, 23, 0, tzinfo=timezone.utc)
    sched, reg = _sched(db, ON, now)
    records = reg.all_canonical()
    assert sched.maybe_run(records)["evaluated"] == 2
    assert sched.maybe_run(records)["evaluated"] == 0   # restart-safe / no duplicate work
