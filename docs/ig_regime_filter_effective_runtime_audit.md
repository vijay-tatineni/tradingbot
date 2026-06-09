# IG Regime-Filter Effective Runtime Audit (Workstream 2)

> **Read-only.** No flag, config, code, or service was changed; no broker call was made. Traces the
> **deployed IG code** at `/root/trading-ig` (`claude-strategy` @ `d95d258`) — not the modern tree.
> Inspection date **2026-06-09 (UTC)**. Runtime facts and provenance: `ig_runtime_config_provenance.md`.

## Contradiction to resolve

`instruments_ig.json` sets `enable_regime_filter_live: true`, yet operational understanding held that
IG might not actually be filtering entries. This audit resolves it with code-path tracing + log
evidence.

---

## 1. Full call-path trace (deployed IG code, exact line numbers)

| # | Question | Finding (file:line) |
|---|---|---|
| 1 | How are feature flags loaded? | `main.py:140` reads `settings.feature_flags`; `main.py:141` builds `FeatureFlags(flag_config)` (`bot/regime/flags.py`). |
| 2 | Defaults when a flag is absent? | `bot/regime/flags.py:43` `SAFE_DEFAULTS["enable_regime_filter_live"] = False`; loader warns and applies the default (`flags.py:83-91`). "Missing config keys never enable live behaviour." |
| 3 | Dependency validation? | `flags.py:62` `enable_regime_filter_live` requires `enable_classifier_shadow`; `_validate_dependencies` (`flags.py:95-104`) raises `ConfigError` at startup if unmet. Live IG config sets `enable_classifier_shadow: true`, so it passes. |
| 4 | Does `enable_regime_filter_live` resolve true or false? | **True** in the running process (PID 503086, started 2026-06-03). Startup summary logged `regime_filter: live=true`. (An earlier epoch resolved `false` when the config lacked `feature_flags`.) |
| 5 | Is the regime orchestrator/plugin constructed? | **Yes.** `main.py:158-172` builds `RegimeOrchestrator(...)`; `main.py:172` `register_plugin(self.orchestrator)`. |
| 6 | Are regime stores/schedulers initialized? | **Yes.** `main.py:150` `SmoothedStateStore`; `main.py:174-181` `RegimeClassificationScheduler(...)`. |
| 7 | Does Layer 1 call `apply_regime_filter()` on IG entry candidates? | **Yes — structurally in the entry path.** `bot/layer1.py:186` `_regime_filter_allows(...)` iterates plugins' `apply_regime_filter` (`layer1.py:196`), and is invoked in the reversal/entry branches at `layer1.py:332, 367, 405, 443` (each sets `action = "REGIME FILTER BLOCKED"` on block). |
| 8 | Does the block/allow result affect actual entry submission? | **Yes (by design).** A `False` return sets the candidate to `REGIME FILTER BLOCKED` and the entry/`handle_signal` submission is skipped. |
| 9 | Fail-open or fail-closed when regime data is unavailable? | **Mixed, by branch** (`bot/regime/orchestrator.py:apply_regime_filter`, line 241): flag off → allow (`:269-270`); **store-lookup exception → allow (fail-OPEN)** (`:276-281`); smoothed regime `TRENDING` → allow (`:283-287`); **no smoothed row (warm-up / None) or non-TRENDING → BLOCK (fail-CLOSED)** (`:288+`). So *missing data* fails **closed** (block), but an *infrastructure exception* fails **open** (allow). |
| 10 | Any separate IG-only path that bypasses the filter? | **No.** The deployed IG `layer1.py` routes all entry/reversal candidates through `_regime_filter_allows`; the IG broker adapter (`bot/brokers/ig.py`, byte-identical to modern) has no parallel entry path. |

**Structural conclusion:** the filter is wired, the flag is true, and Layer 1 calls it on every entry
candidate. So the contradiction is **not** "flag off" and **not** "filter not in the path."

---

## 2. Runtime evidence — does any entry candidate ever reach the filter?

All 10 IG instruments are **cash equities** (`*.CASH.IP`). The decisive causal chain, from logs:

```text
*.CASH.IP bar fetch  →  IG demo: "unauthorised.access.to.equity.exception"  (33,656 occurrences;
                         the LAST line of bot_stderr.log — current/dominant state)
   →  bot/layer1 fetch_bars returns None  →  _closed_row  →  NO signal evaluated
   →  apply_regime_filter is NEVER invoked with a live signal
   →  "REGIME FILTER BLOCKED" count = 0   (across the entire retained window)
```

Independently corroborated by the regime classifier: `logs/regime/regime.log.*` records
`[Scheduler] <SYMBOL> classification failed: insufficient bars: got 0, need 200` for every
instrument → **zero smoothed-regime rows are ever produced** → even if a signal reached the filter,
`get_latest()` would return `None`.

Other runtime facts:

* `Verified IG epic` (successful `fetch_market_by_epic`) = **0** in the retained window; 548
  `NoneType … fetch_market_by_epic` (qualify ran while not logged in) — no positive reference
  resolution of any EPIC at runtime.
* Retained window: `portfolio_bot.log` spans **2026-04-16 → 2026-06-09** (cycle #1129); the
  `bot_stdout.log` deployment history reaches back to 2026-04-16. This is a long, representative
  window — **no candidate reached the filter during it.**
* Per task: this is **proof the filter did not gate any entry**, not mere absence of a log line — it
  is corroborated by the upstream entitlement failure and the "0 bars" classifier failures.

---

## 3. Required verdict

```text
CONFIGURED_TRUE_BUT_RUNTIME_FALSE
```

**Definition used (to avoid ambiguity):** the flag is **configured true and loaded true** in the
running process (see point 11 below), **but the filter's runtime *effect* is inert** — it has
**never evaluated or blocked a live IG entry** in the retained window, because the IG demo account
cannot deliver cash-equity bars (entitlement block), so neither regime classification nor entry
signals are produced. "Runtime false" denotes the **null effective behaviour**, *not* a false flag
value. (This is distinct from the verdict items below: point 11 = flag value = true; point 14 =
this verdict.)

Rejected alternatives: `ENABLED_AND_EFFECTIVE` (0 blocks, 0 smoothed rows, 0 entries);
`ENABLED_BUT_NOT_IN_ENTRY_PATH` (Layer 1 *does* call it at 332/367/405/443); `INDETERMINATE` (the
evidence positively shows zero effect, not uncertainty); `DISABLED_EFFECTIVE` (the flag is not off in
the current epoch — the *configured intent* is on).

---

## 4. Required answers

| Question | Answer |
|---|---|
| **Would moving the IG service to `/root/trading` change current entry behavior?** | **Not today** — IG generates **zero** equity entries under the entitlement block either way. But there is a **latent** divergence: the modern `instruments_ig.json` **lacks** `feature_flags` → the flag would **default false**, whereas the live config = true. **If IG cash-equity access were ever restored**, the flag would matter and behaviour would diverge. |
| **Would preserving the current IG `feature_flags` block preserve actual behavior?** | It preserves the **configured intent** (filter on) and the **future** behaviour-on-data-restoration; it does **not** change today's null behaviour. |
| **Would omitting it silently alter behavior?** | **Today: no observable change.** **Latently: yes** — omitting `feature_flags` flips the flag to false-default, silently disabling a filter the live config intends to be on. Note the fail-**closed** subtlety: with flag=true and no smoothed rows, a *reached* entry would be **blocked** (warm-up default), while a store *exception* fails open. So the on→off flip is consequential the moment IG data is restored. |
| **What must be frozen before consolidation?** | (a) the `feature_flags` block (esp. `enable_regime_filter_live` and its intended value); (b) the operational fact that the IG account currently **cannot fetch cash-equity bars** (entitlement block; earlier transient `api-key-invalid`) — the IG bot is effectively in "runs-but-does-not-trade/classify" mode; (c) the regime-data pipeline state (no smoothed rows ever) — so post-cutover regime behaviour must be re-verified, not assumed. |

**No flag, config, code, or service was modified. Analysis only.**
