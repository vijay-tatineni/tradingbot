# Test Placement Plan

Tracking deferred invariant and regression tests across PRs.
Each test listed in §16.2 (invariants) and §16.6 (regressions) must land in exactly one PR.
This document is the source of truth for verifying nothing slips through.

---

## Invariant Tests (§16.2) — 9 total

| Test file | Target PR | Status | Dependencies |
|---|---|---|---|
| `test_routing_noop_never_allows.py` | PR 1 | ✅ Landed | Router |
| `test_cache_hit_field_present.py` | PR 1 | ✅ Landed | RegimeClassification dataclass |
| `test_no_redundant_flags.py` | PR 1 | ✅ Landed | FeatureFlags |
| `test_overlay_hard_failure_semantics.py` | PR 2 | Pending | Degradation framework, InstrumentPauseRegistry |
| `test_overlay_never_blocks_exits.py` | PR 2 | Pending | Overlay system, exit path |
| `test_entry_regime_exit_contract.py` | PR 3 | Pending | Position metadata tagger, exit policy, get_exit_engine |
| `test_fill_id_composite_key.py` | PR 3 | Pending | Position metadata persistence layer |
| `test_mixed_strategy_fills_raises.py` | PR 3 | Pending | get_exit_engine with assertion on mixed strategies |
| `test_shadow_isolation.py` | PR 3 | Pending | Shadow simulator, counterfactual logger, shadow_* tables |

## Regression Tests (§16.6) — 8 total

| Test file | Target PR | Status | Dependencies |
|---|---|---|---|
| `test_v2_fix_redundant_flag_removed.py` | PR 1 | ✅ Landed | FeatureFlags |
| `test_v2_fix_cache_hit_on_dataclass.py` | PR 1 | ✅ Landed | RegimeClassification dataclass |
| `test_v2_fix_routing_unambiguous.py` | PR 1 | ✅ Landed | Router |
| `test_v2_fix_overlay_fail_safe.py` | PR 2 | Pending | Degradation framework, overlay hard-failure |
| `test_v2_fix_partial_fills_composite_key.py` | PR 3 | Pending | Position metadata persistence |
| `test_v2_fix_exit_contract.py` | PR 3 | Pending | Exit policy enforcement, get_exit_engine |
| `test_v2_fix_no_mixed_strategy_fills.py` | PR 3 | Pending | get_exit_engine assertion |
| `test_v2_fix_overlay_independence.py` | PR 4 | Pending | Main loop entry gates (§14), three-gate wiring |

## Verification protocol

After each PR merges to `claude-strategy`:

1. Check this table — all tests assigned to that PR must have landed
2. Update status column from "Pending" to "✅ Landed"
3. Run `pytest tests/invariants/ tests/regression/ -v` to confirm all landed tests pass
4. Count: landed invariant + landed regression must match the running total
