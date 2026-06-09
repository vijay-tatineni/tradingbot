# IG Mapping Blocker Register (Workstream 1)

> **Read-only.** No IG call made; no config changed. Records the blocking status of each enabled IG
> mapping and the required next action. Detail and the safe-access determination are in
> `ig_epic_reference_verification.md`. Inspection date **2026-06-09 (UTC)**.

## Blocking-status legend

```text
ORDER_BLOCKED          must NOT be used for order routing until verified
VERIFICATION_REQUIRED  needs a read-only IG reference call (operator, separately approved)
```

## Register

| Priority | Symbol | EPIC | Evidence source | Repo-only or IG-confirmed? | Classification | Blocking status | Required next action |
|---|---|---|---|---|---|---|---|
| 1 | NBIS | `UD.D.YNDX.CASH.IP` | `instruments_ig.json` + IG logs (0 runtime verifications) | **repository-only** | UNVERIFIED_NO_SAFE_ACCESS (identity UNRESOLVED) | **ORDER_BLOCKED** | Operator read-only `fetch_market_by_epic`; resolve the 4-way identity (Nebius / legacy Yandex / restructured / unavailable) **before any routing** |
| 2 | PLTR | `SE.D.PLTRUS.CASH.IP` | same | repository-only | UNVERIFIED_NO_SAFE_ACCESS | **ORDER_BLOCKED** | Operator read-only verify: Palantir + correct product type |
| 3 | SGLN | `KA.D.SGLNLN.CASH.IP` | same | repository-only | UNVERIFIED_NO_SAFE_ACCESS | **ORDER_BLOCKED** | Operator read-only verify (ETF, GBP/GBX unit) |
| 3 | SSLN | `KA.D.SSLNLN.CASH.IP` | same | repository-only | UNVERIFIED_NO_SAFE_ACCESS | **ORDER_BLOCKED** | Operator read-only verify (ETF, GBP/GBX unit) |
| 3 | AVGO | `UA.D.AVGO.CASH.IP` | same | repository-only | UNVERIFIED_NO_SAFE_ACCESS | **ORDER_BLOCKED** | Operator read-only verify |
| 3 | SU | `EC.D.SU.CASH.IP` | same | repository-only | UNVERIFIED_NO_SAFE_ACCESS | **ORDER_BLOCKED** | Operator read-only verify (EUR, Paris) |
| 3 | ANTO | `KA.D.ANTO.CASH.IP` | same | repository-only | UNVERIFIED_NO_SAFE_ACCESS | **ORDER_BLOCKED** | Operator read-only verify |
| 3 | AAPL | `UA.D.AAPL.CASH.IP` | same | repository-only | UNVERIFIED_NO_SAFE_ACCESS | **ORDER_BLOCKED** | Operator read-only verify |
| 3 | MSFT | `UC.D.MSFT.CASH.IP` | same | repository-only | UNVERIFIED_NO_SAFE_ACCESS | **ORDER_BLOCKED** | Operator read-only verify |
| 3 | BARC | `KA.D.BARC.CASH.IP` | same | repository-only | UNVERIFIED_NO_SAFE_ACCESS | **ORDER_BLOCKED** | Operator read-only verify |

## Summary

```text
Enabled IG mappings:                 10
Verified for order routing (IG ref):  0 / 10
Classification (all):                 UNVERIFIED_NO_SAFE_ACCESS
Blocking status (all):                ORDER_BLOCKED until IG reference verification
Highest risk:                         NBIS↔YNDX (identity UNRESOLVED), then PLTR↔PLTRUS
Verification date/time:               2026-06-09 (UTC); evidence repository + read-only logs only
```

**No order-capable mapping may be recommended for order-capable use.** The verification requires a
separately-authorized, read-only IG reference task (procedure in `ig_epic_reference_verification.md`
§4). This register feeds directly into the WS3 default `IG_ORDER_ROUTING_BLOCKED`.
