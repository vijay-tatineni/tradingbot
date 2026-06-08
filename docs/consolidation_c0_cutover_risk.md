# Consolidation C0 — Service Topology & Cutover Risk (Workstream B)

> Documentation only. **No service was started, stopped, restarted, or reconfigured.** No order was
> submitted. Account identifiers are **not** exposed. Inspection date **2026-06-08 (UTC)**.

## 1. Active services and what each executes

| Service | State | Executes (checkout) | Config loaded | Broker | Web port |
|---|---|---|---|---|---|
| `cogniflowai-api` | active running | `/root/trading/api_server.py` | `/root/trading/instruments.json` (default) | n/a (API) | nginx 8082; API 127.0.0.1:8081 |
| `cogniflowai-bot` | active running | `/root/trading/main.py` | `instruments.json` (default) | **IBKR** (`IB_*`) | — |
| `cogniflowai-ig-api` | active running | `/root/trading-ig/api_server.py --config …/instruments_ig.json` | `instruments_ig.json` | n/a (API) | nginx 8083; API 127.0.0.1:8084 |
| `cogniflowai-ig-bot` | active running | `/root/trading-ig/main.py --broker ig --config …/instruments_ig.json` | `instruments_ig.json` | **IG** (`IG_*`) | — |

* **Each bot uses its own checkout, own config, own databases, and a different broker/account.**
  Modern→IBKR account; IG→IG account. Two separate books today.
* Databases are **per-checkout** (`positions.db`, `regime.db`, `trading.db` in each tree) — not a
  shared store. Consolidation must keep books separate or explicitly reconcile.

## 2. Duplicate-execution analysis ⚠

Enabled `layer1_active` instruments **in both universes simultaneously**:

```text
IBKR (instruments.json) enabled:  SGLN SSLN TSM AVGO ANET SU SCCO ANTO PLTR NBIS AAPL MSFT BARC NVTS
IG   (instruments_ig.json) enabled: BARC ANTO SU NBIS MSFT AAPL PLTR AVGO SGLN SSLN

OVERLAP (enabled in BOTH): SGLN SSLN BARC ANTO SU NBIS MSFT AAPL PLTR AVGO   (10 instruments)
```

**Today this is NOT duplicate execution** — the two bots trade the *same instruments on different
broker accounts* (IBKR vs IG), which is an intentional dual-broker arrangement, two distinct books.

**The consolidation risk:** if both services are pointed at a single canonical tree/config without
preserving the broker partition, the same signal for (e.g.) `SGLN`, `BARC`, `AAPL` could fire on
**both** brokers, or the same broker twice — a **duplicate-entry window**. The 10-instrument
overlap (incl. UK names BARC/ANTO/SU, gold/silver SGLN/SSLN, US names AAPL/MSFT/PLTR/AVGO/NBIS) is
exactly the set where this would manifest.

**Additional safety asymmetry:** the IG tree's `main.py` **lacks** the hard-disabled invariant and
no-edge guardrail that the modern tree enforces (see divergence register). Consolidating onto the
modern tree would actually **add** safety to the IG path — but until then the IG service runs
without those gates. (No change made; documented for the cutover plan.)

## 3. Future cutover safety checklist (prerequisites — NOT executed here)

```text
[ ] 1. One gateway/bot instance STOPPED before its replacement starts (never two writers on one
        broker account at once). Stop cogniflowai-ig-bot before any IG-on-canonical bot starts.
[ ] 2. Position reconciliation BEFORE any new entries: snapshot open positions per broker
        (IBKR + IG) and confirm against positions.db of each tree.
[ ] 3. Open-order reconciliation: confirm no working orders on either broker before switching.
[ ] 4. Idempotency-state verification: ensure entry-dedupe / reentry-cooldown state is migrated or
        reset so a restart cannot re-fire an already-open instrument.
[ ] 5. Config + database backups: back up instruments.json, instruments_ig.json, and every *.db in
        BOTH trees (hash + copy to restricted dir) before cutover.
[ ] 6. Rollback service definitions: keep the current 4 unit files and their checkouts intact so
        the prior topology can be restored verbatim.
[ ] 7. Health + negative-order tests: after cutover, verify each service active+running, fresh
        cycle in stdout log, dashboard nav-bar present (CLAUDE.md), 8080 closed, and a
        negative/blocked-order test confirms the hard-disabled invariant fires on the IG path.
[ ] 8. Broker partition preserved: confirm each canonical instrument routes to EXACTLY ONE broker
        (no instrument enabled for both IBKR and IG simultaneously post-cutover) OR an explicit,
        reviewed dual-broker policy is in place. This directly addresses the §2 overlap.
```

## 4. Summary risks

```text
- Duplicate-execution window across the 10 overlapping enabled instruments if broker partition is
  not preserved during consolidation.  [HIGH — must gate cutover]
- IG path currently runs WITHOUT the hard-disabled invariant / no-edge guardrail.  [MEDIUM — pre-existing]
- Per-checkout databases must not be merged blindly (separate books/state).  [MEDIUM]
- Public web port 8083 (IG) is on 0.0.0.0; 8082 (modern) likewise via nginx; 8080 confirmed closed. [INFO]
```

**No cutover was performed. These are prerequisites requiring explicit approval.**
