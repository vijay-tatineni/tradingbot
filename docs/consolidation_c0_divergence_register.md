# Consolidation C0 — Divergence Register (Workstream B)

> Compares `/root/trading-ig` (claude-strategy @ `d95d258`, working tree) against the proposed
> canonical modern line `/root/trading` (breakout-strategy / `e866842`, working tree). **Nothing
> was ported, merged, or modified.** Comparison method: working-tree-to-working-tree `diff`
> (`__pycache__`/`.pyc` excluded). No file contents are quoted that could carry secrets.

## Classification legend

`IG_REQUIRED` · `SHARED_FEATURE_MISSING_FROM_OLD_BRANCH` · `STALE_DUPLICATE` ·
`LOCAL_CONFIG_ONLY` · `GENERATED_OR_RUNTIME_STATE` · `UNKNOWN_REQUIRES_REVIEW` ·
`SECRET_OR_ENVIRONMENT_SPECIFIC`

## 1. Tracked-modified files in `/root/trading-ig`

| File | Δ vs modern (modern-only / IG-only lines) | Classification | Note |
|---|---|---|---|
| `bot/brokers/ig.py` | 0 / 0 — **byte-identical** | `IG_REQUIRED` (already merged-forward; no action) | IG adapter already lives identically in modern tree |
| `bot/alerts.py` | 0 / 0 — identical | `SHARED_FEATURE` (no action) | |
| `bot/plugins/base_plugin.py` | 0 / 0 — identical | `SHARED_FEATURE` (no action) | |
| `tests/test_ig_broker.py` | 0 / 0 — identical | `SHARED_FEATURE` (no action) | |
| `api_server.py` | 109 / 0 | `SHARED_FEATURE_MISSING_FROM_OLD_BRANCH` | modern strictly ahead; IG copy fully superseded |
| `bot/dashboard.py` | modern ahead (P&L liveness logic) | `SHARED_FEATURE_MISSING_FROM_OLD_BRANCH` | IG lacks active-currency P&L preserve logic |
| `main.py` | modern ahead | `SHARED_FEATURE_MISSING_FROM_OLD_BRANCH` ⚠ **safety** | IG **lacks** `validate_no_edge_guardrails` + `validate_hard_disabled_instruments` |
| `bot/layer1.py` | 73 / **21** | `UNKNOWN_REQUIRES_REVIEW` | 21 IG-only lines = same regime-filter/shadow/handle_signal blocks at different nesting; modern adds SIGNAL_REVERSED/allow_new_entries suppression. **CLAUDE.md forbids modifying layer1 logic** — review, do not edit. |
| `instruments_ig.json` | 20-line delta | `IG_REQUIRED` + `LOCAL_CONFIG_ONLY` | IG universe: `ig_epic`, `broker`, `ig_acc_type` keys (no account-number value in file). 10 enabled layer1 instruments. |
| `CLAUDE.md` | 24-line delta | `LOCAL_CONFIG_ONLY` | IG-instance ports/topology (8083) vs modern |
| `web/dashboard.html` | 4-line delta | `LOCAL_CONFIG_ONLY` | IG labels/title; nav-bar requirements still apply |
| `web/instruments.html` | 3-line delta | `LOCAL_CONFIG_ONLY` | IG labels/title |

## 2. Untracked directories in `/root/trading-ig`

All of these **already exist as tracked source in the modern tree** — the IG copies are stale
duplicates, not unique IG work.

| IG untracked dir | In modern (tracked)? | IG copy differs? | Classification |
|---|---|---|---|
| `bot/regime/` | yes (21 files) | **1 file** (`cost_tracker.py`) | `STALE_DUPLICATE` + that file `UNKNOWN_REQUIRES_REVIEW` |
| `bot/shadow/` | yes (6) | identical | `STALE_DUPLICATE` |
| `bot/overlays/` | yes (9) | identical | `STALE_DUPLICATE` |
| `bot/degradation/` | yes (6) | identical | `STALE_DUPLICATE` |
| `bot/strategies/` | yes (6) | identical | `STALE_DUPLICATE` |
| `bot/calendar_ui/` | yes (5) | identical | `STALE_DUPLICATE` |
| `tests/{regime,shadow,overlays,degradation,strategies,…}/` | yes (tracked) | n/a | `STALE_DUPLICATE` |
| `docs/`, `specs/` | partly (modern has docs/) | n/a | `UNKNOWN_REQUIRES_REVIEW` (check for IG-only notes) |
| `logs/` | gitignored | n/a | `GENERATED_OR_RUNTIME_STATE` |
| `.env`, `users.json`, `*.db`, `*.log`, `__pycache__` | gitignored | n/a | `SECRET_OR_ENVIRONMENT_SPECIFIC` / `GENERATED_OR_RUNTIME_STATE` |

## 3. Per-IG-required-change detail (documentation only — NO porting)

| Item | Purpose | Source file | Function/class | Equivalent in `/root/trading`? | Porting risk | Tests needed | Affects live execution? |
|---|---|---|---|---|---|---|---|
| IG broker adapter | route orders/bars to IG | `bot/brokers/ig.py` | `IGBroker` | **YES — identical already** | none (no port needed) | existing `test_ig_broker.py` (identical) | yes (IG path) |
| IG universe config | define IG-traded instruments + EPIC mapping | `instruments_ig.json` | n/a (config) | modern has an `instruments_ig.json`; the 20-line delta is the IG universe | low–med: must keep EPIC↔symbol mapping + per-instrument flags exact | config-load + dashboard render tests | **yes** — defines what IG trades |
| IG CLAUDE.md / web labels | IG-instance ports (8083), nav labels | `CLAUDE.md`, `web/*.html` | n/a | modern has its own | low | nav-bar presence check (CLAUDE.md rule) | no |
| `layer1.py` 21 IG-only lines | regime-filter/shadow ordering | `bot/layer1.py` | signal loop | modern has superset + extra suppression | **review-only** (CLAUDE.md: do not modify layer1 logic) | golden trade-list equivalence | yes (signal gen) — **do not touch without behavioural-equivalence proof** |
| `bot/regime/cost_tracker.py` | regime cost tracking | `bot/regime/cost_tracker.py` | — | modern version differs | med (which is newer?) | regime tests | shadow/regime only |

## 4. Divergence classification counts

```text
IG_REQUIRED                              2   (ig.py [already merged], instruments_ig.json)
SHARED_FEATURE / identical (no action)   3   (alerts.py, base_plugin.py, test_ig_broker.py)
SHARED_FEATURE_MISSING_FROM_OLD_BRANCH   3   (api_server.py, dashboard.py, main.py[safety])
STALE_DUPLICATE                          6+  (regime, shadow, overlays, degradation, strategies, calendar_ui + their tests)
LOCAL_CONFIG_ONLY                        4   (instruments_ig.json[also IG_REQUIRED], CLAUDE.md, web/dashboard.html, web/instruments.html)
UNKNOWN_REQUIRES_REVIEW                  3   (layer1.py 21 lines, regime/cost_tracker.py, docs/+specs/)
GENERATED_OR_RUNTIME_STATE               n   (*.db, *.log, logs/, __pycache__)
SECRET_OR_ENVIRONMENT_SPECIFIC           n   (.env, users.json)
```

## 5. Unique IG changes requiring preservation (headline)

Only **two** artifacts are genuinely IG-unique and must survive consolidation:

1. **`instruments_ig.json`** — the IG universe + EPIC mapping + per-instrument flags (`IG_REQUIRED`).
2. **IG local config** — IG `CLAUDE.md` (ports 8083), web labels (`LOCAL_CONFIG_ONLY`).

Everything else is the IG tree being **behind** the modern tree (the IG adapter and all shared
subsystems already exist identically or as supersets in `/root/trading`). The `layer1.py` 21-line
delta and `regime/cost_tracker.py` are **review items**, not confirmed IG-required logic, and
**must not be edited** without a behavioural-equivalence proof (CLAUDE.md Regime rule).
