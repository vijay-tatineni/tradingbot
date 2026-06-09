# IG Difference Disposition Register (Workstream 2)

> **Read-only.** No file in either checkout was modified. One disposition per differing item, using
> the task scheme. Detailed reasoning is in `ig_behavioural_equivalence_audit.md`. Inspection date
> **2026-06-09 (UTC)**.

## Disposition legend

```text
PORT_REQUIRED        IG behaviour/config must be carried into the canonical tree
ALREADY_EQUIVALENT   canonical tree already has equal or superset behaviour
STALE_DROP           IG version is an outdated predecessor; safe to drop on adoption
CONFIG_EXTERNALIZE   a config item to preserve/externalise (not source-line logic)
GENERATED_IGNORE     generated/runtime state; not consolidated
UNKNOWN_BLOCKER      cannot be dispositioned safely without further (authorized) evidence
```

## Per-difference register

| File | Function/class/section | Behavioural purpose | IG-specific? | Equivalent in `/root/trading`? | Modern safer/newer? | IG change stale? | Config-only? | Must preserve? | Tests to prove equivalence | Live-exec risk if omitted | Disposition |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `bot/brokers/ig.py` | `IGBroker` | route orders/bars to IG | yes | **yes — byte-identical (SHA verified)** | n/a (identical) | n/a | no | already present | existing `test_ig_broker.py` (identical) | none | **ALREADY_EQUIVALENT** |
| `bot/layer1.py` | per-instrument signal loop (reversal/entry; 21 IG-only lines) | reversal-exit + entry execution gating | no (shared logic, predecessor form) | **yes — modern is a strict superset** (`allow_new_entries` wrapper) | yes (adds exits-only / no-edge gate) | yes (predecessor) | no | no (modern covers it) | golden trade-list with `allow_new_entries` unset across the 10 IG instruments == IG `layer1.py` | **none** (modern default `allow_new_entries=True` reproduces IG flow) | **ALREADY_EQUIVALENT** (IG lines STALE) |
| `bot/regime/cost_tracker.py` | `get_daily_spend`, `is_budget_exceeded` | LLM-classifier daily cost/budget enforcement | no (Anthropic spend, not broker) | **yes — modern supersedes** (bug-fix) | yes (wall-clock `date(ts)` fixes backfill budget bypass) | yes (pre-bug-fix) | no | no | regime cost-tracker tests; live: `trading_date == date(ts)` ⇒ equivalent | none for live; backfill budget bypass if the IG version were kept | **STALE_DROP** (modern ALREADY_EQUIVALENT for live) |
| `instruments_ig.json` → `layer1_active` | instrument universe + EPIC mapping | defines what IG trades | yes | **yes — byte-identical array** | n/a | no | yes | **yes** (the IG universe) | config-load + dashboard render tests; **+ order-routing verification (separate, unmet)** | mapping risk (see mapping register) | **ALREADY_EQUIVALENT** (transcription) / mapping **UNKNOWN_BLOCKER** for routing |
| `instruments_ig.json` → `settings.feature_flags` | `enable_regime_filter_live` (+ shadow toggles) | gates the live regime filter (entry suppression) and shadow subsystems | partly (IG-instance config) | **no** — modern committed copy **lacks the block** → flag defaults **off** | n/a (config) | no | yes | **yes** | startup flag-summary assertion; **regime-filter runtime check on the IG path** | **MEDIUM** — silent on→off flips an entry-suppression gate (more entries can fire) | **PORT_REQUIRED / CONFIG_EXTERNALIZE** + **UNKNOWN_BLOCKER** (runtime "does it filter?" unresolved) |
| `CLAUDE.md` (IG) | ports/topology (8083 / API 8084) | IG-instance local config | yes | modern has its own | n/a | no | yes | **yes** (local) | nav-bar presence + port checks (CLAUDE.md) | none (topology) | **CONFIG_EXTERNALIZE** |
| `web/dashboard.html`, `web/instruments.html` (IG) | IG labels/title | IG-instance UI labels | yes | modern has its own | n/a | no | yes | yes (labels) | nav-bar requirement check (CLAUDE.md) | none | **CONFIG_EXTERNALIZE** |
| `api_server.py`, `bot/dashboard.py`, `main.py` (IG copies) | shared infra | — | no | **modern strictly ahead** (incl. hard-disabled invariant + no-edge guardrail absent on IG) | yes | yes | no | no | existing safety/route tests (C0 backstop) | adopting modern **adds** safety to IG path | **STALE_DROP** (modern ALREADY_EQUIVALENT+) |
| `bot/{regime,shadow,overlays,degradation,strategies,calendar_ui}/` (IG untracked) | shared subsystems | — | no | tracked supersets in modern (C0) | yes | yes | no | no | existing subsystem suites | none | **STALE_DROP** |
| `*.db`, `*.log`, `logs/`, `__pycache__` | runtime state | — | n/a | per-checkout | n/a | n/a | n/a | back up, don't merge | n/a | n/a | **GENERATED_IGNORE** |
| `.env`, `users.json` | secrets/local | — | n/a | per-checkout | n/a | n/a | n/a | per-instance | n/a | n/a | **GENERATED_IGNORE** (secret/env) |

## Disposition counts

```text
ALREADY_EQUIVALENT     4   (ig.py, layer1.py, cost_tracker [for live], instruments_ig layer1_active)
STALE_DROP             3   (cost_tracker IG copy, api/dashboard/main IG copies, shared-subsystem IG copies)
PORT_REQUIRED          1   (instruments_ig.json feature_flags)
CONFIG_EXTERNALIZE     3   (feature_flags [dual], IG CLAUDE.md, IG web labels)
GENERATED_IGNORE       2+  (dbs/logs/pycache, .env/users.json)
UNKNOWN_BLOCKER        2   (instruments_ig order-routing verification; regime-filter runtime "does it filter?")
```

## Evidence (sealed outside git — redaction rule)

Verbatim diffs are **not** committed. They are stored in the restricted evidence directory and
referenced by SHA-256 (hashes are not secrets):

```text
restricted dir   /root/consolidation_evidence/eodhd_ig_equiv_20260609/   (mode 0700)
layer1_modern_vs_ig.diff          sha256 a2c050154833ac2b54f8f9ee876bdabc2114ed730e27e3f4926666e44b79e94e
cost_tracker_modern_vs_ig.diff    sha256 af54918877ec2b24a25000615c497e1c2ae04f3d61c3df8de02f30f4085bb885
instruments_ig_modern_vs_ig.diff  sha256 7b11b5770ddad6ca53bce9816b3df75ed1789ede8b74005c3cd65412f62e313b
ig_py_sha256.txt                  sha256 ed3007d0c9a89f7c356da8420624cdfe985c40a7f6ba432f30b49385fa26526a
```

This complements the C0 freeze package (`consolidation_c0_evidence_manifest.md`).
**No raw unredacted patch is committed to git.**
