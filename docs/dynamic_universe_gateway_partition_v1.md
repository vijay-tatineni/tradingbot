# Dynamic Universe v1 — Gateway Partition (frozen)

> Records the operator-approved IBKR-first broker partition. **Design/config-freeze
> only** — no router, Order Intent, or gateway code is implemented; `instruments.json`
> and `instruments_ig.json` are not modified. Date: 2026-06-09 (UTC).

## 1. Partition policy

```text
IBKR:  initial primary gateway for Dynamic Universe v1.
IG:    reference / reconciliation research only — equity order routing BLOCKED.

No automatic fallback.
No mirrored IBKR + IG execution.
No cross-gateway resubmission after timeout.
No gateway choice based on historical performance (PF / returns / Sharpe).
```

These are realised in the registry as `canonical_instruments.primary_gateway`
(`IBKR` for v1) and `gateway_map_ig.order_routing_blocked = 1` (always). The registry
enforces the IG block even if a row is explicitly written unblocked (see
`bot/universe/registry.py::upsert_gateway_ig` and its test).

## 2. The ten overlapping instruments (enabled in both universes)

Interim safety allocation — basis: current operational ownership; absent IG
cash-equity data entitlement; unverified IG EPIC mappings; absent IG-specific
deal-size/preflight validation. **Not** based on PF/returns/performance.

| display_symbol | primary_gateway | ig_mapping_status | ig_order_routing |
|---|---|---|---|
| SGLN | IBKR | UNVERIFIED | BLOCKED |
| SSLN | IBKR | UNVERIFIED | BLOCKED |
| AVGO | IBKR | UNVERIFIED | BLOCKED |
| SU   | IBKR | UNVERIFIED | BLOCKED |
| ANTO | IBKR | UNVERIFIED | BLOCKED |
| PLTR | IBKR | UNVERIFIED | BLOCKED |
| NBIS | IBKR | UNVERIFIED | BLOCKED |
| AAPL | IBKR | UNVERIFIED | BLOCKED |
| MSFT | IBKR | UNVERIFIED | BLOCKED |
| BARC | IBKR | UNVERIFIED | BLOCKED |

(`NBIS → UD.D.YNDX.CASH.IP` legacy-Yandex EPIC and `PLTR → SE.D.PLTRUS.CASH.IP` remain
the highest-risk unverified mappings — see prior `ig_mapping_verification_register.md`.)

## 3. Cutover invariant (prerequisite — NOT executed here)

Before any consolidated service may generate new entries: both broker books +
working orders reconciled; every enabled instrument has exactly one primary gateway;
every IG mapping used for routing is VERIFIED; no duplicate active strategy/account
allocation; existing open positions stay owned by their existing gateway; new-entry
routing blocked for unresolved instruments. (Detailed rules:
`docs/consolidation_entry_blocking_rules.md` from the blocker-clearance task; the
shadow engine encodes the IG block and IBKR-primary defaults but routes nothing.)
