"""§6/§7 — offline end-to-end shadow rehearsal: deterministic operational output,
full-lifecycle coverage, and a guard that NO performance metric is ever produced."""
import tests.universe.rehearsal as rehearsal_mod
from tests.universe.rehearsal import format_report, run_rehearsal

# Forbidden outcome vocabulary — the rehearsal reports operations, never performance.
FORBIDDEN = ("return", "pnl", "p&l", "profit", "sharpe", "winner", "loser",
             "ranking", "rank_by_performance", "alpha", "drawdown")


def test_rehearsal_is_deterministic(tmp_path):
    da, db = tmp_path / "a", tmp_path / "b"
    da.mkdir(); db.mkdir()
    a = run_rehearsal(str(da / "universe.db"))
    b = run_rehearsal(str(db / "universe.db"))
    assert a == b                       # identical operational counts across runs


def test_rehearsal_runs_are_isolated_no_shared_fx_state(tmp_path):
    """P3 cleanup: the FX fixture is created FRESH per run, so repeated run_rehearsal()
    calls in one process are order-independent with no accumulated provider-call state."""
    # No module-level mutable FX provider exists to accumulate call history.
    assert not hasattr(rehearsal_mod, "_FX")
    # _make_fx() yields a brand-new provider each call (distinct object, empty call log).
    fx_a, fx_b = rehearsal_mod._make_fx(), rehearsal_mod._make_fx()
    assert fx_a is not fx_b
    assert fx_a.calls == [] and fx_b.calls == []
    # Three back-to-back runs in the SAME process produce identical operational output
    # (no mutation leaking between runs, order cannot influence the result).
    outs = [run_rehearsal(str(tmp_path / f"u{i}.db")) for i in range(3)]
    assert outs[0] == outs[1] == outs[2]


def test_rehearsal_exercises_full_lifecycle(tmp_path):
    c = run_rehearsal(str(tmp_path / "universe.db"))
    st = c["state_transitions"]
    # every lifecycle state was reached organically
    for state in ("WATCHLIST", "ENTRY_ELIGIBLE", "POSITION_OPEN", "EXIT_ONLY",
                  "COOLDOWN", "DATA_INELIGIBLE", "ADMIN_PAUSED", "HARD_DISABLED"):
        assert st.get(state, 0) > 0, f"state {state} never reached"
    # candidate sources all present
    assert set(c["candidates_by_source"]) == {"AUTO", "TTI", "MANUAL"}
    assert c["candidate_expirations"] == 2                 # TTI + MANUAL past TTL
    # contention paths
    assert c["slot_rejections"] >= 1
    assert c["sector_cap_rejections"] >= 1
    assert c["heat_limit_rejections"] >= 1                 # via the heat-probe
    # suppressions
    assert c["hard_disabled_suppressions"] >= 1
    assert c["admin_pause_suppressions"] >= 1
    # position lifecycle
    assert c["position_open_transitions"] >= 1
    assert c["exit_only_transitions"] >= 1
    assert c["cooldown_transitions"] >= 3                  # E + E+1..E+3 blocking
    # safety seams
    assert c["corp_action_unknown_warnings"] >= 1
    assert c["position_status_unknown_safe"] >= 1
    assert c["missing_bar_skips"] >= 1
    assert c["idempotent_skips"] >= 1
    # routing invariants
    assert c["ibkr_primary_assignments"] >= 1
    assert c["ig_routing_blocked"] >= 1
    assert c["migration_idempotent_rerun"] is True


def test_rehearsal_reports_no_performance_metrics(tmp_path):
    c = run_rehearsal(str(tmp_path / "universe.db"))
    blob = (" ".join(map(str, c.keys())) + " " + format_report(c)).lower()
    for term in FORBIDDEN:
        assert term not in blob, f"rehearsal leaked a performance term: {term!r}"
