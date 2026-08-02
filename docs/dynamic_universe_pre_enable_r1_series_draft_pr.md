# Consolidated R1–R1.3 Draft PR — title & body (for manual creation)

GitHub CLI is unauthenticated; this file preserves the exact Draft PR title and body. No PAT
was requested or handled. Create the PR manually as a **Draft** (do not mark Ready, do not
merge, do not enable auto-merge).

**Manual compare link:**
```
https://github.com/vijay-tatineni/tradingbot/compare/breakout-strategy...feature/dynamic-universe-preenable-r1-fix3?expand=1
```
On the page, use **"Create pull request ▾" → "Create draft pull request"**.

- **Base:** `breakout-strategy`
- **Head:** `feature/dynamic-universe-preenable-r1-fix3`

---

## Title

```text
Pre-Enable R1–R1.3: Dynamic Universe state and position safety — disabled, un-wired [DRAFT]
```

## Body

```text
DRAFT — review only. Default-off, un-wired, broker-free additive foundation for the
Dynamic Universe shadow state model. Based on breakout-strategy @ 46b8f257. Changes
confined to bot/universe/*, tests/universe/*, docs/*.

Consolidated review verdict: R1_SERIES_APPROVED_FOR_DISABLED_MERGE.

P0/P1/P2 findings: none.

Tests:
- 221 focused universe tests passed (pytest tests/universe).
- full-suite failures remain only in the pre-existing
  tests/test_breakout_indicators.py isolation issue (identical at base 46b8f257).

Posture:
- feature remains default-off (enable_dynamic_universe_shadow = False);
- feature remains un-wired (not imported by main.py / api_server.py);
- no production universe.db exists;
- no production migration has run;
- no service restart, broker/provider call, or runtime activation occurred.

Status:
- the reviewed R1-series findings are RESOLVED ONLY for a disabled, un-wired merge —
  this does not authorize runtime enablement;
- P3-R1-A (reused explicit provider close_event_id), P3-R1-B (legacy NULL transition
  hash), and P3-R1-C (remaining test completeness) remain MANDATORY pre-enable work;
- all R2 blockers remain open: P3-4 candidate-source integration, P3-5 inherited/
  open-book portfolio heat, P3-6 canonical identity, P3-7 verified IBKR mappings,
  BLOCKER-S FX-normalized sizing.

Merge approval does NOT authorize runtime enablement, scheduler wiring, production
migration, shadow soak, paper/live trading, or Phase R2.

What each phase did:
- R1   — atomic universe_state/history persistence; session-based cooldown semantics.
- R1.1 — authoritative position continuity across provider outages; reconciliation
         blocking; content-aware transition idempotency.
- R1.2 — valid historical replay after later state advancement; v2->v3 authoritative-
         open migration continuity; dedicated POSITION_RECONCILIATION state.
- R1.3 — lifecycle-safe synthetic close-event identity; complete immutable transition
         snapshot/hash; advanced-replay cooldown and lifecycle conflict detection;
         migration and test-coverage additions.

Commit chain:
  R1:           5da3e6ef8c959fc73a69dcf8ccdeb5264f7ee5af
  R1.1:         5c0911992b8b3ac9c97b32d8b91c8fb42257e23d
  R1.2:         73d305fe5220fb5f13e2b5f3f6ad50ec161de9d2
  R1.3:         6713cfa201165ac1908d38198944946abaab0fb4
  Docs closure: <new docs-only commit — see branch HEAD>
```

---

The docs-closure commit hash is recorded on the branch HEAD after the documentation-only
commit; substitute it for `<new docs-only commit>` above when filing the PR.
