# Canonical Instrument Registry — Design (Dynamic Universe Hybrid v1)

> **Design/feasibility only.** No code, migrations, or service changes. Part of the
> `design/dynamic-universe-hybrid-v1` deliverable set. See
> `experiments/dynamic_universe_hybrid_v1_DRAFT.md` for the master index.
>
> Covers deliverable **item 2** (master-registry schema) and the IBKR/IG mapping
> field sets from task §4.

---

## 1. Why a canonical registry

Today the "instrument identity" in this system **is the broker ticker string** and the
file it lives in:

* IBKR side: `instruments.json` records carry `symbol`, `sec_type`, `exchange`,
  `currency` and these are turned directly into `Stock(...)`/`CFD(...)` contracts in
  `bot/connection.py:78` (`_build_contract`). No `conId`, `primaryExchange`,
  `tradingClass`, `minTick`, `lotSize` are persisted (returned by `qualifyContracts()`
  but discarded).
* IG side: `instruments_ig.json` records carry an `ig_epic` string
  (`bot/brokers/ig.py:149` `qualify_contracts` sets `inst['contract'] = epic`). No
  `minDealSize`, `minStopDistance`, `valuePerPoint`, `margin` are fetched or stored.
* The two sides are **two separate config files in two separate checkouts**
  (`/root/trading` and `/root/trading-ig`, manually rsync'd — `docs/TECH_DEBT.md`
  "Codebase duplication"). The same economic instrument (e.g. AAPL) appears as
  `symbol:"AAPL", exchange:"SMART"` on IBKR and `ig_epic:"UA.D.AAPL.CASH.IP"` on IG,
  with **no shared key** linking them.

A dynamic universe that routes one instrument to a chosen gateway needs a single
broker-neutral identity that both adapters resolve against. That is the **canonical
instrument registry**.

### Relationship to the five "sets" (task §1)

The registry is the **Master registry** only. It is deliberately *not* the daily
candidate set, the daily eligibility state, the position-management set, or the
dynamic part of the hard-disabled set:

| Set (task §1) | Where it lives in this design |
|---|---|
| Master registry (technically known/supported) | **This document** — new `canonical_instruments` table + per-gateway mapping tables |
| Daily candidate set (AUTO/TTI/MANUAL) | `candidate_sources` table — see DRAFT.md §3 |
| Daily eligibility state | `universe_state` table — see `dynamic_universe_state_machine.md` |
| Position-management set | existing `positions.db open_positions` + new `unified_positions` — see DRAFT.md §12 |
| Hard-disabled set | **authoritative flag on the canonical record** (`hard_disabled`), enforced by the already-deployed `validate_hard_disabled_instruments` over `INSTRUMENT_SECTIONS` (commit `72e00cb`, `bot/guardrails.py`). See "Hard-disabled binding" below. |

**Invariant:** the registry is a slow-changing, human-curated, audited master list. It is
**not** rewritten by the daily evaluation. Daily add/remove only ever touches the
candidate/eligibility tables (DRAFT.md §3, §12) — never the registry and never
`instruments.json`. This is the design realisation of task §15 "do not rewrite
instruments.json daily" and the §14 test "config remains unchanged by daily evaluation."

---

## 2. Canonical instrument record (master-registry schema)

Proposed table `canonical_instruments` (home DB: a new `universe.db`, or a new set of
tables in the existing `regime.db` — see DRAFT.md §14 "Proposed migrations" for the
DB-placement open decision). Schema shown as the **proposed** shape; column types are
SQLite-flavoured.

```text
canonical_instruments
─────────────────────────────────────────────────────────────────────────
canonical_instrument_id   TEXT  PRIMARY KEY   -- broker-neutral, stable, e.g. "US_AAPL", "LSE_BARC"
display_symbol            TEXT  NOT NULL       -- human ticker, e.g. "AAPL" (NOT an identity key)
instrument_name           TEXT  NOT NULL       -- "Apple Inc."
asset_class               TEXT  NOT NULL       -- EQUITY | ETF | CFD_COMMODITY | INDEX | FX  (enum)
sector                    TEXT                 -- e.g. "Information Technology"  (NEW — absent today)
industry                  TEXT                 -- e.g. "Technology Hardware"     (NEW — absent today)
primary_listing_mic       TEXT                 -- ISO 10383 MIC, e.g. "XNAS", "XLON"
exchange                  TEXT  NOT NULL        -- routing exchange label ("SMART","LSE",...)
currency                  TEXT  NOT NULL        -- quote currency: USD | GBP | EUR | ...
quote_convention          TEXT  NOT NULL        -- MAJOR | PENCE  (GBP LSE quotes in pence — bot/currency.py)
trading_timezone          TEXT  NOT NULL        -- IANA tz, e.g. "America/New_York","Europe/London"
research_provider_symbol  TEXT                 -- vendor ticker (provider-specific)
research_provider_permid  TEXT                 -- vendor PERMANENT id (survives ticker changes)  (NEW)
listing_date              TEXT                 -- ISO date, where known                          (NEW)
delisting_date            TEXT                 -- ISO date or NULL if active                      (NEW)
administratively_active   INTEGER NOT NULL DEFAULT 1   -- operator on/off (does NOT bypass hard_disabled)
hard_disabled             INTEGER NOT NULL DEFAULT 0   -- AUTHORITATIVE safety flag (see binding below)
disabled_reason           TEXT                 -- free-text; defense-in-depth match only, never sole authority
primary_execution_gateway TEXT  NOT NULL        -- IBKR | IG   (exactly one)
supported_gateways        TEXT  NOT NULL        -- JSON list, e.g. ["IBKR"] or ["IBKR","IG"]
created_at                TEXT  NOT NULL
updated_at                TEXT  NOT NULL
created_by                TEXT  NOT NULL
─────────────────────────────────────────────────────────────────────────
```

Notes:

* **`canonical_instrument_id` is the only identity key** used by Order Intent
  (DRAFT.md §8), candidate sources, universe state, and unified positions. Broker
  ticker strings (`display_symbol`, `ig_epic`, IBKR `symbol`) are **attributes**, never
  keys. This directly fixes today's coupling where the symbol string *is* the identity.
* **`sector`/`industry`** are NEW — neither `instruments.json` nor `instruments_ig.json`
  carries them today (confirmed across all `layer1_active` keys). They are required for
  the §2 "max 2 open positions per sector" risk rule; without them the sector cap cannot
  be evaluated. Their initial source is an open decision (item 17) — likely the research
  data provider's classification (GICS-style), not guessed.
* **`research_provider_permid`, `listing_date`, `delisting_date`** are NEW and are the
  backbone of point-in-time universe construction and survivorship-bias avoidance — see
  `historical_data_feasibility.md`. They are intentionally nullable now and populated
  only from verified provider reference data.
* **`quote_convention`** generalises the existing pence handling. Today `bot/currency.py`
  hardcodes `GBP_PENCE_CURRENCIES = {"GBP"}`; the registry makes the convention an explicit
  per-instrument attribute so price/stop/notional maths is unambiguous per gateway.

### Hard-disabled binding (must not be a parallel mechanism)

The `hard_disabled` column on the canonical record **is the same invariant already
deployed** — it is not a new safety system. Authority chain:

* The deployed guard `validate_hard_disabled_instruments(config)` iterates
  `INSTRUMENT_SECTIONS = ("layer1_active","layer2_accumulation","layer3_silver")` via
  `iter_all_configured_instruments` (`bot/guardrails.py`, commit `72e00cb`) and rejects
  any **enabled** instrument carrying `hard_disabled: true` (authoritative) or the legacy
  `disabled_reason == "no_cfd_market_data_paper_account"` (defense-in-depth).
* In the dynamic-universe design, `HARD_DISABLED` is the **dominant** instrument state
  (see `dynamic_universe_state_machine.md`): it overrides every other state, can never be
  set ENTRY_ELIGIBLE / POSITION_OPEN by any daily evaluation, and can only be changed via a
  **separate, explicitly named, audited administrative endpoint** that does not exist yet
  and is **not** built in this task (task §5, §15).
* XAUUSD and XAGUSD remain `enabled:false, hard_disabled:true,
  disabled_reason:"no_cfd_market_data_paper_account"` in the live `instruments.json` and
  must remain so in the canonical registry. A named test pins this
  (`dynamic_universe_state_machine.md` test plan; DRAFT.md §15).

**Migration relationship to `instruments.json`:** the static master registry is the
*superset of identity + safety metadata*; `instruments.json` continues to be the live
operational config that the running bot loads (`bot/config.py`). The registry does not
replace it in v1. The hard-disabled set is the intersection where the two must agree, and
the deployed guard is what keeps them agreeing.

---

## 3. IBKR gateway mapping (per-canonical-id)

Proposed table `gateway_map_ibkr`, one row per canonical id routed to / supported by IBKR.
Fields chosen to (a) reproduce what `bot/connection.py` needs today and (b) capture the
reference data currently discarded after `qualifyContracts()`.

```text
gateway_map_ibkr
─────────────────────────────────────────────────────────────────────────
canonical_instrument_id  TEXT  PK, FK → canonical_instruments
con_id                   INTEGER         -- IBKR permanent contract id (NEW — currently discarded)
symbol                   TEXT  NOT NULL  -- IBKR symbol (today: inst['symbol'])
sec_type                 TEXT  NOT NULL  -- STK | CFD   (today: inst['sec_type'], bot/connection.py:78)
exchange                 TEXT  NOT NULL  -- "SMART","LSE",...
primary_exchange         TEXT            -- e.g. "NASDAQ"  (NEW — currently discarded)
currency                 TEXT  NOT NULL
trading_class            TEXT            -- (NEW — currently discarded)
min_tick                 REAL            -- (NEW — currently discarded)
lot_size                 REAL            -- (NEW — currently discarded)
what_to_show             TEXT            -- "TRADES"(STK) | "MIDPOINT"(CFD), mirrors bot/data.py:56
verified_against         TEXT            -- "ib.qualifyContracts" + timestamp of last verification
─────────────────────────────────────────────────────────────────────────
```

Reality check (from code map):

* `bot/connection.py:_build_contract` builds `Stock`/`CFD` from exactly four fields
  (`symbol`, `exchange`, `currency`, `sec_type`). The other IBKR fields above are
  **populated by `qualifyContracts()` at runtime but never persisted** — so today they are
  re-fetched on every startup and never auditable. The registry persists them with a
  `verified_against` timestamp.
* `con_id` is IBKR's stable permanent id and is the natural IBKR counterpart to
  `research_provider_permid`. Recommended to capture it so a ticker rename does not silently
  re-point a contract.

---

## 4. IG gateway mapping (per-canonical-id)

Proposed table `gateway_map_ig`, one row per canonical id routed to / supported by IG. **All
EPIC and reference values must come from verified IG account/reference data** (task §4 "Do
not guess IG EPICs"). For instruments without verified reference data the row's resolvable
fields are explicitly `UNRESOLVED`.

```text
gateway_map_ig
─────────────────────────────────────────────────────────────────────────
canonical_instrument_id  TEXT  PK, FK → canonical_instruments
epic                     TEXT            -- IG EPIC, e.g. "KA.D.BARC.CASH.IP"; "UNRESOLVED" if unverified
instrument_type          TEXT            -- e.g. SHARES | CURRENCIES | INDICES | COMMODITIES (from IG market info)
expiry                   TEXT            -- "DFB" default (bot/brokers/ig.py:282), or contract expiry
account_compatibility    TEXT            -- which IG account types can trade it (demo entitlement matters — see below)
currency                 TEXT
min_deal_size            REAL            -- (NEW — not fetched/stored today)
min_stop_distance        REAL            -- points (NEW — not fetched/stored today)
value_per_point          REAL            -- contract-size semantics (NEW — not fetched/stored today)
margin_factor            REAL            -- (NEW — not fetched/stored today)
market_hours_meta        TEXT            -- JSON from IG market info (NEW)
data_entitlement         TEXT            -- "OK" | "NO_EQUITY_HISTORY" (see TECH_DEBT entitlement gap)
verified_against         TEXT            -- "ig.fetch_market_by_epic" + timestamp; or "UNVERIFIED"
─────────────────────────────────────────────────────────────────────────
```

### Verified EPICs available *today* (from `instruments_ig.json` — read directly, not guessed)

These are the **only** EPICs present in the repo. They are recorded here verbatim and must
still be re-verified against live IG reference data before any future order (task §7
preflight). Note the symbol↔EPIC divergences — they are exactly why a canonical id is
needed:

| canonical (proposed) | display_symbol | verified `ig_epic` (from file) | ccy | note |
|---|---|---|---|---|
| US_AAPL  | AAPL | `UA.D.AAPL.CASH.IP`   | USD | |
| US_AVGO  | AVGO | `UA.D.AVGO.CASH.IP`   | USD | |
| US_MSFT  | MSFT | `UC.D.MSFT.CASH.IP`   | USD | |
| US_PLTR  | PLTR | `SE.D.PLTRUS.CASH.IP` | USD | EPIC stem `PLTRUS` ≠ symbol `PLTR` |
| US_NBIS  | NBIS | `UD.D.YNDX.CASH.IP`   | USD | EPIC stem `YNDX` (legacy Yandex) ≠ symbol `NBIS` — **flag for re-verification** |
| LSE_BARC | BARC | `KA.D.BARC.CASH.IP`   | GBP | |
| LSE_ANTO | ANTO | `KA.D.ANTO.CASH.IP`   | GBP | |
| LSE_SGLN | SGLN | `KA.D.SGLNLN.CASH.IP` | GBP | EPIC stem `SGLNLN` |
| LSE_SSLN | SSLN | `KA.D.SSLNLN.CASH.IP` | GBP | EPIC stem `SSLNLN` |
| EU_SU    | SU   | `EC.D.SU.CASH.IP`     | EUR | |

**Everything not in the table above → `epic = "UNRESOLVED"`** in the registry until
verified IG reference data is supplied. This includes every other instrument in
`backtest.db`'s 29-symbol set (AMZN, ANET, ASML, CEG, CRWD, CVX, FCX, GOOGL, META, MRVL,
MU, NBIS-as-NBIS, NVDA, NVTS, SCCO, SHEL, TSM, VRT, XAGUSD, XAUUSD, ...).

### Critical IG constraint that the registry must record (`data_entitlement`)

`docs/TECH_DEBT.md` ("IG demo account lacks cash-equity historical-data entitlement",
status *Blocking IG-side regime pipeline*) documents that the IG demo account rejects
historical-bar requests on `*.CASH.IP` epics with
`unauthorised.access.to.equity.exception`, affecting **all 10** configured equities. The bot
"currently operating in mode 3 by default" (IG runs but does not trade or classify).
Consequence for this design: **IG cannot be a historical research-data source** for these
equities today (reinforced in `historical_data_feasibility.md`), and the `data_entitlement`
column records that fact per instrument so the router/preflight can reason about it instead
of failing opaquely at fetch time.

---

## 5. How the adapters resolve against the registry (no code change in this task)

Proposed (future) resolution flow, reusing existing seams:

```text
canonical_instrument_id
   → registry lookup (canonical_instruments + gateway_map_<gateway>)
   → build the broker-native contract:
        IBKR: feed symbol/sec_type/exchange/currency into bot/connection.py:_build_contract (unchanged logic)
        IG  : feed epic/expiry/currency into bot/brokers/ig.py preflight + create_open_position (unchanged logic)
```

The existing `BaseBroker.qualify_contracts(instruments: list[dict])`
(`bot/brokers/base.py:92`) already takes instrument dicts and returns qualified dicts. The
registry is an upstream *source* of those dicts; the adapter interface does not need to
change for v1. This keeps the design additive and consistent with the CLAUDE.md rule
"wrap, don't rewrite."

---

## 6. Open decisions for this document (see DRAFT.md item 17)

1. **DB placement** of the registry tables: new `universe.db` vs. new tables in `regime.db`.
2. **`sector`/`industry` source**: provider GICS classification vs. manual operator entry.
   Required before the sector cap can run.
3. **Canonical-id scheme**: `<REGION>_<SYMBOL>` proposed; needs a tie-break rule for dual
   listings and for ticker reuse after delisting (the `research_provider_permid` is the
   real anchor).
4. **NBIS↔YNDX EPIC**: the `UD.D.YNDX.CASH.IP` mapping must be re-verified — it may be a
   stale legacy mapping. Do not trade it until confirmed.
5. Whether the registry **supersedes** `instruments.json` in a later phase or remains a
   parallel source-of-identity with `instruments.json` as live config (v1 assumes the
   latter).
