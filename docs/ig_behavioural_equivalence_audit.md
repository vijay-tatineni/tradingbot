# IG Behavioural-Equivalence Audit (Workstream 2)

> **Read-only.** Neither `/root/trading` nor `/root/trading-ig` was modified. Nothing was cleaned,
> reset, stashed, checked out, merged, cherry-picked, rsync'd, or restarted. No service was touched;
> no broker endpoint was called. Method: working-tree-to-working-tree comparison + static code
> tracing. **Raw diffs are NOT reproduced here** — they are sealed outside git in the restricted
> evidence directory (see `ig_difference_disposition_register.md` §Evidence). Inspection date:
> **2026-06-09 (UTC)**.
>
> Compares the older IG checkout `/root/trading-ig` (`claude-strategy` @ `d95d258`, dirty working
> tree) against the proposed canonical modern tree `/root/trading` (`breakout-strategy` line). Builds
> on the C0 divergence register; resolves the three `UNKNOWN_REQUIRES_REVIEW` items it left open
> (`bot/layer1.py`, `bot/regime/cost_tracker.py`, and the `instruments_ig.json` settings delta).

---

## 1. Scope audited

```text
bot/brokers/ig.py            — verify the "already byte-identical" claim (do not edit)
bot/layer1.py                — explain the 21 IG-only lines (do not edit; CLAUDE.md forbids logic change)
bot/regime/cost_tracker.py   — explain every behavioural difference (do not edit)
instruments_ig.json          — settings/feature_flags delta + universe (mapping audit in separate doc)
remaining C0 UNKNOWN_REQUIRES_REVIEW entries
```

---

## 2. `bot/brokers/ig.py` — the IG broker adapter

**Claim (C0): byte-identical in the modern tree. Verdict: CONFIRMED.**

```text
SHA-256 /root/trading/bot/brokers/ig.py     = 92cc06e2d649308f7684d379c4de841c564aa04191dbbc22e49f9ca26db8bdea
SHA-256 /root/trading-ig/bot/brokers/ig.py  = 92cc06e2d649308f7684d379c4de841c564aa04191dbbc22e49f9ca26db8bdea
diff = 0 lines
```

The IG order/bars adapter (`IGBroker`) is identical between trees. No port is required and no edit
was made. The modern tree is already broker-pluggable (`main.py --broker ig --config …`).
**Disposition: ALREADY_EQUIVALENT.**

---

## 3. `bot/layer1.py` — the 21 IG-only lines

Diffstat: **73 modern-only lines / 21 IG-only lines.** The 21 IG-only lines are **not IG-specific
logic** — they are the **predecessor form** of the shared signal-handling loop. Behavioural reading
(no file content quoted):

* **What they implement:** the reversal-and-entry handling inside the per-instrument signal loop.
  Specifically, for an open position receiving an opposite (`signal == -1`) confirmation, the IG
  version runs, in order: (a) the regime-filter check (`_regime_filter_allows`), (b) the plugin
  `pre_trade` gate, (c) `broker.handle_signal(...)` to close/flip, (d) the shadow-trade broadcast —
  at one nesting level. It also has the plain `should_reenter` re-entry branch and the fresh
  long/short entry branches.
* **What the modern tree does with the SAME flow:** it wraps that identical sequence inside an
  `allow_new_entries` conditional (an *exits-only* switch, default `True` when the key is absent):
  - `allow_new_entries == False` → on a reversal it **closes the position but does not flip**
    (`SIGNAL_REVERSED (entry suppressed)`); on re-entry / fresh long / fresh short it logs
    `ENTRY SUPPRESSED (allow_new_entries=false)` and opens nothing. Exit management
    (trail/TP/emergency) is always preserved.
  - `allow_new_entries` absent or `True` → it runs the **identical** regime-filter → `pre_trade` →
    `broker.handle_signal` → shadow-broadcast sequence as the IG-only lines. The 21 IG-only lines
    are exactly this branch, minus the wrapping conditional.

* **Which behaviour they affect:** entries and reversal-exits (signal-generation execution gating).
  They do **not** touch symbol mapping, gateway/EPIC handling, position sizing, or reconciliation —
  those live in `bot/brokers/ig.py` (identical) and the config.
* **Does the modern tree already implement the same behaviour?** **Yes — as a strict superset.** For
  any instrument that does not set `allow_new_entries` (which is **all 10** instruments in
  `instruments_ig.json` — none carry the key), the modern code's `allow_new_entries` defaults to
  `True`, so the modern path reduces to the exact regime/pre_trade/handle_signal/shadow flow the IG
  21 lines implement.
* **Would dropping the 21 IG-only lines change live IG behaviour?** **No.** Because the modern
  superset reproduces the identical flow whenever `allow_new_entries` is absent/True — the live case
  for every IG instrument — pointing the IG service at the modern `layer1.py` is behaviour-preserving
  and additionally **adds** the exits-only / no-edge-guardrail capability the IG path currently
  lacks. (That capability is exercised today only by `ANET` on the **IBKR** side, which sets
  `allow_new_entries:false`; it is dormant for the IG universe.)

**No edit was made (CLAUDE.md: do not modify `layer1.py` signal-generation logic — wrap only,
behavioural equivalence proven by the backtest/golden suite). Disposition: ALREADY_EQUIVALENT
(modern superset); the 21 IG-only lines are STALE (predecessor). Equivalence test required before
any cutover: a golden trade-list run with `allow_new_entries` unset across the 10 IG instruments
must produce a bit-identical trade list to the IG `layer1.py`.**

---

## 4. `bot/regime/cost_tracker.py`

Diff is confined to **two methods**: `get_daily_spend` and `is_budget_exceeded`. The
`CREATE_TABLE` definition is **not in the diff** → the `regime_classification_log` schema is
**identical** in both trees.

| Dimension | Finding |
|---|---|
| Behavioural difference | IG: `get_daily_spend(trading_date)` filters `WHERE trading_date = ?`. Modern: `get_daily_spend(day)` filters `WHERE date(ts) = ?` (wall-clock day). `is_budget_exceeded` follows the same parameter. |
| Broker-specific? | **No.** This tracks **LLM regime-classifier token spend** (Anthropic `claude-sonnet-4-6`). It has nothing to do with IG, IBKR, orders, or execution. |
| Affects only reporting/cost accounting? | It affects the **daily-cost budget enforcement** for the classifier (`is_budget_exceeded` gates classifier spend) — never orders or the broker. For **live** classification, `trading_date == date(ts)` (today's bars classified today), so live behaviour is **equivalent**. The fix only changes behaviour for **backfill** runs (many historical `trading_date`s logged within one wall-clock day), where the IG version silently disabled the cap. |
| Modern supersedes? | **Yes.** Modern is newer (mtime Jun 1 vs May 18) and is an explicit **bug-fix** (root cause documented in the modern docstring: filtering on `trading_date` disabled budget enforcement for backfill — found 2026-06-01). |
| DB / schema-compatibility issue? | **None.** `regime_classification_log` carries **both** `ts` and `trading_date` columns, both populated on every `log_classification` insert. Modern's `date(ts)` filter therefore reads an existing, populated column — **no migration, no schema change**. DBs are per-checkout (`regime.db` in each tree) — no cross-contamination. |

**No edit was made. Disposition: ALREADY_EQUIVALENT for live behaviour; the IG version is a
STALE_DROP (pre-bug-fix predecessor). Adopting the modern (canonical) file is strictly safer.**

---

## 5. `instruments_ig.json` — settings / `feature_flags` delta (the real divergence)

The two `instruments_ig.json` files have **byte-identical `layer1_active` arrays** (all 10
instruments: same symbols, EPICs, currency, exchange, qty, stops, flags). The **only** difference is
in `settings`:

* The **live IG config** (`/root/trading-ig/instruments_ig.json`, the file the running
  `cogniflowai-ig-bot` actually loads via `--config`) contains a **`feature_flags`** block.
* The **modern tree's committed `instruments_ig.json`** has **no `feature_flags` block at all**.

The live IG `feature_flags`: all subsystems `*_shadow: true`, all `*_live: false`,
`position_tagged_exit:false`, `calendar_ui:false`, `data_quality_strict:false`, and crucially
**`enable_regime_filter_live: true`**.

### 5a. Regime-filter trace (resolves the C0 review item AND a standing assumption)

Static trace of how the flag is consumed (read-only):

1. `bot/regime/flags.py` — `SAFE_DEFAULTS["enable_regime_filter_live"] = False`; the module header
   states "Missing config keys never enable live behaviour." A missing flag block ⇒ flag **off**.
2. `main.py:141` loads `settings.feature_flags` (empty dict if absent) into `FeatureFlags`; the
   dependency graph requires `enable_classifier_shadow` for `enable_regime_filter_live` (else
   `ConfigError` at startup). The live IG config satisfies that dependency, so the flag loads **on**.
3. `bot/regime/orchestrator.py:apply_regime_filter` — if the flag is **off**, returns `True`
   immediately (no filtering). If **on**, it **blocks** any entry whose smoothed regime isn't
   `TRENDING` (warm-up / no-smoothed-row also blocks — conservative); infra exceptions (store
   unavailable) fall through to **allow**, so transient regime-data failure can't halt trading.
4. `bot/layer1.py:_regime_filter_allows` asks each plugin's `apply_regime_filter`; blocks only if a
   plugin says block.

**Consequence for consolidation:** the live IG service runs with `enable_regime_filter_live = true`.
If a canonical consolidation pointed the IG service at the **modern** `instruments_ig.json` (no
`feature_flags`), the flag would **default to `False`** and the regime-filter live branch would be
**silently turned off**. Turning the flag off **removes an entry-suppression gate** → potentially
**more** entries fire than today. This is a **live-execution behavioural change**, not cosmetic.

**Standing-assumption check ("IG doesn't actually filter"):** the static config shows the flag is
**ON** in the live IG service. Whether the filter currently *bites* depends on whether the IG regime
pipeline produces smoothed-regime rows — and `docs/TECH_DEBT.md` documents that the **IG demo
account lacks cash-equity historical-data entitlement** (`unauthorised.access.to.equity.exception`
on all 10 `*.CASH.IP` epics), which could starve the IG regime classifier of bars. With the flag on
and **no** smoothed rows, `apply_regime_filter` would *block* (warm-up default) unless the store
lookup raises (→ allow). **I cannot determine the live runtime state read-only** without inspecting
the IG `regime.db` or running the bot — both out of scope. Therefore this audit makes **no claim**
about whether the filter is currently active; it records the verifiable facts: (i) the flag is
statically ON in the live IG config, (ii) the modern committed copy would flip it OFF, and (iii) the
IG regime-filter **runtime behaviour must be verified before cutover**. The "IG doesn't filter"
belief and the "flag is on" config are in tension and must be reconciled with live evidence — a
named blocker, not an assumption to carry forward.

**Disposition (split by section):** `layer1_active` universe = **ALREADY_EQUIVALENT** (byte-identical);
`feature_flags` block = **PORT_REQUIRED / CONFIG_EXTERNALIZE** — it must survive into the canonical IG
config, because its absence silently flips a live flag.

---

## 6. Remaining C0 `UNKNOWN_REQUIRES_REVIEW` entries

| C0 item | This audit's resolution |
|---|---|
| `bot/layer1.py` 21 IG-only lines | **Resolved** — predecessor of the modern superset; ALREADY_EQUIVALENT, no live behaviour change on adoption (§3). |
| `bot/regime/cost_tracker.py` | **Resolved** — modern is a strict bug-fix supersession; no schema issue; STALE_DROP of the IG copy (§4). |
| `instruments_ig.json` settings delta | **Resolved** — the delta is the `feature_flags` block incl. `enable_regime_filter_live`; PORT_REQUIRED, with a regime-filter runtime blocker (§5). |
| `docs/` + `specs/` (IG untracked) | **Resolved — reviewed read-only.** `specs/CLAUDE_STRATEGY_SPEC_v3.md` and `specs/prompts/` are **identical** to the modern tree (diff = 0); the spec's "IG-specific adaptations" line sits in a **future-work backlog list** (a deferred idea, not implemented IG-only behaviour). `docs/TECH_DEBT.md` differs but the **modern tree is 119 lines ahead** and the **4 IG-only lines** are a non-behavioural `.gitignore` SQLite-sidecar housekeeping note. **No IG-only intended behavioural divergence is documented.** Disposition: STALE_DROP (modern strictly ahead); no behavioural blocker. |

---

## 7. Audit conclusions

* The IG broker adapter, the layer1 signal loop, and the regime cost tracker are all **already
  equivalent or strictly superseded** in the modern tree. No IG-unique **behaviour** is lost by
  adopting `/root/trading` as canonical.
* The **only genuine behavioural divergence** is the `instruments_ig.json` **`feature_flags`** block
  (chiefly `enable_regime_filter_live`), which is **PORT_REQUIRED** and carries a **regime-filter
  runtime-verification blocker**.
* The instrument **universe** (10 EPIC mappings) is byte-identical between trees but **order-capable
  mapping verification is a separate, unmet requirement** (see `ig_mapping_verification_register.md`).
* Overall readiness, blockers, and the canonical-source confirmation are stated in
  `ig_consolidation_preservation_requirements.md`.

**No code, config, broker, database, or service was changed. This is analysis only.**
