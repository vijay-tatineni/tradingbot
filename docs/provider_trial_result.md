# Provider Feasibility Trial — Result (Workstream A)

> **Branch:** `planning/dynamic-universe-provider-c0` (base `design/dynamic-universe-hybrid-v1` @ `e8668422`).
> **Outcome-blind. No PF / Sharpe / returns / breakout signals / rankings computed.**
> **No account created, no subscription purchased, no paid trial started, no payment details
> provided, no provider terms bypassed, no broad/full universe downloaded.**
> Date of inspection / public-doc verification: **2026-06-08 (UTC)**.
> This trial does **not** authorize ingestion. Approval is required before any provider purchase or download.

---

## 0. Authorization-boundary outcome (checked first)

Inspected `/root/trading/.env` and `/root/trading-ig/.env` (**key names only — no values read,
copied, logged, or committed**). Approved historical-research provider access status:

| Provider | Credential present? | Evidence (key name only) |
|---|---|---|
| **EODHD** | **NO** | no `EOD*` / `EODHD*` key in either `.env` |
| **Polygon** | **NO** | no `POLYGON*` key in either `.env` |
| Finnhub | yes — **but news-only, not price bars** | `FINNHUB_API_KEY` present; used in `bot/llm/news_collector.py` (verified in design doc) |
| IBKR | yes (live) | `IB_USERNAME` / `IB_PASSWORD`; bot is **live on IBKR right now** |
| IG | yes (execution) | `IG_*` keys; IG bot live |

**Conclusion:** **No approved access exists for either recommended research candidate (EODHD or
Polygon).** Per the authorization boundary, the **per-instrument data-download portion of the
trial was NOT performed** (would require account creation / paid trial). Instead:

* Provider-**level** capability, coverage, pricing-tier and licensing facts were verified against
  **public documentation** (reading public docs is not sign-up, auth, purchase, or download).
* The exact operator signup/trial checklist is produced in `provider_operator_signup_checklist.md`.
* Per-instrument fields (real OHLCV, splits, listing dates, bar counts for the sample tickers)
  are labelled **NOT VERIFIED WITHOUT ACCESS** in `provider_field_coverage_matrix.md` — they were
  **not** reconstructed from memory.

No sample data was downloaded, so no data file, manifest, or temporary research directory of
provider data was created. (The restricted evidence directory `/root/consolidation_evidence/`
holds only Workstream-B freeze evidence, not provider data.)

---

## 1. Representative sample (selected for coverage verification, not performance)

Selection is **outcome-blind** — chosen to exercise each required data case, never for returns.
These are the tickers an operator should pull **once access exists**; this trial could not pull them.

| Case | Example (for trial under access) | Why this case |
|---|---|---|
| Active US equity | `AAPL` (US) | liquid active baseline; also already in both live universes |
| Inactive / delisted US equity | `LEH` (Lehman Bros, delisted 2008) | classic delisting; tests delisted-coverage + delisting date |
| Active UK equity | `BARC.LSE` (Barclays) | active LSE name; in both live universes — tests UK leg |
| Inactive / delisted UK equity | `TT.LSE` (Thomas Cook, delisted 2019) | UK delisting; tests UK delisted coverage (the decisive gap) |
| Unleveraged ETF | `SGLN.LSE` (iShares Physical Gold) | unleveraged ETF, LSE-listed; in both live universes |
| Documented stock split | `AAPL` (4:1 split 2020-08-31; 7:1 2014) | split-factor + raw-vs-adjusted consistency |
| Documented ticker/name change | `FB → META` (2022 ticker change) | identifier-continuity / ticker-change metadata |

`AAPL` covers active-US + split; `BARC.LSE`/`SGLN.LSE` cover UK-active + ETF. Seven cases, five
distinct securities-of-interest plus two delisted names. **None were downloaded.**

---

## 2. Provider-level findings (verified from public docs, 2026-06-08)

### 2a. EODHD (recommended primary candidate)

| Capability | Finding | Status | Source |
|---|---|---|---|
| US coverage | NYSE/NASDAQ within 70+ exchanges | **VERIFIED (docs)** | eodhd.com/financial-apis, list-of-stock-markets |
| **UK / LSE coverage** | Exchange code `LSE` (MIC `XLON`, GBP), **~6,388 active tickers**; LSE dividends incl. payment-date | **VERIFIED (docs)** | eodhd.com exchange/LSE; blog: LSE dividends |
| Delisted / inactive | dedicated **"Delisted Stock Companies Data"** API | **VERIFIED (docs)** — *per-ticker depth NOT verified without access* | eodhd.com/financial-apis |
| Splits & dividends | **"Corporate Actions: Splits and Dividends API"** + Bulk API for EOD/Splits/Dividends | **VERIFIED (docs)** | eodhd.com/financial-apis |
| Raw + adjusted OHLCV | EOD incl. splits, dividends, adjusted | **VERIFIED (docs)** — *adjustment methodology detail UNKNOWN until tested* | eodhd.com/pricing |
| Bulk download / export | Bulk API (EOD/Splits/Dividends), Bulk Fundamentals | **VERIFIED (docs)** | eodhd.com/financial-apis |
| Sector / industry | Fundamentals feed incl. sector/industry classification | **VERIFIED (docs)** — *history depth UNKNOWN* | eodhd.com/financial-apis |
| Stable identifiers | ID-mapping API: CUSIP / ISIN / FIGI / LEI / CIK | **VERIFIED (docs)** — *point-in-time mapping behaviour UNKNOWN* | eodhd.com/financial-apis |
| Pricing tier | Free 20 calls/day; **EOD All-World £19.99/mo (100k calls/day, 30+yr)**; Fundamentals £59.99; All-in-One £99.99 | **VERIFIED (docs)** | eodhd.com/pricing |
| Licensing | listed tiers are **"Personal use"**; **commercial → "Startups & Enterprise" plan** | **PARTIALLY VERIFIED** — redistribution/storage/retention terms **not on pricing page**, require full T&C review | eodhd.com/pricing |

### 2b. Polygon (US-first alternative)

| Capability | Finding | Status | Source |
|---|---|---|---|
| US coverage | full-coverage US equities, historical aggregates/OHLCV | **VERIFIED (docs/review)** | polygon.io reviews, readthedocs |
| **UK / LSE coverage** | **none found — US-only for equities** | **VERIFIED (docs): NOT AVAILABLE** | search corpus, no LSE endpoint |
| Delisted / inactive | active+delisted via reference endpoints, **but third-party reports of unreliable delisted coverage** | **PARTIALLY VERIFIED / concern** | Medium/InsiderFinance reviews |
| Splits & dividends | endpoints exist, **but reported missing dividends/splits (e.g. SPY)** | **PARTIALLY VERIFIED / concern** | reviews |
| Flat files / bulk | flat-file/S3 bulk historically offered (US) | **PARTIALLY VERIFIED** | known offering; re-verify |

**IBKR** remains the proposed recent-bar reconciliation source (verified integrated:
`bot/data.py`); **IG** remains execution/reconciliation only (verified entitlement-blocked for
equity history). Neither is a survivorship-free research source.

---

## 3. Required conclusion

```text
TRIAL ACCESS REQUIRED BEFORE DECISION
```

Rationale: EODHD's **provider-level** capability set (US + UK/LSE, delisted endpoint, splits &
dividends, bulk export, fundamentals/sector, ISIN/FIGI identifiers, low-mid cost) is the **only
single candidate that plausibly satisfies the US+UK survivorship-aware requirement** and is
verified-from-docs to advertise every required capability. **But** none of the **per-instrument
data-quality facts** (real adjustment methodology, actual delisted-history depth, LSE corporate-
action completeness, point-in-time identifier stability, bar/gap counts) could be verified without
account access, which is unauthorized here. A decision of `SUITABLE…` would require asserting those
unverified facts — which the rules forbid.

## 3a. IBKR reconciliation procedure (documented; NOT executed)

IBKR is the proposed recent-bar reconciliation source. **Not executed this trial**: there is no
provider data to compare against (no approved EODHD/Polygon access), and a second live IBKR
session was **not** opened (the bot is live on IBKR now — a second session risks contention and is
prohibited). Procedure for a future, separately approved run:

```text
1. Select active instruments present in BOTH IBKR and the provider (e.g. AAPL, MSFT; LSE: BARC/SGLN).
2. Provider side: pull raw daily OHLCV for a small recent window (e.g. last ~20 sessions).
3. IBKR side: obtain the same window from EXISTING cached/offline data, OR a read-only
   reqHistoricalData call under separate approval — NOT a second live trading session.
4. Align by date; compare close and volume per session.
5. EXPECTED differences: provider split/dividend-adjusted vs IBKR raw/unadjusted; GBX-vs-GBP for
   LSE names; timezone/session-boundary. Record these as expected, not discrepancies.
6. Flag only MATERIAL, UNEXPLAINED diffs beyond a stated tolerance (e.g. >0.5% close with no
   corporate action) for review.
7. Do NOT submit orders; do NOT alter broker configuration.
```

Reconciliation feasibility verdict: **feasible but not executed** — depends on approved provider
access plus either cached IBKR bars or an approved read-only IBKR pull.

## 4. Specific questions answered

| Question | Answer (this trial) |
|---|---|
| Survivorship-aware **US+UK** dynamic universe? | **EODHD: plausibly yes** (US + LSE + delisted endpoint, doc-verified). **Polygon: no** (US-only). Per-ticker UK delisted depth **needs trial**. |
| Sufficiently reliable corporate actions? | EODHD advertises splits+dividends incl. LSE; **reliability NOT verifiable without access**. Polygon has **reported reliability gaps**. |
| Inactive/delisted coverage? | EODHD has a dedicated delisted endpoint (doc-verified); depth/quality **needs trial**. Polygon delisted **reported weak**. |
| Stable identifiers available? | EODHD: ISIN/FIGI/CUSIP/LEI/CIK mapping (doc-verified); point-in-time behaviour **needs trial**. |
| Sector data sourced consistently? | EODHD fundamentals carry sector/industry (doc-verified); **history depth UNKNOWN**. |
| What remains unavailable? | All **per-instrument** verification; EODHD redistribution/storage/retention T&C; adjustment methodology; UK delisted depth; Polygon reliability. |
| Second provider required? | **For US+UK, a single primary (EODHD) is plausible**; if UK is descoped, US-only survivorship specialists (Norgate/Sharadar) re-open as primaries (operator decision). IBKR stays as reconciliation regardless. **Recommend a trial of EODHD before committing**; keep IBKR recon as the cross-check. |

**Full ingestion is NOT authorized and was NOT performed.**
