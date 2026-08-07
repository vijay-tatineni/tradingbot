"""Daily shadow scheduler: post-close gating, multi-timezone awareness, flag-off
no-op, restart-safe idempotency, no incomplete-bar evaluation, and the §5
completed-bar-availability gate (DST / holiday / half-day / late-bar handling)."""
from datetime import datetime, timezone

from bot.universe.evaluator import ShadowEvaluator
from bot.universe.registry import Registry
from bot.universe.scheduler import DailyUniverseScheduler
from bot.universe.seed import canonical_id, seed_registry
from tests.universe._fixtures import OFF, ON, SpyProvider, inst, make_bars, write_configs

US = canonical_id("AAPL", "USD", "NASDAQ")
LSE = canonical_id("BARC", "GBP", "SMART")


def _seed_us_lse(tmp_path):
    p1, p2 = write_configs(tmp_path, [
        inst("AAPL", currency="USD", exchange="NASDAQ"),
        inst("BARC", currency="GBP", exchange="SMART"),
    ], [])
    db = str(tmp_path / "universe.db")
    seed_registry(db, p1, p2)
    return db


def _sched(db, flags, now, bar_available_fn=None):
    reg = Registry(db)
    ev = ShadowEvaluator(reg, SpyProvider({US: {"bars": make_bars(), "corp_action_status": "ok",
                                                "sector": "Tech"}}), flags, equity=100_000)
    return DailyUniverseScheduler(ev, flags, now_fn=lambda: now,
                                  bar_available_fn=bar_available_fn), reg


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


# ── §5: DST conservatism — the fixed UTC gate is post-close in BOTH DST regimes ──

def test_us_dst_gate_is_conservative_both_regimes(tmp_path):
    # US cash close is 21:00 UTC (EST/winter) and 20:00 UTC (EDT/summer); gate=22:00.
    db = _seed_us_lse(tmp_path)
    # Winter date (Jan, EST): at 21:30 UTC the bar is closed but the gate (22:00) has
    # NOT fired — conservative wait, never an early evaluation.
    sched, reg = _sched(db, ON, datetime(2026, 1, 15, 21, 30, tzinfo=timezone.utc))
    assert US not in sched.maybe_run(reg.all_canonical()).get("scheduled_ids", [])
    # Summer date (Jul, EDT): close was 20:00; at 22:00 UTC the gate fires (post-close).
    sched2, reg2 = _sched(db, ON, datetime(2026, 7, 15, 22, 0, tzinfo=timezone.utc))
    assert US in sched2.maybe_run(reg2.all_canonical())["scheduled_ids"]


def test_uk_us_dst_offset_divergence_window(tmp_path):
    # Late-Oct window: UK has returned to GMT (last Sun Oct) but US is still on EDT
    # (until 1st Sun Nov). Offsets differ, yet the fixed UTC gates stay conservative.
    db = _seed_us_lse(tmp_path)
    # 2026-10-28: at 18:00 UTC LSE (gate 17) is complete; US (gate 22) is not.
    sched, reg = _sched(db, ON, datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc))
    r = sched.maybe_run(reg.all_canonical())
    assert r["scheduled_ids"] == [LSE]


def test_holiday_missing_bar_is_skipped_and_retried(tmp_path):
    # Exchange holiday: the time gate passes but NO completed bar exists. The injected
    # availability check returns False → instrument skipped, no history written, retried.
    db = _seed_us_lse(tmp_path)
    now = datetime(2026, 6, 10, 23, 0, tzinfo=timezone.utc)
    available = {US: False, LSE: True}
    sched, reg = _sched(db, ON, now,
                        bar_available_fn=lambda rec, td: available[rec["canonical_instrument_id"]])
    r = sched.maybe_run(reg.all_canonical())
    assert r["scheduled_ids"] == [LSE]            # only the market with a real bar ran
    assert r["missing_bar_skipped"] == [US]
    # the skipped instrument wrote NO history → it is retried on the next cycle
    assert reg.history_count(US) == 0
    # bar arrives later: a subsequent cycle evaluates it idempotently
    available[US] = True
    r2 = sched.maybe_run(reg.all_canonical())
    assert US in r2["scheduled_ids"] and r2["evaluated"] == 1
    assert reg.history_count(US) == 1


def test_late_bar_then_available_retry(tmp_path):
    # All instruments' bars are late on the first cycle (none available) → no_op + skip.
    db = _seed_us_lse(tmp_path)
    now = datetime(2026, 6, 10, 23, 0, tzinfo=timezone.utc)
    state = {"ready": False}
    sched, reg = _sched(db, ON, now, bar_available_fn=lambda rec, td: state["ready"])
    r = sched.maybe_run(reg.all_canonical())
    assert r["ran"] is False and r["reason"] == "no_completed_sessions"
    assert set(r["missing_bar_skipped"]) == {US, LSE}
    # bars arrive → retry succeeds
    state["ready"] = True
    r2 = sched.maybe_run(reg.all_canonical())
    assert r2["evaluated"] == 2 and r2["missing_bar_skipped"] == []


def test_bar_availability_error_is_fail_safe_skip(tmp_path):
    # A raising availability check must be treated as 'unavailable' (skip), never as a
    # silent green light to evaluate on a possibly-incomplete bar.
    db = _seed_us_lse(tmp_path)
    now = datetime(2026, 6, 10, 23, 0, tzinfo=timezone.utc)

    def boom(rec, td):
        raise RuntimeError("availability source down")

    sched, reg = _sched(db, ON, now, bar_available_fn=boom)
    r = sched.maybe_run(reg.all_canonical())
    assert r["ran"] is False and set(r["missing_bar_skipped"]) == {US, LSE}


def test_half_day_early_close_still_gated_after_close(tmp_path):
    # On a half-day the close is EARLIER, so the completed bar is available SOONER; the
    # fixed gate (which sits after the regular close) therefore remains safe — it can
    # only ever run late. At 22:00 UTC both markets are post-close with bars present.
    db = _seed_us_lse(tmp_path)
    now = datetime(2026, 11, 27, 22, 0, tzinfo=timezone.utc)   # US half-day (day after Thanksgiving)
    sched, reg = _sched(db, ON, now, bar_available_fn=lambda rec, td: True)
    r = sched.maybe_run(reg.all_canonical())
    assert set(r["scheduled_ids"]) == {US, LSE}
