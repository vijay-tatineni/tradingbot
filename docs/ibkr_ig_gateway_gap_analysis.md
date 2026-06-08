# IBKR & IG Gateway — Capability / Gap Analysis (Dynamic Universe Hybrid v1)

> **Design/feasibility only.** No broker calls, no orders, no demo/live endpoint
> invocation (task §15). Part of `design/dynamic-universe-hybrid-v1`; master index
> `experiments/dynamic_universe_hybrid_v1_DRAFT.md`. Covers deliverable **item 9**
> (IBKR capability/gap) and **item 10** (IG capability/gap). The broker-neutral Order
> Intent and the global/account risk-view architecture live in DRAFT.md §8/§11.

---

## 1. Shared baseline: the existing broker abstraction

Both gateways already sit behind one interface — this is the seam the dynamic-universe
router plugs into, and the reason the design is additive.

* `BaseBroker(ABC)` — `bot/brokers/base.py:56`. Abstract methods incl. `connect`,
  `is_connected`, `reconnect`, `sleep`, `qualify_contracts(instruments: list[dict])`
  (`:92`), `fetch_bars(contract, days=300, bar_size='1 day')` (`:102`),
  `place_order(contract, action, qty, name) -> FillResult` (`:128`),
  `close_position`, `get_position`, `get_position_info`, `get_all_positions() ->
  list[BrokerPosition]` (`:192`), `is_emergency_stop`.
* Dataclasses: `BrokerPosition` (`base.py:18`), `FillResult` (`base.py:32`),
  `PositionInfo` (`base.py:43`).
* Factory: `create_broker(broker_type, cfg)` — `bot/brokers/__init__.py:11`; routed by
  `settings.broker` ("ibkr"|"ig") in `instruments.json`, overridable via `--broker`
  (`main.py`).
* Sizing is shared and **quantity-based**: `calculate_qty(instrument, price,
  default_target_notional)` (`bot/sizing.py:12`) computes integer share/unit qty from a
  target notional, written into `inst['qty']` before either adapter is called
  (`bot/layer1.py:244`).

**Key shared gap:** there is **no broker-neutral order-intent object**. Both adapters take
`(contract, action, qty, name)` and build broker-native orders inline (IBKR `Order` in
`bot/orders.py:51`; IG `create_open_position(...)` in `bot/brokers/ig.py:280`). The dynamic
universe introduces an Order Intent *upstream* of `place_order` (DRAFT.md §8); the adapter
signatures need not change for v1.

---

## 2. IBKR — capability / gap analysis (item 9)

### 2.1 Capabilities present

| Capability | Status | Reference |
|---|---|---|
| Contract qualification | ✅ builds `Stock`/`CFD` from symbol/exchange/currency/sec_type | `bot/connection.py:78` |
| Historical bars (multi-year for live names) | ✅ `reqHistoricalData` (`durationStr` years OK) | `backtest/download.py:70`, `bot/data.py:57` |
| Recent-bar fetch with staleness guard | ✅ rejects >4-day-old last bar | `bot/data.py:74` |
| Market order placement (shares) | ✅ MKT only, sync fill wait ≤30s | `bot/orders.py:51` |
| Position read + P&L | ✅ via `ib.positions(account)` | `bot/portfolio.py:35` |
| Emergency stop / portfolio loss limit | ✅ | `BaseBroker.is_emergency_stop` |
| Per-cycle reconciliation (broker↔tracker) | ✅ add-missing + 3-cycle stale close | `bot/layer1.py:94` |
| Multi-currency quote handling (pence) | ✅ GBP pence↔pounds | `bot/currency.py` |

### 2.2 Gaps for dynamic universe

| Gap | Impact | Effort (rough) |
|---|---|---|
| `conId`, `primaryExchange`, `tradingClass`, `minTick`, `lotSize` qualified at runtime but **not persisted** | no auditable, rename-proof identity; re-qualified every startup | S — persist into `gateway_map_ibkr` (registry doc §3) |
| No broker-neutral order intent | strategy would construct IBKR orders directly (forbidden by task §5) | M — intent + router (DRAFT.md §8) |
| Order types limited to MKT | initial-stop / stop placement is strategy-side, not broker-side bracket | M — design choice; v1 keeps strategy-side stops as today |
| No IBKR-account-scoped risk view | only a global loss limit exists; no per-account allocation | M — `IBKR account risk` view (DRAFT.md §11) |
| No idempotency key on submission | a timeout could double-submit on naive retry | M — idempotency design (DRAFT.md §12) |
| Reconciliation uses symbol string, not canonical id | dual-listing / rename ambiguity | S — key reconciliation on canonical id |
| No split/dividend awareness | RAW bars (see feasibility doc) | M/L — provider-dependent |

### 2.3 IBKR-specific reconciliation note

The existing `_reconcile_with_broker` (`layer1.py:94`) is **single-broker** and
**symbol-keyed**, and forces a stale close after a 3-cycle counter using the market price.
For the dynamic universe this must be generalised to: (a) canonical-id keying, (b) a
*both-broker* startup reconcile **before** new entries (DRAFT.md §12), and (c) on mismatch,
pause via `InstrumentPauseRegistry` rather than force-close. The existing logic is a sound
template, not a drop-in.

---

## 3. IG — capability / gap analysis (item 10)

### 3.1 Capabilities present

| Capability | Status | Reference |
|---|---|---|
| Session auth (REST) | ✅ `create_session()`, retry | `bot/brokers/ig.py:98` |
| EPIC qualification | ✅ `fetch_market_by_epic(epic)`, sets `inst['contract']=epic` | `ig.py:149` |
| Historical bars by epic | ✅ `fetch_historical_prices_by_epic`, 2.0s floor | `ig.py:207` |
| Deal placement | ✅ `create_open_position(currency_code, direction, epic, MARKET, expiry, size, ...)` | `ig.py:280` |
| Position read (cached 30s) | ✅ `fetch_open_positions()` → `BrokerPosition` | `ig.py:503` |
| EPIC→symbol resolution | ✅ scans config `ig_epic` | `ig.py:583` |

### 3.2 Gaps for dynamic universe (and IG-specific risk translation, task §7)

| Gap | Impact | Effort |
|---|---|---|
| `minDealSize`, `minStopDistance`, `valuePerPoint`, `marginFactor`, `instrumentType`, market-hours meta **not fetched/stored** | cannot translate a common risk budget into a valid IG deal size or validate min-stop — **blocks correct IG sizing** | M — fetch from market info → `gateway_map_ig` (registry doc §4) |
| Sizing reuses share-qty (`calculate_qty`) for IG | **wrong** for IG point/contract semantics; task §7 forbids treating IG like IBKR shares | M — IG-specific deal-size translator (DRAFT.md §7) |
| Demo account lacks cash-equity history entitlement | IG equities don't trade or classify today; "mode 3" dashboard-only | External — `docs/TECH_DEBT.md` (Blocking); operator decision (item 17) |
| Only 10 verified EPICs exist; rest UNRESOLVED | universe coverage on IG is tiny; must not guess EPICs (task §4) | Data — verified IG reference data needed |
| EPIC↔symbol divergence (NBIS→`YNDX`, PLTR→`PLTRUS`) | stale/incorrect mapping risk | S — re-verify; canonical id anchors it |
| No deterministic preflight gate | failure path undefined | M — preflight (below) |
| No IG-account risk view | no per-account allocation | M — `IG account risk` view (DRAFT.md §11) |
| Async deal confirmation / dealReference handling for idempotency | timeout → unknown state | M — `UNKNOWN_PENDING_RECONCILIATION` (DRAFT.md §12) |

### 3.3 IG deal-size translation (task §7) — design

The IG adapter must translate the **common risk budget** (base-currency risk, from the Order
Intent) into an IG-specific deal size using: `EPIC`, `instrument_type`, `currency`,
`value_per_point`/contract size, `min_deal_size`, `min_stop_distance`, `margin_factor`,
executable spread. None of these are captured today; they are added to `gateway_map_ig`.
The translation is **not** `calculate_qty`'s share formula. **Design only — not built here.**

### 3.4 IG preflight gate (task §7) — design (no calls in this task)

Before any future IG order, the adapter must verify, and on any failure return a
**deterministic rejection state (never fall back to IBKR)**:

```text
authenticated session            EPIC resolved & market tradeable
correct account                  instrument available to this account (entitlement!)
currency known                   min deal size satisfied
min stop distance satisfied      margin within IG-account allocation
no duplicate open deal           no working duplicate order
no unresolved prior submission (UNKNOWN_PENDING_RECONCILIATION)
```

Failure → reject + audit (mirror the deployed `HARD_DISABLED_REJECT` audit pattern,
`api_server.py` audit logger), pause the instrument via `InstrumentPauseRegistry`. The
entitlement check is first-class because of the documented demo-account block.

---

## 4. Cross-gateway routing summary (full rules in DRAFT.md §6)

* Each canonical instrument has **exactly one** `primary_execution_gateway` (registry).
* **No automatic broker fallback, no mirrored/duplicate cross-broker execution.** Gateway
  unavailable → reject + log (task §6).
* Three risk views — Global / IBKR-account / IG-account — and a trade must pass **both**
  global and its gateway/account limits (DRAFT.md §11).
* The dual codebase (`/root/trading` vs `/root/trading-ig`, `docs/TECH_DEBT.md`) is itself a
  gap: a single-process two-gateway router is the target end-state; v1 design assumes the
  router lives on the IBKR side and treats IG as a routed gateway, consolidation tracked as
  an open decision (item 17).

---

## 5. Effort roll-up (gateways only; full sequence in DRAFT.md item 18)

| Workstream | Size |
|---|---|
| Persist IBKR reference data into registry | S |
| Broker-neutral Order Intent + router | M |
| IG reference-data fetch + `gateway_map_ig` population | M (needs verified IG data) |
| IG deal-size translator | M |
| IG preflight gate + deterministic rejection | M |
| Both-broker startup reconciliation, canonical-id keyed | M |
| Per-account risk views | M |
| Resolve IG demo entitlement (external) / decide IG scope | external + decision |
