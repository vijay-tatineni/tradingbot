# Broker Partition Design (Workstream 3)

> **Design only.** No instrument config, router, mapping, gateway assignment, code, or service was
> changed or implemented. No `positions.db` was read; no position/order specifics are committed.
> Builds on `canonical_instrument_registry_design.md` (`primary_execution_gateway`),
> `consolidation_c0_cutover_risk.md`, and the WS1/WS2 findings. Inspection date **2026-06-09 (UTC)**.

## 1. Problem

A consolidated system that loads one canonical universe must never submit the **same strategy
signal to both IBKR and IG** (or the same broker twice). Today the two bots are separate books on
separate broker accounts; consolidation removes that natural partition unless an explicit one
replaces it. Ten instruments are enabled in **both** universes (see
`overlapping_instrument_partition_register.md`).

## 2. Required routing policy

```text
( canonical_instrument_id , strategy_id , account_allocation )  →  EXACTLY ONE primary_execution_gateway
```

Realised by the registry fields already designed (`canonical_instrument_registry_design.md` §2):
`primary_execution_gateway` (`IBKR | IG`, **exactly one**) and `supported_gateways` (JSON list).
Mere presence of an instrument in both universe files is **not** authority to trade it on both.

### Initial safety rules (hard invariants)

```text
- No automatic fallback to a second gateway.
- No mirrored execution across gateways.
- No second-gateway resubmission after a timeout.
- An UNKNOWN submission state BLOCKS any further submission (for that key and its sibling gateway).
- Existing positions and working orders are reconciled (per broker) BEFORE any routing decision.
- The same canonical instrument may be active on BOTH brokers ONLY with explicit, operator-approved,
  separate strategy/account allocations — never by default.
```

### Gateway-selection inputs (allowed)

Recommendations are based **only** on:

```text
instrument/product correctness · verified mapping · data entitlement · execution support ·
risk/sizing compatibility · current operational ownership · operator intent
```

**Never** on historical returns, profit factor, Sharpe, or strategy performance.

### Mandatory default

```text
Any instrument with an UNVERIFIED IG mapping  →  IG_ORDER_ROUTING_BLOCKED  (until verified).
```

Per WS1, **all 10** IG mappings are currently `UNVERIFIED_NO_SAFE_ACCESS` → all default to
`IG_ORDER_ROUTING_BLOCKED`. Combined with the WS2 finding that the IG bot is entitlement-blocked
(produces no equity entries), the safe interim primary for every overlap instrument is **IBKR** — on
the allowed grounds of current operational ownership + IG data/execution entitlement gap + unverified
IG mapping (explicitly **not** performance).

## 3. Idempotency design (future — not implemented)

Proposed routing key:

```text
( strategy_id , canonical_instrument_id , direction , signal_bar_date , account_alias , primary_gateway )
```

Intent state machine:

```text
NEW
  → PREFLIGHT_REJECTED        (terminal; never submitted)
  → SUBMITTED
      → ACKNOWLEDGED
          → FILLED            (terminal)
          → CANCELLED         (terminal)
      → UNKNOWN_PENDING_RECONCILIATION   (submission outcome unknown)
  → FAILED                    (terminal)
```

Invariants:

```text
- An UNKNOWN_PENDING_RECONCILIATION intent MUST NOT be submitted to the other gateway (no failover).
- A given routing key resolves to exactly one primary_gateway; resubmission to a different gateway
  for the same key is prohibited.
- Reconciliation must resolve UNKNOWN_PENDING_RECONCILIATION to FILLED/CANCELLED/FAILED before any
  new intent for the same (instrument, gateway) is created.
```

This is a **design**; no Order Intent, router, scheduler, or state store is implemented in this task.

## 4. Cutover invariant (prerequisite gate — NOT executed)

Before a consolidated service may generate **new entries**:

```text
[ ] both broker books reconciled (IBKR + IG);
[ ] working orders reconciled on both brokers;
[ ] every enabled instrument has exactly one primary_execution_gateway;
[ ] every IG mapping used for routing is VERIFIED_REFERENCE_MATCH (WS1) — none are today;
[ ] no duplicate active strategy/account allocation exists for any canonical instrument;
[ ] current open positions remain owned by their existing gateway (no silent re-ownership);
[ ] new-entry routing is BLOCKED for every unresolved/unverified instrument.
```

## 5. Verdict

```text
PARTITION_READY_WITH_OPERATOR_DECISIONS
```

The partition **design** is complete and the safety defaults are unambiguous (all 10 overlap
instruments → IBKR primary, IG `ORDER_ROUTING_BLOCKED`). It is **ready with operator decisions**
because execution still requires: (a) operator sign-off on per-instrument gateway/strategy
allocations, (b) WS1 IG mapping verification (B1), and (c) the §4 reconciliation gate. **No router or
mapping was implemented.**
