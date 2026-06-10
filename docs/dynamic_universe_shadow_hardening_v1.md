# Dynamic Universe v1 — Shadow Hardening (review record)

> Child branch `feature/dynamic-universe-ibkr-shadow-v1-hardening` (base
> `feature/dynamic-universe-ibkr-shadow-v1` @ `82814cd`). **Additive, feature-flagged,
> non-integrated, NOT merged/deployed/enabled.** The breakout result remains PROVISIONAL
> (Phase 3c: NO; real-money: NO) and is untouched. No live/paper execution, no Order
> Intent, no IG execution, no EODHD ingestion, no Claude/TTI.

This record covers the review-hardening changes only; the foundation freeze record is
`experiments/dynamic_universe_ibkr_shadow_v1.md`.

## 1. Cooldown semantics — FROZEN (task §2)

The exit session **E** does **not** count as a completed post-exit cooldown session.
The instrument is blocked for the next three completed sessions (E+1, E+2, E+3); the
earliest re-eligibility evaluation is **E+4**. On exit, `cooldown_remaining` is set to 3
and is not decremented that session; it decrements on each subsequent confirmed-flat
session and is **held** while a position is open or while position status is UNKNOWN.
The 3-session duration is unchanged. Full transition table in
`docs/dynamic_universe_state_transitions_v1.md`.

## 2. Position-status seam (task §3)

Broker-free `PositionSnapshotProvider` (`NO_POSITION` / `POSITION_OPEN` /
`POSITION_EXITED_TODAY` / `UNKNOWN`), dependency-injected. Drives POSITION_OPEN ⇄
EXIT_ONLY and exit → COOLDOWN organically. The universe package imports no broker
adapter and calls no broker method (asserted). `UNKNOWN` is fail-safe: no flat
assumption, never entry-eligible, never liquidated, cooldown held, reason recorded.

## 3. Corporate-action policy — two modes (task §4)

| corp-action status | Shadow mode | Paper/live mode (default, fail-closed) |
|--------------------|-------------|-----------------------------------------|
| `ok`               | eligible    | eligible                                |
| unknown/`unavailable` | WARNING `corporate_action_status_unknown` (non-blocking) | HARD BLOCK `corp_action_data_unavailable` |
| `anomaly`          | BLOCK `corp_action_anomaly` | BLOCK `corp_action_anomaly` |

Default mode is fail-closed (paper/live). Disjoint reason codes ⇒ policies can never be
confused. Paper/live is a tested-only path — **not** wired or enabled here.

## 4. Scheduler timing / market-calendar review (task §5)

**Finding — the fixed post-close UTC gates are conservative by construction.** Each gate
sits AFTER the latest possible close across BOTH DST regimes, so it can only ever fire
LATE, never early:

```text
Market  gate (UTC)   close winter / summer (UTC)        margin
LSE     17:00        16:30 (GMT) / 15:30 (BST)          ≥ 30 min
EU      17:00        16:30 (CET) / 15:30 (CEST)         ≥ 30 min
US      22:00        21:00 (EST) / 20:00 (EDT)          ≥ 60 min
```

* **US & UK DST transitions, and the divergence windows** (mid-March: US already EDT
  while UK still GMT; late-Oct: UK back to GMT while US still EDT) are safe — the gates
  are conservative regardless of the offset.
* **Early-close / half-day sessions** are safe — an earlier close only makes the
  completed bar available sooner; the gate still runs after.
* The time gate is **necessary, not sufficient**: on an exchange **holiday** the gate
  passes but no session occurred, and a daily bar can **arrive late**. The fixed UTC gate
  alone cannot guarantee the completed bar exists.

**Resolution (no new calendar dataset downloaded).** An injectable
`bar_available_fn(rec, trading_date) -> bool` is added; when provided the scheduler
requires it to confirm the expected completed session bar exists before evaluating.
Missing → skip + log + retry idempotently next cycle (no history written). A raising
check is fail-safe (treated as unavailable). Invariant upheld: *the evaluator may run
late, but never before the completed daily bar is safely available.* The missing-bar
skip (retry, no history) is deliberately distinct from the eligibility `stale_bar` →
DATA_INELIGIBLE path (history written).

## 5. Portfolio-heat rejection is structurally dominated (finding)

Under the FROZEN params `MAX_OPEN_POSITIONS = 5`, `RISK_PER_TRADE = 0.5%`,
`MAX_PORTFOLIO_HEAT = 2.5%`: `5 × 0.5% = 2.5%`, and per-order risk is floored (always ≤
0.5%·equity), so five selected positions sum to ≤ 2.5%. The **slot cap binds at or before
the heat cap** — `portfolio_heat_exceeded` is therefore not organically reachable. The
heat-rejection branch is retained (defensive; covers a future param change) and is
exercised in the rehearsal via a clearly-labelled probe that transiently lowers the heat
cap (production params are never mutated persistently). Organic heat-limit rejections = 0.

## 6. Offline rehearsal report (task §6) — operational metrics only

Deterministic, fully offline (`tests/universe/rehearsal.py`). **No returns / P&L / PF /
Sharpe / winners / rankings are computed or reported** (guarded by a test). Representative
counts from one run (synthetic fixtures; identical across runs):

```text
sessions_evaluated                : 18
instruments_evaluated             : 37
migration_idempotent_rerun        : True
state_transitions (by new_state):
    ADMIN_PAUSED   : 2     COOLDOWN        : 4     DATA_INELIGIBLE : 3
    ENTRY_ELIGIBLE : 15    EXIT_ONLY       : 2     HARD_DISABLED   : 2
    POSITION_OPEN  : 2     WATCHLIST       : 7
candidates_by_source              : AUTO 1, TTI 1, MANUAL 1
candidate_expirations             : 2      (TTI + MANUAL past 5-session TTL)
hard_disabled_suppressions        : 2
admin_pause_suppressions          : 2
position_open_transitions         : 2
exit_only_transitions             : 2
cooldown_transitions              : 4      (E + E+1/E+2/E+3 blocking)
slot_rejections                   : 2
sector_cap_rejections             : 1
heat_limit_rejections             : 2      (heat-probe; 0 organically — see §5)
idempotent_skips                  : 8
missing_bar_skips                 : 1
corp_action_unknown_warnings      : 2
position_status_unknown_safe      : 1
ibkr_primary_assignments          : 7
ig_routing_blocked                : 1
data_ineligible_reasons           : insufficient_history 3, indicators_unavailable 3,
                                     adv20_below_min 1, invalid_ohlc 1, price_below_min 1,
                                     stale_bar 1
```

## 7. Limitations / not-done (unchanged scope boundaries)

No live/paper execution, Order Intent, broker router, IG execution/mapping change, EODHD
call, Phase 3c, Claude overlay, or TTI parsing. The shadow flag remains default-OFF and
un-wired into `main.py`. Historical strategy validation remains BLOCKED pending approved
provider access (`docs/dynamic_universe_provider_status.md`). Organic portfolio-heat
rejection is unreachable under frozen params (§5).
