# IG Shutdown-Readiness Audit (read-only)

> **Read-only.** No service was stopped, disabled, restarted, or reconfigured; no IG
> session was created or disrupted; no order/position touched. `positions.db` was read
> in immutable read-only mode; **no quantities, account IDs, or sensitive details are
> committed** — only presence/count facts. Date: 2026-06-09 (UTC).

## 1. Required questions

| Question | Finding |
|---|---|
| Any open IG position in existing local state / service output / already-safe read-only path? | **No.** `/root/trading-ig/positions.db` `open_positions` table = **0 rows** (read-only, immutable). Consistent with an instrument set that never traded (all `*.CASH.IP` bar fetches are entitlement-blocked → no entries). |
| Any working or pending order? | **No local evidence.** The IG bot persists no working-order store; `watch_positions` = 0 rows, `pnl_cache` = 0 rows. **Broker-side** working-order confirmation requires a read-only IG account call — **not performed** (would risk the live session) → **unresolved**. |
| Does the internal positions DB hold any IG position requiring continued management? | **No** — `open_positions` empty; `positions.db` mtime unchanged since the Apr-16 bot start. |
| Any uncertain submission / unresolved deal confirmation? | **No local evidence.** Cannot be fully confirmed without a read-only IG account reconciliation (deferred) → **unresolved**. |
| Which service can be stopped without preventing management of a real position? | The **bot** (`cogniflowai-ig-bot`) — with no open position to manage, pausing it does not abandon a live position. (Decision still gated on the broker-side reconciliation below.) |
| Safe to leave the API service active while stopping only the bot? | **Yes.** `cogniflowai-ig-api` serves the dashboard/read API on 127.0.0.1:8084 / nginx 8083 and does not place orders; it is independent of the bot loop. |
| What reconciliation evidence is still missing? | A **read-only IG account reconciliation** (open positions + working orders direct from IG) — the one authoritative check not obtainable without an IG session. |

## 2. Operational context (from the blocker-clearance evidence)

* IG bot (PID 503086, started 2026-06-03) cycles every 5 min but **all cash-equity bar
  fetches fail** with `unauthorised.access.to.equity.exception` (33k+ occurrences) — the
  demo account lacks cash-equity historical-data entitlement. An earlier transient
  `error.security.api-key-invalid` episode was observed; the current dominant state is
  the entitlement block.
* Net effect: the IG bot is **operationally inert for cash equities** — it generates no
  signals, no entries, no fills (0 `REGIME FILTER BLOCKED`, classifier "0 bars").

## 3. Verdict

```text
INDETERMINATE_RECONCILIATION_REQUIRED
```

Local state cleanly shows **no open positions, no watch/cooldown positions, no working
orders**, and the bot has never been able to open a position — which strongly favours
"safe to pause." However, the **authoritative broker-side** open-position / working-order
confirmation requires a read-only IG account reconciliation that was **not** performed
(performing it could disrupt the live session — out of scope). Per the task's rule
("if a safe answer requires an IG call, mark it unresolved"), the verdict is
INDETERMINATE pending that one reconciliation step.

**The IG service was NOT stopped in this task** (and must not be, even were the verdict
SAFE_TO_PAUSE). Future maintenance-window steps: `docs/ig_service_pause_runbook.md`.
