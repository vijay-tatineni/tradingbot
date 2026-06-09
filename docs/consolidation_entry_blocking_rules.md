# Consolidation Entry-Blocking Rules (Workstream 3)

> **Design only.** No code, router, config, or service was changed. Defines the deterministic
> entry-blocking rules a future consolidated service must enforce before generating new entries.
> Companion to `broker_partition_design.md` and `overlapping_instrument_partition_register.md`.
> Inspection date **2026-06-09 (UTC)**.

## 1. Block-by-default principle

A consolidated service must **block new entries by default** and only allow an entry when **every**
gate below passes for that `(canonical_instrument_id, strategy_id, primary_gateway)` tuple. Blocking
is fail-safe: any unknown, unverified, or unreconciled condition → **BLOCK**.

## 2. Deterministic entry gates (all must pass)

```text
G1  PRIMARY_GATEWAY_RESOLVED      exactly one primary_execution_gateway for the canonical instrument
G2  MAPPING_VERIFIED             the gateway mapping used for routing is VERIFIED_REFERENCE_MATCH
                                 (IG mappings: all currently UNVERIFIED → G2 FAILS for IG today)
G3  NOT_HARD_DISABLED            hard_disabled == false (deployed invariant; authoritative)
G4  NO_DUPLICATE_ALLOCATION      no other active strategy/account allocation targets the same
                                 canonical instrument on another gateway (unless operator-approved dual)
G5  BOOKS_RECONCILED             both broker books + working orders reconciled in this cutover window
G6  NO_UNKNOWN_INTENT            no UNKNOWN_PENDING_RECONCILIATION intent exists for this key/sibling
G7  ENTITLEMENT_OK               the gateway can actually trade/fetch the instrument
                                 (IG demo cash-equity entitlement gap → G7 FAILS for IG equities today)
G8  ALLOW_NEW_ENTRIES            instrument-level allow_new_entries is not false (exits-only switch)
```

## 3. Current evaluation of the gates (today's state)

| Gate | IBKR side (overlap) | IG side (overlap) |
|---|---|---|
| G1 primary resolved | IBKR proposed primary (operator to confirm) | n/a (blocked) |
| G2 mapping verified | IBKR contracts qualified at runtime | **FAIL** — all 10 IG mappings UNVERIFIED |
| G3 not hard-disabled | pass (XAUUSD/XAGUSD excluded) | pass (no hard-disabled in IG universe) |
| G4 no duplicate allocation | requires partition (this design) | **blocked** pending partition |
| G5 books reconciled | **pending** operator reconciliation | **pending** |
| G6 no unknown intent | n/a (no intent engine yet) | n/a |
| G7 entitlement ok | IBKR live | **FAIL** — IG demo equity-history entitlement block |
| G8 allow_new_entries | per instrument | per instrument |

**Result:** every IG-side entry for the 10 overlap instruments is **BLOCKED** today (G2 + G7 fail).
IBKR-side entries depend on the operator confirming the partition and completing reconciliation
(G4/G5) — this design does not enable them.

## 4. Mandatory blocking outcomes

```text
- Any instrument with an UNVERIFIED IG mapping        →  IG_ORDER_ROUTING_BLOCKED  (all 10 today)
- Any UNKNOWN_PENDING_RECONCILIATION intent           →  block submission to ANY gateway (no failover)
- Any unreconciled broker book/working order          →  block ALL new entries this window
- Any canonical instrument lacking a single primary   →  block until exactly one is assigned
- hard_disabled instruments                           →  blocked (deployed invariant, unchanged)
```

## 5. Relationship to existing safety

* The deployed **hard-disabled invariant** (`validate_hard_disabled_instruments`) and **no-edge
  guardrail** remain authoritative; these rules are **additional**, gating duplicate/cross-gateway
  execution, not replacing the existing gates.
* Consolidating the IG path onto the modern tree would **add** the hard-disabled + no-edge guardrails
  the IG `main.py` currently lacks (C0 finding) — a safety improvement, not a regression.

## 6. Verdict (consistent with the partition design)

```text
PARTITION_READY_WITH_OPERATOR_DECISIONS
```

The entry-blocking rule set is fully specified and deterministic. Activation requires operator
decisions (gateway/strategy allocations), WS1 IG mapping verification, and the reconciliation gate.
**No router, intent engine, or gateway change was implemented.**
