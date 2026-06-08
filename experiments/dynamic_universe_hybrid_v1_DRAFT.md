# Dynamic Universe Hybrid v1 — Master Design Draft (IBKR + IG)

> **DESIGN & FEASIBILITY ONLY — DRAFT.** No trading logic, migrations, schedulers,
> dashboard changes, broker orders, or live-service changes are implemented. Nothing is
> pushed or deployed. The frozen fixed-14 breakout Phase 3b result remains
> **PROVISIONAL / Phase-3c NO / real-money NO** and is **not** modified, rerun, tuned, or
> reinterpreted here (`experiments/breakout_3b_result.md`).
>
> Branch: `design/dynamic-universe-hybrid-v1` (based on `breakout-strategy` @ `ffd6d23`,
> which carries the deployed hard-disabled safety invariant, commit `72e00cb`).

---

## 0. Deliverable index (no-drop guarantee)

The 5 design files and the 18 "Include" items (task §13) are different lists. This index
fixes the home of each item:

| Item (§13) | Lives in |
|---|---|
| 1. Current-code integration map | **DRAFT §3** (+ sub-doc cross-refs) |
| 2. Master-registry schema | `docs/canonical_instrument_registry_design.md` §2 |
| 3. Candidate-source schema | **DRAFT §5** |
| 4. Universe-state + immutable-history schema | `docs/dynamic_universe_state_machine.md` §7 |
| 5. State-transition table | `docs/dynamic_universe_state_machine.md` §4 |
| 6. Daily scheduler across exchanges/timezones | **DRAFT §6** |
| 7. Historical-data provider feasibility + survivorship | `docs/historical_data_feasibility.md` |
| 8. Broker-neutral order-intent schema | **DRAFT §7** |
| 9. IBKR capability/gap analysis | `docs/ibkr_ig_gateway_gap_analysis.md` §2 |
| 10. IG capability/gap analysis | `docs/ibkr_ig_gateway_gap_analysis.md` §3 |
| 11. Global + broker-specific risk architecture | **DRAFT §8** |
| 12. Idempotency + reconciliation design | **DRAFT §9** |
| 13. Shadow-mode rollout plan | **DRAFT §10** |
| 14. Proposed database migrations | **DRAFT §11** |
| 15. Test plan | **DRAFT §12** (covers the §14 list) |
| 16. Security & operational risks | **DRAFT §13** |
| 17. Open decisions requiring operator approval | **DRAFT §14** |
| 18. Implementation sequence & effort | **DRAFT §15** |

The other four design docs:
`docs/canonical_instrument_registry_design.md`,
`docs/dynamic_universe_state_machine.md`,
`docs/historical_data_feasibility.md`,
`docs/ibkr_ig_gateway_gap_analysis.md`.

---

## 1. Target architecture

```text
Broad static master instrument registry        (canonical_instruments — registry doc)
        ↓
Daily candidate sources                         (candidate_sources — §5)
        ├── AUTO scanner
        ├── TTI/report candidates
        └── MANUAL operator candidates
        ↓
Structural / data eligibility                   (DQ rules — feasibility doc §5.7; → DATA_INELIGIBLE/WATCHLIST)
        ↓
Frozen deterministic breakout signal            (backtest/breakout_strategy.py — UNCHANGED)
        ↓
Portfolio risk & slot selection                 (§8 risk views; ADV/spread/id ordering — §2)
        ↓
Broker-neutral Order Intent                      (§7)
        ↓
Explicit primary gateway router                  (§6 routing)
        ├── IBKR
        └── IG
```

**Separation of the five sets** (task §1) is the spine of the whole design and is detailed
in `canonical_instrument_registry_design.md` §1. The binding invariant:

> **The daily evaluation never rewrites `instruments.json` and never mutates the master
> registry.** Daily add/remove only writes the new `candidate_sources` and `universe_state`
> tables. "Add" = becomes a candidate / entry-eligible. "Remove" = blocks *new entries*
> only; an open position is never removed from exit management.

Pinned by the §14 test "config remains unchanged by daily evaluation" (§12 test plan).

---

## 2. Approved strategy assumptions (recap; frozen pieces referenced, not re-specified)

* **Candidate sources:** AUTO, TTI, MANUAL. TTI/MANUAL expire after **5 completed trading
  sessions** unless resubmitted (TTL design in §5; session counting per exchange calendar,
  `bot/market_hours.py`). No source bypasses structural eligibility, breakout conditions,
  risk, gateway preflight, or the hard-disabled guard.
* **Structural eligibility (gate, not a trade):** ≥250 valid completed daily bars; fresh
  latest bar; price ≥ $10 (or local equiv); ADV20 ≥ $20M equiv; indicators fully defined;
  valid broker/research mapping; supported exchange/currency; no unresolved OHLC/CA anomaly;
  not hard-disabled; no active admin pause. DQ realisation: `historical_data_feasibility.md`
  §5.7. Sector data (needed downstream for the sector cap) is currently **absent** and is an
  open decision (§14).
* **Entry signal — FROZEN, unchanged** (`backtest/breakout_strategy.py:137–140`): close >
  preceding 20-day intraday high (excl. current bar, `.shift(1)`), close > SMA200, SMA50 >
  SMA200, ADX(14) > 25; computed on **completed bars only**; entry intended for the **next
  session**. Not re-implemented or altered here.
* **Risk:** 0.50% equity risk/trade; ≤20% notional/instrument; ≤5 open positions; ≤2.50%
  planned portfolio heat; ≤2 open positions/sector; no strategy leverage. Architecture in §8.
* **Exit — FROZEN, unchanged**: initial stop = entry − 2×ATR14(Wilder, signal bar); trailing
  = highest completed close since entry − 3×ATR14, monotonic ratchet (never moves down);
  trend-break = completed close < SMA50, sell next session under frozen adverse-fill rules.
  Losing entry-eligibility **never** forces liquidation → position becomes EXIT_ONLY,
  fully managed (`dynamic_universe_state_machine.md` §4).
* **Candidate priority when slots scarce — deterministic, NO alpha ranking:** (1) highest
  ADV20 $; (2) lowest estimated executable spread; (3) stable canonical instrument id as
  final tie-break. Adding any alpha rule (ADX, historical PF, Flow Score, breakout distance)
  requires a **separate pre-registration** and is explicitly out of scope here (task §2).

---

## 3. Current-code integration map (item 1)

Exact files/classes/functions the design plugs into. ✅ reuse · ➕ extend · ❌ absent.

### Strategy / signal / exits (frozen)
* `backtest/breakout_strategy.py` — `wilder_atr` (`:37`), `wilder_adx` (`:61`),
  `compute_indicators` (`:116`), entry/trend-break (`:137`,`:144`), `first_valid_signal_index`
  (`:148`). **FROZEN — do not modify.**
* `backtest/breakout_sim.py`, `backtest/simulator.py` (`CostConfig`) — frozen sim/cost path.

### Live execution / brokers
* `bot/brokers/base.py` `BaseBroker` (`:56`); `bot/brokers/__init__.py` `create_broker`
  (`:11`); `bot/brokers/ibkr.py`; `bot/brokers/ig.py`. ➕ Order Intent upstream of
  `place_order`; ❌ no broker-neutral intent today.
* `bot/connection.py:_build_contract` (`:78`) — IBKR contract build (4 fields). ➕ persist
  reference data.
* `bot/orders.py:OrderManager.place` (`:43`); `bot/order_validator.py:validate_order`
  (`:19`); `bot/sizing.py:calculate_qty` (`:12`) — ➕ IG deal-size translator (gateway doc §3.3).

### State / gating / scheduling (reuse heavily)
* `bot/regime/scheduler.py:RegimeClassificationScheduler.maybe_run` (`:56`), post-close gates
  (`:33,35`), `cache.has_for_day` dedupe (`:84`) — ✅ template for daily idempotent universe eval (§6).
* `bot/regime/flags.py:FeatureFlags` (`:12` KNOWN_FLAGS, dependency graph) — ✅ shadow/live gating (§10).
* `bot/degradation/instrument_pause_registry.py:InstrumentPauseRegistry` (`:14`) — ✅ `ADMIN_PAUSED`.
* `bot/regime/smoothing_store.py` + `bot/regime/smoothing.py` (`PENDING_DAYS_TO_PROMOTE=2`,
  0.85/0.70/0.75) — ✅ hysteresis precedent (state-machine §5).
* `bot/regime/orchestrator.py:RegimeOrchestrator.pre_trade` (`:146`) — ✅ entry-gate plumbing.
* `bot/bar_schedule.py`, `bot/market_hours.py` — ✅ exchange sessions/timezones/holidays.

### Shadow infra (reuse for shadow-first rollout)
* `bot/shadow/counterfactual_logger.py` (`shadow_decisions`, `shadow_hypothetical_trades`),
  `bot/shadow/trade_simulator.py:ShadowTradeSimulator`,
  `bot/shadow/position_metadata_store.py` — ✅ record hypothetical candidates/intents/exits.
* `bot/regime/blocked_entries.py:RegimeBlockedEntriesLog` — ✅ append-only blocked-candidate log pattern.

### Reconciliation / positions
* `bot/layer1.py:_sync_existing_positions` (`:488`), `_reconcile_with_broker` (`:94`) —
  ✅ template; ➕ generalise to canonical-id + both-broker startup reconcile (§9).
* `bot/position_tracker.py` (`open_positions`, `watch_positions`, `check_reentry` `:433`) — ✅ `COOLDOWN`.

### Safety (deployed; authoritative)
* `bot/guardrails.py:validate_hard_disabled_instruments` over `INSTRUMENT_SECTIONS`
  (commit `72e00cb`) + `api_server.py` enforcement + `save()` backstop — ✅ `HARD_DISABLED`
  authority; **not reimplemented**.

### Known integration debt (must be respected, `docs/TECH_DEBT.md`)
* Strategy-engine cluster instantiated-but-unwired; `PositionMetadataStore` unwired;
  degradation input loop unwired; dual codebase `/root/trading` vs `/root/trading-ig`;
  IG demo cash-equity entitlement block.

---

## 4. Candidate sources (AUTO / TTI / MANUAL) — roles

AUTO (scanner over the registry/universe), TTI (report-derived discovery), MANUAL (operator).
**All three are discovery inputs only** — they propose a `canonical_instrument_id` to
consider; they never create orders and never bypass the downstream gates. TTI/Claude roles
are deliberately constrained (§16 below). No PDF parsing / TTI score ingestion / Claude
overlay is built here (task §9/§10/§15) — only the candidate-source interface and schema (§5).

---

## 5. Candidate-source schema (item 3)

```text
candidate_sources
─────────────────────────────────────────────────────────────────────────
candidate_id              TEXT PRIMARY KEY        -- stable, e.g. uuid
canonical_instrument_id   TEXT NOT NULL  FK → canonical_instruments
source                    TEXT NOT NULL           -- AUTO | TTI | MANUAL (enum)
source_reference          TEXT                    -- scanner run id / TTI report id / operator ticket
added_at                  TEXT NOT NULL
effective_trading_date    TEXT NOT NULL           -- session the candidate is valid from (exchange-local)
expires_after_trading_date TEXT NOT NULL          -- effective + 5 completed sessions (TTI/MANUAL); AUTO = daily
reason_codes              TEXT                    -- JSON list of machine codes
operator_notes            TEXT
active                    INTEGER NOT NULL DEFAULT 1
created_by                TEXT NOT NULL           -- "scanner" | "operator:<user>" | "tti_import"
audit_meta_json           TEXT                    -- ingest provenance (report hash, scanner params)
─────────────────────────────────────────────────────────────────────────
```

TTL: TTI/MANUAL candidates expire after 5 completed trading sessions on the instrument's
exchange calendar unless resubmitted (re-inserted with a new `effective_trading_date`). AUTO
candidates are regenerated each session. A candidate row never enables anything by itself —
it only makes the instrument *considered* by the daily eval, which still applies structural
eligibility + frozen breakout + risk + gateway preflight + hard-disabled.

---

## 6. Daily scheduler across exchanges/timezones (item 6)

Reuses the proven pattern in `RegimeClassificationScheduler` (`bot/regime/scheduler.py`):

1. **Per-exchange post-close trigger.** Evaluate an instrument only after its exchange's
   session close (existing conservative UTC gates: LSE 17:00, US 21:30 — `scheduler.py:33,35`;
   EUR added from `bot/market_hours.py`). This makes "completed bar" well-defined per market.
2. **Idempotent per session.** Dedupe via a `has_for_session(canonical_id, session_date)`
   check mirroring `cache.has_for_day` (`scheduler.py:84`). Re-running the daily pass within a
   session is a no-op (the §14 idempotent-rerun test).
3. **Ordering of the daily pass:** refresh candidates (expire TTLs) → structural eligibility
   (DQ) → frozen breakout on completed bars → risk/slot selection (ADV/spread/id) → emit
   Order Intents (shadow-only in v1) → write `universe_state` + append `universe_state_history`.
4. **Multi-timezone "daily" cutover:** there is no single global midnight; each instrument's
   "session" is its exchange's trading day. The 5-session TTL and all "completed session"
   counts use the instrument's own calendar (`bot/bar_schedule.py`, `bot/market_hours.py`,
   DST-aware via the `holidays` library).
5. **Cadence:** the pass is invoked from the existing main loop tick (`main.py` run loop) like
   the regime scheduler — once eligible per session, idempotent thereafter. **Not built here.**

---

## 7. Broker-neutral Order Intent (item 8)

The strategy emits an **Order Intent**, never an IBKR/IG order (task §5).

```json
{
  "intent_id": "DYNBRK|US_AAPL|BUY|2026-06-08|IBKR_U1234567",
  "canonical_instrument_id": "US_AAPL",
  "strategy_id": "DYNAMIC_BREAKOUT_V1",
  "direction": "BUY",
  "signal_bar_date": "2026-06-07",
  "risk_budget_base_currency": 50.0,
  "reference_entry_price": 200.0,
  "initial_stop": 190.0,
  "maximum_notional": 2000.0,
  "primary_gateway": "IBKR",
  "candidate_source": "AUTO"
}
```

Required flow (task §5): `strategy candidate → global risk validation → broker-neutral order
intent → gateway router → gateway-specific preflight → broker-specific order`. The router
resolves `canonical_instrument_id` + `primary_gateway` against the registry, then:

* IBKR: feed registry-resolved contract into existing `place_order(contract, action, qty,
  name)` (`bot/orders.py:43`) — `qty` from `calculate_qty`.
* IG: feed EPIC + reference data into the IG **deal-size translator** (gateway doc §3.3) then
  `create_open_position(...)` (`ig.py:280`) — **never** the share-qty path.

`intent_id` doubles as the idempotency key (§9). **No order is constructed or sent in this
task.**

---

## 8. Global & broker-specific risk architecture (item 11)

Three risk views; a trade must pass **both** the global view and its gateway/account view.

```text
Global strategy risk view   (across IBKR + IG, one portfolio):
    ≤5 open positions total · ≤2.50% planned portfolio heat · ≤2 open per sector ·
    0.50% equity risk/trade · ≤20% notional/instrument · no leverage
IBKR account risk view:
    IBKR-account allocation; account-scoped open count / notional / margin
IG account risk view:
    IG-account allocation; IG margin (margin_factor), min deal size, min stop distance
```

* IBKR and IG are **separate execution allocations under one global portfolio-risk view**
  (task §6). Slot selection (ADV/spread/id, §2) runs at the global level; gateway/account
  checks run at the adapter level before the order.
* Sector cap needs the `sector` field that is **absent today** (registry doc §2; open
  decision §14). Heat = Σ(per-position risk) capped at 2.50%; risk/trade = 0.50% × equity.
* Reuses the emergency-stop concept (`BaseBroker.is_emergency_stop`) but adds per-account
  scoping (absent today — gateway doc §2.2/§3.2).

---

## 9. Idempotency & reconciliation (item 12)

### Idempotency
* Key = `strategy + canonical_instrument_id + direction + signal_bar_date + broker_account`
  (the `intent_id` above). Mirrors the scheduler's existing per-day dedupe
  (`cache.has_for_day`, `scheduler.py:84`).
* A submission timeout becomes **`UNKNOWN_PENDING_RECONCILIATION`** — **never auto-resubmit**
  an uncertain order (task §11). The instrument is paused (`InstrumentPauseRegistry`) until a
  human/recon resolves it.

### Unified position record (proposed)
```text
unified_positions
─────────────────────────────────────────────────────────────────────────
canonical_instrument_id  TEXT NOT NULL
gateway                  TEXT NOT NULL        -- IBKR | IG
account                  TEXT NOT NULL
broker_position_id       TEXT                 -- IBKR (conId+acct) / IG dealId
internal_intent_id       TEXT                 -- FK → order intent
quantity_or_deal_size    REAL NOT NULL        -- shares (IBKR) | deal size (IG)
entry_price              REAL
active_stop              REAL
currency                 TEXT
position_state           TEXT NOT NULL        -- mirrors state machine (POSITION_OPEN/EXIT_ONLY/...)
last_reconciliation_at   TEXT NOT NULL
PRIMARY KEY (canonical_instrument_id, gateway, account)
─────────────────────────────────────────────────────────────────────────
```

### Startup reconciliation (both brokers, before new entries)
Generalises `bot/layer1.py:_reconcile_with_broker` (`:94`) from single-broker/symbol-keyed
to **both IBKR and IG**, canonical-id-keyed, run **before** any new entry is allowed
(task §11). A mismatch (broker shows a position the system doesn't, or vice versa) →
**pause the affected instrument** (`InstrumentPauseRegistry`) and block duplicate exposure —
**not** an automatic force-close. (The existing 3-cycle stale-close is a single-broker
heuristic and is replaced by pause-on-mismatch for the multi-gateway case.)

---

## 10. Shadow-first rollout (item 13)

Phase 1 is **shadow-only** — it records, it does not execute (task §12). Reuses the existing
shadow stack (`bot/shadow/*`, `bot/regime/blocked_entries.py`, `FeatureFlags`
shadow/live gating with safe defaults, `flags.py`).

Shadow mode records, per session:
```text
daily candidate additions/removals          slot-contention winners + rejected candidates
daily eligibility states (universe_state)    proposed gateway per candidate
hypothetical breakout candidates             risk calculation (global + per-account)
hypothetical order intents (§7)              position→EXIT_ONLY transitions
data-quality failures (DATA_INELIGIBLE)
```

Storage: extend `shadow_decisions`/`shadow_hypothetical_trades` patterns +
`universe_state_history`. Gated behind a new `enable_dynamic_universe_shadow` flag (defaults
**false/shadow**, following the existing `KNOWN_FLAGS` convention and dependency rules,
`flags.py:12`). It **must not** alter live or paper order execution, and **must not** rewrite
`instruments.json`. The deployed hard-disabled invariant remains authoritative and unchanged.

---

## 11. Proposed database migrations (item 14 — TEXT ONLY, nothing created)

New tables (DDL is illustrative; **no migration file is created in this task**, task §15):

| Table | Home (open decision §14) | Purpose | Defined in |
|---|---|---|---|
| `canonical_instruments` | `universe.db` or `regime.db` | master registry | registry doc §2 |
| `gateway_map_ibkr` | same | IBKR reference data | registry doc §3 |
| `gateway_map_ig` | same | IG reference data + entitlement | registry doc §4 |
| `candidate_sources` | same | AUTO/TTI/MANUAL daily candidates | §5 |
| `universe_state` | same | current per-instrument state | state-machine §7.1 |
| `universe_state_history` | same | append-only audit | state-machine §7.2 |
| `unified_positions` | same | cross-gateway position records | §9 |
| `raw_bars` | research store | superset of `ohlcv` | feasibility §5.3 |
| `corporate_actions` | research store | splits/dividends/renames | feasibility §5.4 |
| `dataset_manifest` | research store | point-in-time snapshot + hashes | feasibility §5.5 |

Migration discipline: additive only; **no change** to `instruments.json` shape; **no change**
to the deployed `bot/guardrails.py` guard; reuse `regime.db` connection conventions
(WAL, busy_timeout) seen across `bot/regime/*`. Existing `backtest.db ohlcv` is left intact;
`raw_bars` supersedes it for dynamic-universe research without dropping it.

---

## 12. Test plan (item 15 — covers the task §14 list)

Each row is a required test; "Asserts" states the invariant. (Design only — **no test files
written in this task**.)

| # | Test | Asserts |
|---|---|---|
| 1 | every state transition (from×event, state-machine §4) | reaches expected `to_state`; row appended to `universe_state_history` |
| 2 | hard-disabled dominance | `HARD_DISABLED` overrides candidate/signal/admin/cooldown; never ENTRY_ELIGIBLE |
| 3 | admin pause | `ADMIN_PAUSED` blocks new entries; open pos still managed (`InstrumentPauseRegistry`) |
| 4 | data-ineligible transitions | <250 bars / stale / corrupt → `DATA_INELIGIBLE`; entries blocked |
| 5 | hysteresis | DATA_INELIGIBLE→WATCHLIST only after N_data sustained sessions |
| 6 | cooldown | post-exit/churn cooldown blocks re-entry until cleared (`check_reentry`) |
| 7 | idempotent reruns | re-running daily eval in same session = no-op; no duplicate intents/state rows |
| 8 | restart recovery | state + open positions rebuilt from DB; no lost in-flight shadow positions |
| 9 | open position becomes exit-only | any "remove" with open pos → `EXIT_ONLY`, `position_protected=1` |
| 10 | exit management continues | EXIT_ONLY/ADMIN_PAUSED/DATA_INELIGIBLE positions still get stop/trail/trend-break |
| 11 | no reversal or pyramiding from exit-only | EXIT_ONLY rejects new/reverse/add intents |
| 12 | candidate TTL expiration | TTI/MANUAL expire after 5 sessions unless resubmitted |
| 13 | AUTO/TTI/MANUAL source handling | each source recorded; none bypasses downstream gates |
| 14 | slot-contention determinism | ADV→spread→canonical-id ordering is deterministic & stable |
| 15 | sector cap | ≤2 open per sector enforced (requires `sector` field) |
| 16 | cash & portfolio-heat limits | ≤5 positions, ≤2.50% heat, 0.50%/trade, ≤20% notional |
| 17 | multiple exchanges & timezones | per-exchange session/post-close handling correct (LSE/US/EUR) |
| 18 | IBKR-only instrument | routes to IBKR; IG path not invoked |
| 19 | IG-only instrument | routes to IG; deal-size translation, not share qty |
| 20 | both-gateway instrument | uses `primary_execution_gateway`; no mirrored execution |
| 21 | gateway unavailable | reject + log; no fallback |
| 22 | no automatic fallback | failure on primary never re-routes to the other broker |
| 23 | duplicate-intent prevention | same idempotency key → single intent |
| 24 | unknown broker submission state | timeout → `UNKNOWN_PENDING_RECONCILIATION`; no auto-resubmit |
| 25 | IBKR reconciliation mismatch | mismatch → pause instrument, block duplicate exposure |
| 26 | IG reconciliation mismatch | mismatch → pause instrument, block duplicate exposure |
| 27 | config unchanged by daily evaluation | `instruments.json` byte-identical before/after a daily pass |
| 28 | XAUUSD/XAGUSD remain hard-disabled | both stay `enabled:false, hard_disabled:true` after any daily eval; reuse deployed guard tests |

---

## 13. Security & operational risks (item 16)

* **Survivorship / look-ahead bias** if current 29-survivor data is misused as a historical
  universe — mitigated by shadow-only forward operation + provider deferral (feasibility doc §4).
* **RAW-price corporate-action corruption** of frozen indicators — data-correctness risk
  (feasibility §4); mitigated by `corporate_actions` + `adj_factor` before any historical study.
* **Cross-broker double exposure** if reconciliation is incomplete — mitigated by both-broker
  startup reconcile + pause-on-mismatch + no auto-resubmit (§9).
* **IG entitlement/credentials** — demo cash-equity block (`TECH_DEBT.md`); IG creds via env
  (`ig.py:64`); audit must never log secrets (mirror deployed `HARD_DISABLED_REJECT` audit which
  logs route+symbol only).
* **Dual codebase drift** (`/root/trading` vs `/root/trading-ig`) — a router spanning both
  gateways must not depend on manual rsync; consolidation is an open decision.
* **Hard-disabled bypass** — none introduced: the design defers entirely to the deployed
  `validate_hard_disabled_instruments` guard; no parallel enable path; admin override endpoint
  explicitly **not** built.
* **Config drift** — daily eval writes only new tables; `instruments.json` untouched (test #27).
* **API/data rate limits** — IBKR pacing (`download.py:109`), IG 2.0s + weekly allowance
  (`ig.py:43`); idempotent downloader must respect them.

---

## 14. Open decisions requiring operator approval (item 17)

1. **Research-data provider** for survivorship-free, corporate-action-correct, point-in-time
   history (feasibility §5.1). *Top blocker.* No sign-up/purchase without approval.
2. **Sector/industry source** (provider GICS vs manual) — required for the sector cap.
3. **Hysteresis/cooldown numbers** (state-machine §5): `N_data=2`; session-based vs minute-based
   cooldown; the **new** churn-rule cooldown (needs separate pre-registration).
4. **DB placement**: new `universe.db` vs new tables in `regime.db`.
5. **IG scope**: keep IG execution/reconciliation-only (task default) given the demo
   entitlement block makes it unusable as a data source anyway; and whether/when to resolve
   the IG entitlement (external dependency).
6. **NBIS↔`YNDX` EPIC** re-verification before any IG action (registry doc §6).
7. **Canonical-id scheme** + dual-listing / ticker-reuse tie-break (anchor on provider permid).
8. **Codebase consolidation** (single two-gateway process) timing vs the pending PR7.
9. **Alpha ranking** in slot selection — out of scope; requires separate pre-registration if
   ever desired (task §2).

---

## 15. Implementation sequence & effort (item 18)

Phased, each phase gated by explicit operator approval (no work proceeds from this design
without it). Sizes are rough: S≈days, M≈1–2 weeks, L≈3+ weeks.

| Phase | Scope | Size | Gate |
|---|---|---|---|
| 0 | **This design** + operator review of open decisions (§14) | done | — |
| 1 | Canonical registry tables + populate from `instruments.json`/`instruments_ig.json` (verified data only) | M | approve schema + DB placement |
| 2 | Candidate-source + universe-state + history tables; daily eval **shadow-only** (no execution) | M | approve hysteresis/cooldown |
| 3 | Broker-neutral Order Intent + router + per-account risk views (shadow intents only) | M | — |
| 4 | IG reference-data capture + deal-size translator + preflight (no live calls until approved) | M | verified IG data; entitlement decision |
| 5 | Both-broker startup reconciliation + `unified_positions` + idempotency/UNKNOWN_PENDING | M | — |
| 6 | Research-data provider integration + `raw_bars`/`corporate_actions`/`manifest` + idempotent downloader | L | **provider purchase approval** |
| 7 | Promote dynamic-universe from shadow → live (gateway routing live) | M | full shadow evidence + explicit go-live approval |

Phases 1–5 are mostly additive on top of existing infra; Phase 6 is the heavy,
externally-dependent one; Phase 7 is the only one that touches live execution.

---

## 16. TTI & Claude roles (task §10) — constrained, not built

* **TTI** may later supply candidate discovery, market breadth, sector strength, Flow Score,
  score change, UDVR/DDVR, relative volume, Smart Money Exit flag. TTI inputs are **discovery
  only** — they enter as `candidate_sources` rows and still pass every downstream gate. **No
  PDF parsing / score ingestion built here** (task §9/§15).
* **Claude** is a future **structured shadow overlay** that runs *after* a deterministic
  candidate exists, classifying SUPPORTIVE / CONFLICTED / DISTRIBUTING / UNCLEAR. It may
  eventually *reduce or block* entry risk **only after evidence supports it**. Claude may
  **never** create a trade, choose direction, increase size, change stops, override
  `hard_disabled`, remove a position from exit management, change gateway mappings, or rewrite
  eligibility rules. **No Claude/TTI integration built here** (task §10/§15). This mirrors the
  existing shadow-overlay precedent (`bot/regime/orchestrator.py` shadow logging).

---

## 17. Compliance with prohibited actions (task §15) & exit discipline

Confirmed for this task: **no** DB migrations created; **no** daily scheduler implemented;
**no** Layer 1 execution change; **no** IBKR/IG order placed; **no** live/demo endpoint
called; **no** data purchased; **no** large dataset downloaded; `instruments.json` **not**
rewritten; deployed hard-disabled guard **unchanged**; frozen breakout parameters
**unchanged**; **no** Phase 3c; **no** Claude/TTI logic built; **no** live/paper
dynamic-universe trading; **no** services restarted; **nothing pushed or deployed**. All
analysis is **outcome-blind** — no returns/PF/Sharpe/rankings computed or referenced.

**This deliverable ends at: commit the 5 design docs on `design/dynamic-universe-hybrid-v1`,
then report.** Per task §16 the docs are committed (not merged, not deployed); per §15 the
branch is **not** pushed. Explicit operator approval is requested before implementing any
migration, scheduler, broker adapter, Layer 1 integration, or shadow runtime.
