# IG Service Pause Runbook (future maintenance window — NOT executed)

> Exact steps for a future, separately-approved maintenance window to pause the IG bot.
> **Nothing here was executed in this task.** The IG services remain running. Prereq:
> resolve the `INDETERMINATE_RECONCILIATION_REQUIRED` verdict in
> `docs/ig_shutdown_readiness_audit.md` first. Date: 2026-06-09 (UTC).

## Preconditions (must all hold before stopping the bot)

```text
[ ] Operator approval for the maintenance window is granted.
[ ] Position reconciliation: read-only IG account check confirms NO open IG position
        (cross-check against /root/trading-ig/positions.db open_positions — currently 0).
[ ] Working-order reconciliation: read-only IG account check confirms NO working/pending
        orders.
[ ] No unresolved/uncertain deal confirmation outstanding.
```

## Steps

```text
1. Backup state/config/logs (no secrets in git; copy to the restricted dir, hash):
     - /root/trading-ig/positions.db, regime.db, trading.db (+ other *.db)
     - /root/trading-ig/instruments_ig.json, .env (restricted; never committed)
     - /root/trading-ig/{bot_stdout.log,bot_stderr.log,portfolio_bot.log}, logs/
     - record SHA-256 of each backup artefact.
2. Stop ONLY the bot (leave the API service running):
     systemctl stop cogniflowai-ig-bot.service
   Do NOT stop cogniflowai-ig-api.service (dashboard/read API stays available; no orders).
3. Post-stop health verification:
     - systemctl status cogniflowai-ig-bot.service shows inactive (stopped);
     - cogniflowai-ig-api.service still active (running); dashboard on 8083 loads;
     - no new orders/fills appear anywhere; port 8080 remains closed.
4. Rollback (if needed):
     systemctl start cogniflowai-ig-bot.service
     - confirm active (running) + a fresh cycle line in bot_stdout.log;
     - confirm the prior unit files/checkout are intact so the exact prior topology
       can be restored verbatim.
```

## Notes

* The IG bot is currently inert for cash equities (entitlement-blocked); pausing it
  removes only an idle loop, not active position management — but the broker-side
  reconciliation in the preconditions is the authoritative gate.
* This runbook does not touch `/root/trading` (IBKR) or its services.
