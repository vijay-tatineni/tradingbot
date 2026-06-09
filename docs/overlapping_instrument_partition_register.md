# Overlapping-Instrument Partition Register (Workstream 3)

> **Design only.** No config/gateway change; no router implemented. **`positions.db` was NOT read**;
> position quantities, account IDs, and working-order specifics are deliberately **NOT** in git —
> they are marked "unknown — operator reconciliation required" per the safety rules. Gateway
> recommendations use **only** allowed inputs (correctness/mapping/entitlement/ownership/intent),
> **never** performance. Inspection date **2026-06-09 (UTC)**.

## Overlap set (enabled in BOTH universes)

From `instruments.json` (IBKR, enabled `layer1_active`, excluding hard-disabled `XAUUSD`/`XAGUSD`)
∩ `instruments_ig.json` (IG, enabled): **10 instruments**.

```text
SGLN SSLN AVGO SU ANTO PLTR NBIS AAPL MSFT BARC      (overlap count = 10)
IBKR-only enabled (not in IG): TSM ANET SCCO NVTS
```

## Register

| Canonical candidate | IBKR mapping | IG mapping | IBKR enabled | IG enabled | Open IBKR pos | Open IG pos | Working order | Strategy ownership (today) | Proposed primary | Reason (allowed inputs only) | IG mapping status | Cutover risk | Operator decision required |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| US_AAPL | `AAPL`/SMART/USD | `UA.D.AAPL.CASH.IP` | yes | yes | unknown — reconcile | unknown — reconcile | unknown — reconcile | IBKR bot (live); IG bot entitlement-blocked | **IBKR** | current ownership + IG entitlement gap + unverified IG mapping | UNVERIFIED → BLOCKED | duplicate-entry if partition not set | confirm IBKR-primary; IG stays blocked |
| US_MSFT | `MSFT`/SMART/USD | `UC.D.MSFT.CASH.IP` | yes | yes | unknown — reconcile | unknown — reconcile | unknown — reconcile | IBKR live; IG blocked | **IBKR** | same | UNVERIFIED → BLOCKED | as above | as above |
| US_AVGO | `AVGO`/SMART/USD | `UA.D.AVGO.CASH.IP` | yes | yes | unknown — reconcile | unknown — reconcile | unknown — reconcile | IBKR live; IG blocked | **IBKR** | same | UNVERIFIED → BLOCKED | as above | as above |
| US_PLTR | `PLTR`/SMART/USD | `SE.D.PLTRUS.CASH.IP` | yes | yes | unknown — reconcile | unknown — reconcile | unknown — reconcile | IBKR live; IG blocked | **IBKR** | same + EPIC stem `PLTRUS`≠symbol | UNVERIFIED → BLOCKED (priority 2) | as above | verify PLTR/PLTRUS before any IG use |
| US_NBIS | `NBIS`/SMART/USD | `UD.D.YNDX.CASH.IP` | yes | yes | unknown — reconcile | unknown — reconcile | unknown — reconcile | IBKR live; IG blocked | **IBKR** | same + **identity UNRESOLVED** (legacy `YNDX` stem) | UNVERIFIED → BLOCKED (**highest risk**) | **high** — wrong/suspended instrument if traded on IG | resolve NBIS/YNDX identity FIRST; keep IG blocked |
| LSE_BARC | `BARC`/SMART/GBP | `KA.D.BARC.CASH.IP` | yes | yes | unknown — reconcile | unknown — reconcile | unknown — reconcile | IBKR live; IG blocked | **IBKR** | current ownership + IG entitlement gap + unverified mapping; GBP/GBX unit to confirm | UNVERIFIED → BLOCKED | as above | as above |
| LSE_ANTO | `ANTO`/SMART/GBP | `KA.D.ANTO.CASH.IP` | yes | yes | unknown — reconcile | unknown — reconcile | unknown — reconcile | IBKR live; IG blocked | **IBKR** | same | UNVERIFIED → BLOCKED | as above | as above |
| LSE_SGLN | `SGLN`/SMART/GBP (ETF) | `KA.D.SGLNLN.CASH.IP` | yes | yes | unknown — reconcile | unknown — reconcile | unknown — reconcile | IBKR live; IG blocked | **IBKR** | same; ETF; GBP/GBX unit to confirm | UNVERIFIED → BLOCKED | as above | as above |
| LSE_SSLN | `SSLN`/SMART/GBP (ETF) | `KA.D.SSLNLN.CASH.IP` | yes | yes | unknown — reconcile | unknown — reconcile | unknown — reconcile | IBKR live; IG blocked | **IBKR** | same; ETF; GBP/GBX unit to confirm | UNVERIFIED → BLOCKED | as above | as above |
| EU_SU | `SU`/SMART/EUR | `EC.D.SU.CASH.IP` | yes | yes | unknown — reconcile | unknown — reconcile | unknown — reconcile | IBKR live; IG blocked | **IBKR** | same; EUR/Paris listing | UNVERIFIED → BLOCKED | as above | as above |

> "unknown — reconcile" = the safety rules forbid reading/committing position/order specifics; the
> operator must reconcile both broker books before routing (cutover invariant, `broker_partition_design.md` §4).

## Summary

```text
Overlap instruments:                         10
Proposed primary gateway (all):              IBKR (interim) — allowed grounds, NOT performance
IG routing status (all):                     IG_ORDER_ROUTING_BLOCKED (unverified mappings, WS1)
Highest-risk overlap mapping:                US_NBIS (UD.D.YNDX.CASH.IP — identity UNRESOLVED)
Position/working-order state:                UNKNOWN — operator reconciliation required (not in git)
Dual-broker active allocation:               NONE approved; default single-gateway (IBKR)
```

**No gateway assignment was changed. This is a design register only.**
