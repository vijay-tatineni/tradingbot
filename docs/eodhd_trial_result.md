# EODHD Trial — Result (Workstream 1)

> **Branch:** `planning/eodhd-trial-ig-equivalence` (base `planning/dynamic-universe-provider-c0` @ `263d5ee`).
> **Scope:** field-coverage, data-quality, licensing and storage **feasibility trial only.**
> **Outcome-blind.** No returns, profit factor, Sharpe, breakout signals, strategy rankings, or
> instrument winners/losers were computed. The PROVISIONAL breakout result is untouched
> (not rerun, tuned, reinterpreted, or extended). The deployed hard-disabled invariant is unchanged.
> **No account created, no subscription purchased, no paid trial started, no payment details entered.**
> Inspection / public-doc verification date: **2026-06-09 (UTC)**.

---

## 0. Access-boundary outcome (checked FIRST)

Re-verified the credential state directly (env-var **key names only — no values read, copied,
logged, or committed**):

| Provider | Approved access key present? | Evidence (key name only) |
|---|---|---|
| **EODHD** | **NO** | no `EOD*` / `EODHD*` key in `/root/trading/.env` or `/root/trading-ig/.env` |
| **Polygon** | **NO** | no `POLYGON*` key in either `.env` |
| Tiingo | **NO** | no `TIINGO*` key in either `.env` |
| Finnhub | yes — **news-only, not price bars** | `FINNHUB_API_KEY` present (sentiment gate) |
| IBKR | yes (live) | `IB_USERNAME` / `IB_PASSWORD` — bot live on IBKR now |
| IG | yes (execution) | `IG_*` keys — IG bot live |

The only EODHD-related artifact in the repo is a **WebFetch permission for the public docs domain**
(`.claude/settings.local.json`: `WebFetch(domain:eodhd.com)`) — reading public documentation is not
sign-up, authentication, purchase, or download.

**Conclusion: no approved EODHD access exists.** Per the authorization boundary the
**per-instrument data-download portion of the trial was NOT performed** (it would require account
creation / a paid trial, which is not authorized here). This matches the C0 finding
(`provider_trial_result.md` §0) and is unchanged as of 2026-06-09.

What was done instead, within the boundary:

* Provider-**level** capability and **licensing** facts were verified against **public
  documentation** — including a bounded fetch of the public **Terms & Conditions** page (see
  `eodhd_licensing_and_storage_review.md`), which the C0 trial had flagged as outstanding.
* The exact operator signup/trial steps remain in `provider_operator_signup_checklist.md`
  (C0 deliverable, still authoritative).
* Per-instrument fields (real OHLCV, splits, listing/delisting dates, bar counts, GBX/GBP for the
  sample tickers) are labelled **NOT VERIFIED WITHOUT ACCESS** in
  `eodhd_field_coverage_matrix.md` — they were **not** reconstructed from memory or training data.

No sample data was downloaded → no data file or research directory of provider data was created.
The trial manifest (`eodhd_trial_manifest.md`) records **NO DATA DOWNLOADED**.

---

## 1. Representative sample (selected for coverage verification, not performance)

Selection is **outcome-blind** — each entry exercises a required data case, never returns. These
are the tickers an operator should pull **once access exists**; this trial could not pull them.

| # | Case | Example (for a future trial under access) | Why this case |
|---|---|---|---|
| 1 | Active US equity | `AAPL.US` | liquid active baseline; also covers case 6 (split) |
| 2 | Inactive / delisted US equity | `LEH.US` (Lehman Bros, delisted 2008) | classic delisting; tests delisted coverage + delisting date |
| 3 | Active UK / LSE equity | `BARC.LSE` (Barclays) | active LSE name; tests UK leg + GBX/GBP |
| 4 | Inactive / delisted UK / LSE equity | `TT.LSE` (Thomas Cook, delisted 2019) | UK delisting — the decisive coverage gap |
| 5 | Unleveraged ETF | `SGLN.LSE` (iShares Physical Gold) | unleveraged ETF, LSE-listed |
| 6 | Documented split | `AAPL.US` (4:1 2020-08-31; 7:1 2014-06) | split-factor + raw-vs-adjusted consistency |
| 7 | Documented ticker/name change | `FB→META` (2022) and `YNDX→NBIS` (Yandex→Nebius, 2024) | identifier-continuity / ticker-change metadata |

`AAPL.US` covers cases 1+6; `BARC.LSE`/`SGLN.LSE` cover 3+5. The `YNDX→NBIS` example is added under
case 7 because that exact ticker change is **live in our own IG universe** (`NBIS` mapped to EPIC
`UD.D.YNDX.CASH.IP`) — see `ig_mapping_verification_register.md`. **None were downloaded.**

---

## 2. Provider-level findings (verified from public docs, 2026-06-09)

Carried forward and re-confirmed from C0 (`provider_trial_result.md` §2a,
`provider_field_coverage_matrix.md`), with **one new VERIFIED block** from the Terms fetch:

| Capability | Finding | Status |
|---|---|---|
| US coverage | NYSE/NASDAQ within 70+ exchanges | **VERIFIED (docs)** |
| UK / LSE coverage | exchange code `LSE` (MIC `XLON`, GBP), ~6,388 active tickers | **VERIFIED (docs, C0)** |
| Delisted / inactive | dedicated "Delisted Stock Companies Data" API | **VERIFIED (docs)** — per-ticker depth **NOT VERIFIED without access** |
| Splits & dividends | "Corporate Actions: Splits & Dividends" API + Bulk API | **VERIFIED (docs)** — completeness **NOT VERIFIED without access** |
| Raw + adjusted OHLCV | EOD incl. splits/dividends/adjusted | **VERIFIED (docs)** — adjustment methodology **UNKNOWN** until tested |
| Stable identifiers | ID-mapping API: ISIN/FIGI/CUSIP/LEI/CIK | **VERIFIED (docs)** — point-in-time behaviour **UNKNOWN** |
| Sector / industry | fundamentals feed | **VERIFIED (docs)** — history depth **UNKNOWN** |
| Local storage / retention | **storage permitted during active subscription; all copies must be deleted within 1 month of termination/expiry** | **VERIFIED (T&C, NEW 2026-06-09)** |
| Redistribution | prohibited for non-professional; resell/repackage needs prior written approval | **VERIFIED (T&C, NEW)** |
| Rate limit | 100,000 API requests/day per key | **VERIFIED (T&C, NEW)** |
| API caching / derived-data / bulk-download rights | **clauses absent from the fetched T&C** | **UNKNOWN — requires provider clarification** |

See `eodhd_field_coverage_matrix.md` and `eodhd_licensing_and_storage_review.md` for the full
field-by-field tables.

---

## 3. Required verdict

```text
TRIAL ACCESS REQUIRED BEFORE DECISION
```

Rationale (unchanged from C0, refined by the new T&C evidence): EODHD's **provider-level**
capability set is the only single candidate that plausibly satisfies the US+UK survivorship-aware
requirement, and the licensing picture is now clearer (storage + 1-month deletion + 100k/day are
VERIFIED). **But** every **per-instrument** data-quality fact (real adjustment methodology, actual
delisted-history depth, LSE corporate-action completeness, point-in-time identifier stability,
GBX/GBP normalization, bar/gap counts) still requires account access, which is unauthorized here.
A verdict of `ADOPT…` would require asserting those unverified facts — which the rules forbid.

The path forward is a **separately approved** small sample trial (the 7-instrument battery above)
executing `eodhd_field_coverage_matrix.md` §B and `eodhd_data_quality_report.md`.

---

## 4. Specific questions answered (this trial)

| Question | Answer |
|---|---|
| Can EODHD support **active and delisted US+UK** instruments? | **Plausibly yes** — US + LSE coverage and a dedicated delisted endpoint are doc-verified. UK **delisted depth** (e.g. `TT.LSE`) is the decisive item and is **NOT VERIFIED without access**. |
| Are **corporate actions** sufficiently complete? | Splits & dividends APIs are doc-verified to **exist**; **completeness/quality NOT VERIFIED without access** (raw-vs-adjusted and split-factor checks are deferred). |
| Are **stable identifiers** usable? | ISIN/FIGI/CUSIP/LEI/CIK mapping is doc-verified; **point-in-time stability across ticker changes NOT VERIFIED without access**. |
| Is **GBP/GBX normalization** reliable? | **NOT VERIFIED without access** — a per-instrument data check (LSE quoted in pence is a known trap). Deferred to the data-quality battery. |
| Is **sector/industry** coverage adequate? | Fundamentals feed carries sector/industry (doc-verified); **history depth UNKNOWN**. Sufficiency for the "max 2 positions/sector" rule needs the trial. |
| Can data be **stored reproducibly under the applicable terms**? | **Yes, conditionally** — T&C permit local storage **during the subscription** with **mandatory deletion within 1 month of termination**. Reproducibility (manifest + hashes) is supported; the deletion obligation must be tracked. Caching/derived-data rights remain **UNKNOWN**. |
| Is **another source** required for any critical field? | **For US+UK, a single primary (EODHD) is plausible.** If UK is descoped, US-only survivorship specialists (Norgate/Sharadar) re-open as primaries (operator decision). **IBKR remains the recommended recent-bar reconciliation cross-check regardless.** No second provider is confirmed-required until the trial reveals a gap. |

**Full ingestion is NOT authorized and was NOT performed.**
