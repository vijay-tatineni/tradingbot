# Historical-Data Provider Selection Matrix — Dynamic Universe Hybrid v1

> **Design/planning only. Outcome-blind.** No provider was signed up for, authenticated
> to, purchased from, or downloaded from. No strategy results, PF, Sharpe, or returns are
> computed. Part of `design/dynamic-universe-hybrid-v1`; master index
> `experiments/dynamic_universe_hybrid_v1_DRAFT.md`. This is **Task 2** of the consolidation
> planning set.

## Evidence labelling (read first)

Every cell is tagged:

* **[V] verified** — from current repository evidence (code/config/`.env`/`requirements.txt`)
  that I inspected directly. Only the two already-integrated providers (IBKR, IG) and the two
  auxiliary ones (Finnhub, yfinance) get [V] facts, and only for what the repo actually shows.
* **[I] inferred** — from public provider documentation as understood at my **knowledge
  cutoff (Jan 2026)**. These are **not re-verified against live provider docs** in this task
  (doing so would mean authenticating/calling, which is prohibited). **Treat every [I] as
  "re-verify before relying on it."**
* **[U] unknown** — not determinable without authenticating, a paid trial, or a sales quote.

Pricing is given only as **tier category** with an [I]/[U] tag — **no exact price is
asserted**; pricing and licensing change frequently and must be confirmed with the vendor
under operator approval before any purchase.

---

## 1. Providers already present in the repository (verified)

| Provider | Role in repo today | Evidence |
|---|---|---|
| **IBKR** (`ib_insync==0.9.86`) | Primary live + backtest **OHLCV** via `reqHistoricalData` | [V] `bot/data.py:57`, `backtest/download.py:70`, `requirements.txt` |
| **IG** (`trading-ig>=0.0.23`) | Execution + on-demand bars via `fetch_historical_prices_by_epic`; **equity history blocked on demo** | [V] `bot/brokers/ig.py:207`; entitlement block `docs/TECH_DEBT.md` |
| **Finnhub** (`FINNHUB_API_KEY`) | **News headlines only** (sentiment gate), **not price bars** | [V] `bot/llm/news_collector.py:164–200`, free tier 60/min, US-only |
| **yfinance** | **Research/eval scripts only** (classifier labelling/forward-returns), **not** the live data path; not in `requirements.txt` | [V] `scripts/eval_classifier_forward_returns.py:46`, `scripts/generate_labelling_worksheet.py:29` |

The repository therefore has **no survivorship-bias-free, corporate-action-correct,
delisted-inclusive** historical source today. IBKR/IG are live-contract feeds; Finnhub is
news; yfinance is a free convenience source with the well-known survivorship limitation.

---

## 2. Requirement → why current sources fall short (verified)

From `docs/historical_data_feasibility.md` (verified): `backtest.db` holds **29 survivor
symbols, RAW/unadjusted, ~2yr, no corporate actions, no delisted names, no listing/delisting
dates, no permanent IDs**. The six target capabilities (task §2) require a provider that
supplies: point-in-time universe membership, delisted/inactive securities, split- &
dividend-adjusted series + the raw series, listing/delisting dates, permanent identifiers,
and historical liquidity — for **US and UK** equities and ETFs.

---

## 3. Provider comparison matrix

Columns abbreviated; all non-repo capability cells are **[I] (re-verify)** or **[U]** per the
labelling rule. "UK" = LSE-listed equities/ETFs.

| Capability | IBKR | IG | Polygon | Tiingo | EODHD | Norgate† | Sharadar/NDL† |
|---|---|---|---|---|---|---|---|
| US equity coverage | [V] live contracts | [V] limited (entitlement) | [I] broad | [I] broad | [I] broad | [I] broad (US) | [I] broad (US) |
| UK equity coverage | [V] live (LSE via SMART) | [V] EPIC-based, demo-blocked | [I] limited/none on base tiers | [I] partial (intl add-on) | [I] **strong global incl. LSE** | [I] **US-focused (no UK)** | [I] **US-only** |
| ETF coverage | [V] yes (SGLN/SSLN traded) | [V] yes | [I] yes | [I] yes | [I] yes | [I] yes (US) | [I] yes (US) |
| Inactive/delisted coverage | [U]/[I] generally **no** | [U] **no** | [I] yes (paid tiers) | [I] limited | [I] yes | [I] **yes (core strength)** | [I] **yes (core strength)** |
| Listing & delisting dates | [I] no | [U] no | [I] yes | [I] partial | [I] yes | [I] **yes** | [I] **yes** |
| Historical ticker/name changes | [I] no | [U] no | [I] partial | [U] | [I] yes | [I] **yes** | [I] yes |
| Stable permanent IDs | [V] `conId` (not persisted) | [V] EPIC (not a security id) | [I] tickers + ids | [U] | [I] yes | [I] **yes (internal ids)** | [I] yes (permaticker) |
| Raw daily OHLCV | [V] yes (RAW) | [V] yes (mid) | [I] yes | [I] yes | [I] yes | [I] yes | [I] yes |
| Split-adjusted OHLCV | [I] single series only | [U] | [I] yes | [I] yes | [I] yes | [I] **yes (both)** | [I] **yes (both)** |
| Split events | [I] no | [U] | [I] yes | [I] yes | [I] yes | [I] yes | [I] yes |
| Cash-dividend events | [I] no | [U] | [I] yes | [I] yes | [I] yes | [I] yes | [I] yes |
| Historical volume/liquidity | [V] equities yes; CFD `-1` | [V] mid-based | [I] yes | [I] yes | [I] yes | [I] yes | [I] yes |
| Sector/industry history | [I] no | [U] no | [I] reference (current) | [I] limited | [I] yes (fundamentals) | [I] some | [I] **yes (Sharadar tickers/metadata)** |
| Point-in-time universe support | [I] no | [U] no | [I] reconstructable | [U] | [I] reconstructable | [I] **native (core design)** | [I] **strong** |
| API access model | [V] socket (ib_insync) | [V] REST (trading-ig) | [I] REST + flat-file/S3 | [I] REST | [I] REST + bulk | [I] desktop/data files + API | [I] REST/bulk via NDL |
| Rate limits | [V] pacing `ib.sleep(12)` | [V] 2s + weekly cap | [I] tier-dependent | [I] tier-dependent | [I] tier-dependent | [I] local files (no live limit) | [I] tier-dependent |
| Bulk download support | [I] no (per-contract) | [I] no (weekly cap) | [I] **yes (flat files)** | [I] yes | [I] **yes** | [I] **yes (bulk files)** | [I] **yes (bulk)** |
| Expected pricing tier | [V] incl. w/ brokerage | [V] incl. w/ brokerage | [I] mid SaaS | [I] **low/free-ish** | [I] **low–mid** | [I] mid annual | [I] low–mid (per dataset) |
| Licensing restrictions | [I] personal/non-redistribution | [I] personal | [I] per-seat/redistribution limits | [I] non-redistribution | [I] varies | [I] per-seat, no redistribution | [I] per-seat (NDL terms) |
| Redistribution restrictions | [I] yes (restricted) | [I] yes | [I] yes | [I] yes | [I] yes | [I] yes | [I] yes |
| Storage/export rights | [I] internal use | [I] internal | [I] export allowed (internal) | [I] export | [I] export | [I] **local files (export-native)** | [I] export |
| Known survivorship limitation | [V] **survivor-only** | [V] **survivor-only** | [I] mitigated on paid | [I] **present unless add-on** | [I] mitigated | [I] **none (built for this)** | [I] **none (built for this)** |
| Known adjustment limitation | [V] **RAW only, no CA** | [V] mid, no CA | [I] check adj methodology | [I] check | [I] check | [I] documented adj | [I] documented adj |

† **Norgate Data** and **Sharadar (SEP, via Nasdaq Data Link)** are added as "other credible
providers" because they are the canonical survivorship-bias-free equity-history sources for
exactly this use case. Both are **[I] US-centric / US-only** for equities — a key limitation
for the UK requirement.

---

## 4. The UK problem (decisive constraint)

The dynamic universe spans **US and UK** instruments (the live universe already includes LSE
names BARC, ANTO, SGLN, SSLN, SU — [V] `instruments.json`/`instruments_ig.json`). This
**eliminates the US-only survivorship specialists (Norgate, Sharadar)** as a *sole* primary
source: they do not cover LSE equities. The providers that plausibly cover **both US and UK
with corporate actions and delisted history** are, [I]: **EODHD** (broad global incl. LSE,
bulk, low–mid cost) and **Polygon** (strong US; UK weaker on base tiers). Tiingo is
[I] strong US, weaker/intl-add-on for UK.

---

## 5. Recommendation (subject to operator approval — no purchase made)

| Role | Recommendation | Rationale | Confidence |
|---|---|---|---|
| **Primary historical research provider** | **EODHD** (primary candidate), with **Polygon** as the close alternative if US-only-first is acceptable | [I] only realistic single source giving **US + UK** equities/ETFs, split/dividend events, delisted coverage, listing/delisting dates, bulk download, low–mid cost. Best fit for survivorship-free point-in-time over the actual US+UK universe. | **Medium** — all capability claims are [I]; must be verified on a trial under approval |
| **Secondary / reconciliation source** | **IBKR** (`bot/data.py`) | [V] already integrated; ideal for recent-bar cross-check at the trade boundary and live/next-session price. Not a universe-history source. | **High** (verified integration) |
| **IBKR role** | Execution (IBKR gateway) + recent-bar reconciliation | [V] live path exists | High |
| **IG role** | Execution/reconciliation **only**; **not** a research-data source | [V] demo cash-equity entitlement block (`TECH_DEBT.md`); task §7 default | High |
| **Sector/industry source** | From the chosen primary provider's reference/fundamentals (EODHD fundamentals or Polygon reference), GICS-style | [I] sector history absent today; needed for the §2 sector cap | Medium |

**Why not the survivorship specialists as primary:** Norgate/Sharadar are the strongest
survivorship-free choices but are **US-only** for equities — incompatible with the UK leg.
They remain viable as a **US-only secondary/validation** source if the operator later splits
US and UK research, but that is an open decision (operator-decisions doc), not a
recommendation here.

**Why not IBKR/IG as primary research:** [V] both are survivor-only, RAW/uncorrected, and IG
equity history is entitlement-blocked. Adequate for execution/reconciliation, not for a
survivorship-free historical universe.

### Compatibility with the canonical schema

The recommendation maps cleanly onto the proposed `raw_bars` / `corporate_actions` /
`dataset_manifest` schemas (`docs/historical_data_feasibility.md` §5): the primary provider
populates `raw_bars` (raw + adjusted), `corporate_actions`, listing/delisting and permanent
ids on `canonical_instruments`; IBKR populates the reconciliation comparison. `dataset_manifest`
hashes pin each point-in-time snapshot for reproducibility.

---

## 6. Hard constraints honored

* No sign-up, authentication, purchase, or download performed.
* No exact pricing asserted; all provider capability claims are **[I] re-verify** or **[U]**.
* Recommendation is **not** tuned to the current 14 or 29 symbols — it is driven by the
  US+UK + survivorship-free + corporate-action + delisted requirements.
* No strategy results computed.

**Operator approval is required before selecting, trialing, or purchasing any provider.**
The single most consequential open decision (see operator-decisions doc) is whether the UK
leg is in scope for v1 history — if UK is deferred, the US-only survivorship specialists
(Norgate/Sharadar) become viable primaries and the recommendation changes.
