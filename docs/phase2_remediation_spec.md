# Phase 2 Remediation Spec (canonical)

**Provenance:** Authored in the Claude chat overseeing this remediation; committed to the repo so PRs B/C/D run against canonical text rather than inference. Supersedes quoted fragments in docs/flatten_record.md. Phase structure, Phase 0/1 content, and standing constraints are as recorded in docs/flatten_record.md; this document is authoritative for Phase 2 and Phase 3.

**Base branch:** `breakout-strategy`. Each fix is its own branch and PR, in order A → B → C → D, one at a time. Every PR: isolated tests (no test may create or touch a live-tree DB path), a diff summary in the PR description, no changes outside stated scope. Bots remain stopped; nothing deploys until Phase 3.

---

## PR A — Position reconciliation + alerting  ✅ MERGED (PR #19, a1d561c)

On startup and once per cycle, diff broker positions (read-only call) against the position tracker. Any divergence: Telegram alert with symbol/size detail (alert on transition, not every cycle), and block new entries for the divergent symbol until resolved.

Confirmed policy decisions:
- Blocks do NOT auto-clear (`reconciliation_auto_clear` default false). "Resolved" = explicit operator action.
- A failed broker read is never treated as agreement — no block clears and no divergence is inferred from an exception.
- Quantity comparison is sign-aware (long flipped to equal-size short must not compare equal).
- In-memory blocks clearing on restart is acceptable because startup reconciliation re-detects and re-blocks any divergence that still exists. (Desirable test: restart with persisting divergence → block re-established.)

## PR B — Broker-attached protective stops

IBKR: protective stop submitted atomically with every entry (parent + child via bracket/attached order, ib_insync `bracketOrder` or equivalent transmit-flag pattern). IG: attached stop via deal parameters. Synthetic exit logic is retained unchanged as the primary exit layer.

**Design decisions (settle the three open questions):**

1. **Stop level = the wider emergency level** (`trail_stop_pct * 2`, i.e. the existing tier-1 emergency level in layer1), NOT the synthetic trail level. The broker-held stop is a catastrophe backstop for process/host death; the synthetic trail with its confirmation logic remains the primary exit while the bot runs. Strategy behaviour must not change: an attached stop at the trail level would fire without the bot's confirmation logic and alter exits.
2. **No ratcheting.** The broker stop is submitted once at entry and remains static. Live-order modification every cycle is out of scope as an unacceptable new failure surface. Corollary (mandatory): every normal (synthetic) exit path MUST cancel the attached stop before/atomically with submitting the closing order, so no naked working stop survives a closed position. The startup check must alert on orphaned stop orders: any working stop order with no matching open position.
3. **Attach-failure invariant: no unprotected position persists.** If the parent fills and the child stop is rejected or fails: retry the attach once; if still unprotected, immediately flatten the position with a market order and alert. An entry that cannot be protected is an entry the system refuses to hold.

Startup check: any tracked position lacking a broker-held stop, and any orphaned stop order, triggers an alert — wired into PR A's reconciler, which owns startup and alerting.

## PR C — Exit-cadence fix

Fix the Tier-2 window defect so daily-timeframe names get trailing-stop/take-profit evaluation on every cycle (or an explicitly defined cadence), not never. Include a test that fails on the old behaviour. Document the new worst-case dead band in minutes in the PR description. Precursor (may be a separate tiny commit/PR): test-isolation fix for module-level DB path constants so the full suite creates zero files in a live tree.

## PR D — Risk-based sizing

Replace `int($1000/price)` equal-notional sizing with ATR/stop-distance fixed-fractional risk sizing, respecting the pre-order validation gate. **Account equity MUST be read live from the broker at sizing time — never a hardcoded value.** (Phase 1's overnight baseline change from ~£1.01M to £250k without warning is the standing argument.) Include the formula and worked examples in tests. Lands before restart so the Phase 3 observation window measures the intended system.

## Per-PR completion report format

```text
PR <A/B/C/D>: branch, commit, PR link
files changed: <list — must match stated scope>
tests added/passing: <n>/<n>
out-of-scope changes: 0 (mandatory)
PHASE_2_<A/B/C/D>_MERGED   (issued only after human review and merge)
```

## Phase 3 — Clean restart with a pre-registered window (unchanged summary)

Not before A–D are merged and human-reviewed. (1) Pre-register the observation window in `docs/observation_window_v2.md` BEFORE restart: duration, metrics (expectancy/PF of taken trades; counterfactual expectancy of regime-blocked trades from shadow logs; per-instrument trade counts), minimum sample size below which the window is inconclusive by definition, and the keep/replace/retire decision rule for the regime layer. Baseline: GBP 250,000. (2) Restart services (bots re-enabled, APIs, nginx, per flatten_record §6); first-cycle verification: reconciliation silent, first entry carries a broker-held stop at the emergency level, sizing matches the risk formula against live equity. (3) First-week check: daily-timeframe names show Tier-2 evaluations in logs. Deferred CLAUDE.md restart rule from PR #19 is satisfied by this restart.

## Standing constraints (all phases)

- Dynamic Universe remains frozen; universe DBs are not created.
- XAUUSD/XAGUSD remain hard-disabled for entries.
- Paper/demo accounts only.
- No branch checkouts in production trees — worktrees only.
- During any window where the agent needs broker access, the operator stays out of Client Portal.
- Anything not completable within constraints is reported BLOCKED; scope is never widened to unblock.
