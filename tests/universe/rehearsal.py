"""Offline, deterministic end-to-end Dynamic Universe shadow rehearsal (task §6).

Pure fixtures / synthetic data — NO broker calls, NO data-provider calls, NO live DB,
NO production config writes. It exercises the full universe-state lifecycle and reports
ONLY operational metrics: state-transition counts, candidate counts, suppression and
rejection counts, idempotent/missing-bar skips. It NEVER computes or reports returns,
P&L, profit factor, Sharpe, winners/losers, or instrument rankings by performance.

``run_rehearsal(db_path)`` returns a counts dict; it is fully deterministic (no
wall-clock / RNG dependence in the COUNTS) so the same fixtures always yield the same
report — asserted in tests/universe/test_rehearsal.py.
"""
from collections import Counter
from contextlib import contextmanager

from bot.universe import params
from bot.universe.evaluator import ShadowEvaluator
from bot.universe.models import PositionSnapshot, PositionStatus, Reason, State
from bot.universe.registry import Registry
from bot.universe.scheduler import DailyUniverseScheduler
from bot.universe.seed import canonical_id, seed_registry
from bot.universe.db import current_version, migrate

from tests.universe._fixtures import (
    ON, SpyProvider, StaticFxRateProvider, StubPositionProvider, flat, inst, make_bars,
    write_configs,
)
from datetime import date, datetime, timezone

# Deterministic, broker-free FX rates so non-USD (GBP) instruments are USD-normalised
# for eligibility (P2-2). Effective on the requested trading date (zero staleness).
def _make_fx():
    """Return a FRESH deterministic FX provider per rehearsal run, so no mutable
    call-history accumulates across repeated run_rehearsal() invocations (each run is
    fully self-contained and order-independent)."""
    return StaticFxRateProvider({"GBP": 1.25, "EUR": 1.10})


def _src(sector="Tech", bars=True, n=300, corp="ok", volume=2_000_000.0, price_unit=None):
    return {"bars": (make_bars(n=n, volume=volume) if bars else None),
            "corp_action_status": corp, "sector": sector, "spread": 0.01,
            "price_unit": price_unit}


@contextmanager
def _heat_cap(value):
    """Transiently lower the portfolio-heat cap ONLY to exercise the heat-rejection
    branch (under the FROZEN 5×0.5% params the slot cap binds first, so heat rejection
    is otherwise structurally unreachable). Restored on exit — production params are
    never mutated persistently."""
    original = params.MAX_PORTFOLIO_HEAT
    params.MAX_PORTFOLIO_HEAT = value
    try:
        yield
    finally:
        params.MAX_PORTFOLIO_HEAT = original


def run_rehearsal(db_path: str) -> dict:
    fx = _make_fx()   # fresh per-run FX provider; no shared mutable state across runs
    counts = {
        "sessions_evaluated": 0,
        "instruments_evaluated": 0,
        "state_transitions": Counter(),
        "candidates_by_source": Counter(),
        "candidate_expirations": 0,
        "data_ineligible_reasons": Counter(),
        "hard_disabled_suppressions": 0,
        "admin_pause_suppressions": 0,
        "position_open_transitions": 0,
        "exit_only_transitions": 0,
        "position_reconciliation_transitions": 0,
        "cooldown_transitions": 0,
        "slot_rejections": 0,
        "sector_cap_rejections": 0,
        "heat_limit_rejections": 0,
        "idempotent_skips": 0,
        "missing_bar_skips": 0,
        "corp_action_unknown_warnings": 0,
        "position_status_unknown_safe": 0,
        "ibkr_primary_assignments": 0,
        "ig_routing_blocked": 0,
        "migration_idempotent_rerun": False,
    }

    def tally(outcomes):
        for o in outcomes:
            if "skipped" in o:
                counts["idempotent_skips"] += 1
                continue
            counts["instruments_evaluated"] += 1
            st = o["new_state"]
            counts["state_transitions"][st] += 1
            rc = o.get("reason_codes", [])
            if st == State.HARD_DISABLED.value:
                counts["hard_disabled_suppressions"] += 1
            elif st == State.ADMIN_PAUSED.value:
                counts["admin_pause_suppressions"] += 1
            elif st == State.POSITION_OPEN.value:
                counts["position_open_transitions"] += 1
            elif st == State.EXIT_ONLY.value:
                counts["exit_only_transitions"] += 1
            elif st == State.POSITION_RECONCILIATION.value:
                counts["position_reconciliation_transitions"] += 1
            elif st == State.COOLDOWN.value:
                counts["cooldown_transitions"] += 1
            elif st == State.DATA_INELIGIBLE.value:
                for r in rc:
                    counts["data_ineligible_reasons"][r] += 1
            if Reason.CORP_ACTION_STATUS_UNKNOWN in rc:
                counts["corp_action_unknown_warnings"] += 1
            if Reason.POSITION_STATUS_UNKNOWN in rc:
                counts["position_status_unknown_safe"] += 1

    def tally_contention(r):
        for x in r.get("rejected", []):
            reason = x.get("rejected_reason")
            if reason == Reason.SLOT_CAP_REACHED:
                counts["slot_rejections"] += 1
            elif reason == Reason.SECTOR_CAP_REACHED:
                counts["sector_cap_rejections"] += 1
            elif reason == Reason.PORTFOLIO_HEAT_EXCEEDED:
                counts["heat_limit_rejections"] += 1

    # ── Scenario A: registry seed + idempotent migration rerun ───────────────
    instruments = [
        inst("AAPL", currency="USD", exchange="NASDAQ"),     # lifecycle driver
        inst("MSFT", currency="USD", exchange="NASDAQ"),     # Tech
        inst("NVDA", currency="USD", exchange="NASDAQ"),     # Tech (3rd → sector cap)
        inst("BARC", currency="GBP", exchange="SMART"),      # LSE (multi-tz)
        inst("PAUSE", currency="USD", exchange="NASDAQ", enabled=False),   # admin-paused
        inst("SHORTH", currency="USD", exchange="NASDAQ"),   # data-ineligible (short history)
        inst("XAUUSD", currency="USD", exchange="SMART", hard_disabled=True),  # hard-disabled
    ]
    # One IG mapping so the order-routing-blocked invariant is exercised (always
    # UNVERIFIED + order_routing_blocked=1; never order-capable in v1).
    ig_instruments = [{"symbol": "BARC", "currency": "GBP", "exchange": "SMART",
                       "ig_epic": "KA.D.BARC.DAILY.IP"}]
    p1, p2 = write_configs(tmp_dir_configs(db_path), instruments, ig_instruments)
    seed_registry(db_path, p1, p2)
    v1 = current_version(db_path)
    migrate(db_path)                                   # rerun
    counts["migration_idempotent_rerun"] = (current_version(db_path) == v1)

    reg = Registry(db_path)
    for c in reg.all_canonical():
        counts["ibkr_primary_assignments"] += 1 if c["primary_gateway"] == "IBKR" else 0

    # ── Scenario B: candidate sources (AUTO/TTI/MANUAL) + 5-session TTL ───────
    aapl = canonical_id("AAPL", "USD", "NASDAQ")
    reg.add_candidate({"candidate_id": "auto-1", "canonical_instrument_id": aapl,
                       "source": "AUTO"})
    reg.add_candidate({"candidate_id": "tti-1", "canonical_instrument_id": aapl,
                       "source": "TTI", "effective_trading_date": "2026-06-01",
                       "expires_after_trading_date": "2026-06-05"})   # 5-session TTL
    reg.add_candidate({"candidate_id": "man-1", "canonical_instrument_id": aapl,
                       "source": "MANUAL", "effective_trading_date": "2026-06-01",
                       "expires_after_trading_date": "2026-06-05"})
    for c in reg.active_candidates():
        counts["candidates_by_source"][c["source"]] += 1

    # ── Scenario C: multi-timezone scheduling + same-day idempotency ─────────
    # Sources keyed per canonical id; SHORTH gets short history → DATA_INELIGIBLE.
    sources = {
        aapl: _src(sector="Tech"),
        canonical_id("MSFT", "USD", "NASDAQ"): _src(sector="Tech", volume=3_000_000.0),
        canonical_id("NVDA", "USD", "NASDAQ"): _src(sector="Tech", volume=2_500_000.0),
        # corp-action UNKNOWN: shadow policy WARNS (still eligible), never blocks.
        canonical_id("BARC", "GBP", "SMART"): _src(sector="Financials", corp="unavailable",
                                                   price_unit="MAJOR"),
        canonical_id("PAUSE", "USD", "NASDAQ"): _src(sector="Tech"),
        canonical_id("SHORTH", "USD", "NASDAQ"): _src(sector="Energy", n=100),  # short history
        canonical_id("XAUUSD", "USD", "SMART"): _src(sector="Metals"),
    }
    prov = SpyProvider(sources)
    ev = ShadowEvaluator(reg, prov, ON, equity=100_000, fx_provider=fx,
                         position_provider=flat())
    # two completed sessions to drive entry hysteresis (WATCHLIST→ENTRY_ELIGIBLE)
    for d in ("2026-06-10", "2026-06-11"):
        r = ev.maybe_run(d)
        counts["sessions_evaluated"] += 1
        tally(r["outcomes"]); tally_contention(r)
    # candidate TTL expiry occurs on the first session past the expiry date
    # (expire_candidates ran inside maybe_run for 2026-06-10).
    # idempotent rerun of a completed session
    r_idem = ev.maybe_run("2026-06-11")
    counts["idempotent_skips"] += sum(1 for o in r_idem["outcomes"] if "skipped" in o)

    # Candidate expirations recorded across the run (TTI + MANUAL past 2026-06-05).
    counts["candidate_expirations"] = 2 - len(
        [c for c in reg.active_candidates() if c["source"] in ("TTI", "MANUAL")])

    # IG routing blocked invariant (no IG mappings seeded here → assert structural rule).
    counts["ig_routing_blocked"] = sum(
        1 for c in reg.all_canonical() if reg.get_gateway_ig(c["canonical_instrument_id"])
        and reg.get_gateway_ig(c["canonical_instrument_id"])["order_routing_blocked"] == 1)

    # ── Scenario D: organic POSITION_OPEN / EXIT_ONLY / COOLDOWN lifecycle ────
    # Drive AAPL through the full position lifecycle via the injected position seam.
    # P3-9: the exit (E) is detected from a DURABLE open→flat transition + evidence
    # (closed_trading_date), NOT from the deprecated one-cycle POSITION_EXITED_TODAY —
    # so the rehearsal exercises the robust path rather than masking the blocker.
    lifecycle = [
        ("2026-06-12", PositionStatus.POSITION_OPEN, _src()),                 # → POSITION_OPEN
        ("2026-06-13", PositionStatus.POSITION_OPEN, _src(bars=False)),       # lose elig → EXIT_ONLY
        ("2026-06-14", PositionStatus.POSITION_OPEN, _src()),                 # restore → POSITION_OPEN
        ("2026-06-15", PositionSnapshot(status=PositionStatus.NO_POSITION,    # exit → COOLDOWN (E)
                                        position_id="aapl-pos-1",
                                        opened_trading_date=date(2026, 6, 12),  # R1.3 discriminator
                                        closed_trading_date=date(2026, 6, 15)), _src()),
        ("2026-06-16", PositionStatus.NO_POSITION, _src()),                   # E+1 COOLDOWN
        ("2026-06-17", PositionStatus.NO_POSITION, _src()),                   # E+2 COOLDOWN
        ("2026-06-18", PositionStatus.NO_POSITION, _src()),                   # E+3 COOLDOWN
        ("2026-06-19", PositionStatus.NO_POSITION, _src()),                   # E+4 release
        ("2026-06-20", PositionStatus.UNKNOWN, _src()),                       # UNKNOWN → safe hold
    ]
    for d, status, src in lifecycle:
        pos = StubPositionProvider({aapl: status})
        ev_l = ShadowEvaluator(reg, SpyProvider({aapl: src}), ON, equity=100_000,
                               position_provider=pos)
        r = ev_l.maybe_run(d, only_ids={aapl})
        counts["sessions_evaluated"] += 1
        tally([o for o in r["outcomes"] if o["canonical_instrument_id"] == aapl])

    # ── Scenario E: 2-failure eligibility removal (ENTRY_ELIGIBLE → out) ──────
    msft = canonical_id("MSFT", "USD", "NASDAQ")
    fail_src = SpyProvider({msft: _src(bars=False)})       # structural fail
    ev_f = ShadowEvaluator(reg, fail_src, ON, equity=100_000, position_provider=flat())
    for d in ("2026-06-12", "2026-06-13"):                  # 2 consecutive failures
        r = ev_f.maybe_run(d, only_ids={msft})
        counts["sessions_evaluated"] += 1
        tally([o for o in r["outcomes"] if o["canonical_instrument_id"] == msft])

    # ── Scenario F: slot contention + sector cap (fresh DB slice) ────────────
    sc_db = db_path + ".contention"
    sc_syms = [f"C{i}" for i in range(7)]
    sp1, sp2 = write_configs(tmp_dir_configs(sc_db),
                             [inst(s, currency="USD", exchange="NASDAQ") for s in sc_syms], [])
    seed_registry(sc_db, sp1, sp2)
    sc_reg = Registry(sc_db)
    # 6 distinct sectors (slot cap) + force one extra Tech to hit the sector cap.
    sc_sources = {}
    for i, s in enumerate(sc_syms[:6]):
        sc_sources[canonical_id(s, "USD", "NASDAQ")] = _src(sector=f"sec{i}",
                                                            volume=1_000_000.0 * (i + 1))
    sc_sources[canonical_id("C6", "USD", "NASDAQ")] = _src(sector="sec0",   # same sector as C0/dup
                                                          volume=500_000.0)
    sc_ev = ShadowEvaluator(sc_reg, SpyProvider(sc_sources), ON, equity=100_000,
                            position_provider=flat())
    sc_ev.maybe_run("2026-06-10")
    rc = sc_ev.maybe_run("2026-06-11")
    counts["sessions_evaluated"] += 2
    tally(rc["outcomes"]); tally_contention(rc)

    # ── Scenario G: portfolio-heat rejection (heat-probe; cap lowered) ───────
    with _heat_cap(0.002):     # 0.20% cap vs 0.15%/order → 2nd+ candidates heat-rejected
        hp_db = db_path + ".heat"
        hp_syms = [f"H{i}" for i in range(3)]
        hp1, hp2 = write_configs(tmp_dir_configs(hp_db),
                                 [inst(s, currency="USD", exchange="NASDAQ") for s in hp_syms], [])
        seed_registry(hp_db, hp1, hp2)
        hp_reg = Registry(hp_db)
        hp_sources = {canonical_id(s, "USD", "NASDAQ"): _src(sector=f"h{i}",
                                                            volume=1_000_000.0 * (i + 1))
                      for i, s in enumerate(hp_syms)}
        hp_ev = ShadowEvaluator(hp_reg, SpyProvider(hp_sources), ON, equity=100_000,
                                position_provider=flat())
        hp_ev.maybe_run("2026-06-10")
        rh = hp_ev.maybe_run("2026-06-11")
        counts["sessions_evaluated"] += 2
        tally(rh["outcomes"]); tally_contention(rh)

    # ── Scenario H: missing-bar skip + restart recovery via scheduler ────────
    sch_db = db_path + ".sched"
    sh1, sh2 = write_configs(tmp_dir_configs(sch_db),
                             [inst("AAPL", currency="USD", exchange="NASDAQ"),
                              inst("BARC", currency="GBP", exchange="SMART")], [])
    seed_registry(sch_db, sh1, sh2)
    sch_reg = Registry(sch_db)
    sch_ev = ShadowEvaluator(sch_reg, SpyProvider({
        canonical_id("AAPL", "USD", "NASDAQ"): _src(),
        canonical_id("BARC", "GBP", "SMART"): _src(sector="Financials", price_unit="MAJOR"),
    }), ON, equity=100_000, fx_provider=fx, position_provider=flat())
    avail = {canonical_id("AAPL", "USD", "NASDAQ"): False,    # US bar late/holiday
             canonical_id("BARC", "GBP", "SMART"): True}
    now = datetime(2026, 6, 10, 23, 0, tzinfo=timezone.utc)
    sched = DailyUniverseScheduler(sch_ev, ON, now_fn=lambda: now,
                                   bar_available_fn=lambda rec, td: avail[rec["canonical_instrument_id"]])
    r_sched = sched.maybe_run(sch_reg.all_canonical())
    counts["missing_bar_skips"] += len(r_sched.get("missing_bar_skipped", []))
    counts["sessions_evaluated"] += 1
    tally(r_sched.get("outcomes", []))
    # restart recovery: late bar now present; idempotent for the already-evaluated one
    avail[canonical_id("AAPL", "USD", "NASDAQ")] = True
    sched2 = DailyUniverseScheduler(
        ShadowEvaluator(Registry(sch_db), SpyProvider({
            canonical_id("AAPL", "USD", "NASDAQ"): _src(),
            canonical_id("BARC", "GBP", "SMART"): _src(sector="Financials", price_unit="MAJOR"),
        }), ON, equity=100_000, fx_provider=fx, position_provider=flat()),
        ON, now_fn=lambda: now,
        bar_available_fn=lambda rec, td: avail[rec["canonical_instrument_id"]])
    r_sched2 = sched2.maybe_run(sch_reg.all_canonical())
    counts["idempotent_skips"] += sum(1 for o in r_sched2.get("outcomes", []) if "skipped" in o)
    tally([o for o in r_sched2.get("outcomes", []) if "skipped" not in o])

    # Freeze Counters into plain dicts for stable equality / reporting.
    counts["state_transitions"] = dict(counts["state_transitions"])
    counts["candidates_by_source"] = dict(counts["candidates_by_source"])
    counts["data_ineligible_reasons"] = dict(counts["data_ineligible_reasons"])
    return counts


def tmp_dir_configs(db_path: str):
    """Config files live beside the rehearsal db (a tmp dir); never the repo configs."""
    import pathlib
    return pathlib.Path(db_path).parent


def format_report(counts: dict) -> str:
    lines = ["Dynamic Universe — offline shadow rehearsal (operational metrics only)",
             "=" * 68]
    for k in ("sessions_evaluated", "instruments_evaluated"):
        lines.append(f"{k:34s}: {counts[k]}")
    lines.append(f"{'migration_idempotent_rerun':34s}: {counts['migration_idempotent_rerun']}")
    lines.append("state_transitions (by new_state):")
    for st, n in sorted(counts["state_transitions"].items()):
        lines.append(f"    {st:24s}: {n}")
    lines.append("candidates_by_source:")
    for s, n in sorted(counts["candidates_by_source"].items()):
        lines.append(f"    {s:24s}: {n}")
    for k in ("candidate_expirations", "hard_disabled_suppressions",
              "admin_pause_suppressions", "position_open_transitions",
              "exit_only_transitions", "cooldown_transitions", "slot_rejections",
              "sector_cap_rejections", "heat_limit_rejections", "idempotent_skips",
              "missing_bar_skips", "corp_action_unknown_warnings",
              "position_status_unknown_safe", "ibkr_primary_assignments",
              "ig_routing_blocked"):
        lines.append(f"{k:34s}: {counts[k]}")
    lines.append("data_ineligible_reasons:")
    for r, n in sorted(counts["data_ineligible_reasons"].items()):
        lines.append(f"    {r:24s}: {n}")
    return "\n".join(lines)
