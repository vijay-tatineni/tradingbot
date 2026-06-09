# IG Consolidation — Preservation Requirements & C1 Readiness (Workstream 2)

> **Read-only.** No porting, no consolidation, no config/source/service/broker/database change was
> performed. This document states what must be preserved, the duplicate-execution analysis, and the
> overall C1 readiness verdict. Inspection date **2026-06-09 (UTC)**. Builds on
> `ig_behavioural_equivalence_audit.md`, `ig_difference_disposition_register.md`,
> `ig_mapping_verification_register.md`, and the C0 cutover-risk analysis.

---

## 1. Required answers

### Can `/root/trading` be confirmed as the canonical source?

**Yes — with explicit blockers (below).** `/root/trading` (`breakout-strategy` line) is strictly
newer, already carries the IG broker adapter **byte-identical** (`ig.py` SHA verified), is already
broker-pluggable (`main.py --broker ig --config …`), carries the **deployed hard-disabled invariant
+ no-edge guardrail the IG tree lacks**, and has a clean working tree. The IG tree contributes only
config to preserve — it holds **no IG-unique behavioural logic** the modern tree doesn't already
equal or supersede.

### Which IG changes must be ported / preserved?

```text
P1  instruments_ig.json  →  settings.feature_flags block (esp. enable_regime_filter_live: true)
        Disposition: PORT_REQUIRED / CONFIG_EXTERNALIZE. The modern committed copy lacks it; its
        absence silently flips the live regime filter OFF (more entries can fire). MEDIUM risk.
P2  instruments_ig.json  →  layer1_active IG universe + EPIC mappings (already byte-identical, but
        is the authoritative IG universe and must be carried verbatim — and its mappings verified,
        see B1/B2).
P3  IG local config      →  IG CLAUDE.md (ports 8083 / API 8084 topology), web/*.html IG labels.
        Disposition: CONFIG_EXTERNALIZE.
```

### Which differences are already equivalent?

```text
- bot/brokers/ig.py            ALREADY_EQUIVALENT (byte-identical, SHA verified)
- bot/layer1.py                ALREADY_EQUIVALENT (modern superset; 21 IG-only lines are predecessor)
- bot/regime/cost_tracker.py   ALREADY_EQUIVALENT for live (modern bug-fix supersedes)
- instruments_ig.json layer1_active  ALREADY_EQUIVALENT (byte-identical array)
```

### Which differences are stale?

```text
- The 21 IG-only lines in layer1.py            (predecessor of the allow_new_entries superset)
- The IG bot/regime/cost_tracker.py            (pre-2026-06-01 budget-enforcement bug)
- IG copies of api_server.py / dashboard.py / main.py and the shared subsystems
  (regime/shadow/overlays/degradation/strategies/calendar_ui) — modern strictly ahead
- The modern tree's own feature-flag-less instruments_ig.json copy is itself stale relative to the
  LIVE IG config (resolve by adopting the live feature_flags — P1)
```

### Which mappings block safe consolidation?

**All order-capable EPIC mappings are UNVERIFIED for routing** (no IG reference-data call
authorized). **NBIS↔YNDX** (`UD.D.YNDX.CASH.IP`) is the **top blocker** (legacy Yandex stem,
sanctions/restructuring history; registry design already says "do not trade until confirmed");
**PLTR↔PLTRUS** is second. See `ig_mapping_verification_register.md`.

### Can consolidation Phase C1 begin?

**Yes, with explicit blockers** — see verdict §4. C1 *planning/preparation* can proceed; C1
*execution* (any cutover that points services at a single tree) must not begin until B1–B3 clear.

---

## 2. Duplicate-execution analysis (10 overlap instruments)

Overlap enabled in both universes: `SGLN SSLN AVGO SU ANTO PLTR NBIS AAPL MSFT BARC`.

| Question | Finding |
|---|---|
| Current IBKR service ownership | `cogniflowai-bot` → `/root/trading/main.py`, IBKR account, loads `instruments.json` |
| Current IG service ownership | `cogniflowai-ig-bot` → `/root/trading-ig/main.py --broker ig`, IG account, loads `instruments_ig.json` |
| Can both generate the same signal? | **Yes** — same `layer1` logic over the same 10 instruments |
| Can both open exposure simultaneously? | **Yes** — but on **different broker accounts** (IBKR vs IG). Today this is **two distinct books**, an intentional dual-broker arrangement, **not** duplicate execution. |
| How should the future primary-gateway field prevent duplication? | The canonical registry's **`primary_execution_gateway` (IBKR \| IG, exactly one)** plus **`supported_gateways`** JSON list (`canonical_instrument_registry_design.md` §2) make Order Intent route each canonical instrument to **exactly one** gateway. The cutover test "each canonical instrument routes to EXACTLY ONE broker" (cutover-risk §3 item 8) enforces it. **No gateway field exists in the configs today** — this is design, not implemented (and must not be implemented in this task). |
| Reconciliation required before cutover | Per `consolidation_c0_cutover_risk.md` §3: stop one bot before its replacement starts (never two writers on one broker account); snapshot + reconcile open positions and working orders per broker against each `positions.db`; verify entry-dedupe/cooldown idempotency state; back up all configs + `*.db` in both trees; keep the 4 unit files for rollback; post-cutover negative-order test that the hard-disabled invariant fires on the IG path. |

**Risk if broker partition is not preserved at cutover:** the same signal for an overlap instrument
(e.g. `SGLN`, `BARC`, `AAPL`) could fire on **both** brokers, or the same broker twice — a
duplicate-entry window across exactly these 10 instruments. **HIGH — must gate cutover.**

---

## 3. Explicit blockers for C1

```text
B1  IG EPIC order-routing verification (IG reference-data, separately authorized).
    NBIS↔YNDX first (top risk), then PLTR↔PLTRUS, then the remaining 8. No order-capable mapping
    may be treated as verified solely because it is in the file.   [BLOCKER — mapping]

B2  Preserve instruments_ig.json feature_flags (P1) AND verify the IG regime-filter RUNTIME
    behaviour. The flag is statically ON in the live IG config but the modern committed copy would
    flip it OFF, and whether the filter currently bites (given the IG demo equity-history
    entitlement gap) is unresolved read-only. Reconcile the "IG doesn't filter" belief with the
    "flag is on" config using live evidence before cutover.   [BLOCKER — live-execution config]

B3  Enforce broker partition before any cutover: each canonical instrument routes to EXACTLY ONE
    gateway (primary_execution_gateway), or an explicit reviewed dual-broker policy is in place,
    to close the 10-instrument duplicate-execution window.   [BLOCKER — duplicate execution]
```

None of B1–B3 is an architectural unknown; each is a bounded verification/preservation/partition
step. That is why the verdict is "READY WITH EXPLICIT BLOCKERS" and not "NOT READY."

---

## 4. Overall verdict

```text
C1 READY WITH EXPLICIT BLOCKERS
```

`/root/trading` is confirmable as the canonical source and no IG-unique **behaviour** is lost on
adoption. C1 may proceed to **preparation**; C1 **execution / cutover** is gated on clearing
**B1 (mapping order-routing verification)**, **B2 (feature_flags preservation + regime-filter
runtime check)**, and **B3 (broker-partition enforcement against the 10-instrument overlap)**.

**No porting, consolidation, registry/state implementation, gateway change, broker call, database
change, or service restart was performed. Analysis only.**
