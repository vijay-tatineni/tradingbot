# EODHD Field-Coverage Matrix (Workstream 1)

> Outcome-blind. **No download performed** (no approved EODHD access — see `eodhd_trial_result.md`
> §0). Provider-**level** cells are verified against **public docs on 2026-06-09** (incl. a bounded
> Terms & Conditions fetch). Per-**instrument** cells require API access and are therefore
> **NOT VERIFIED WITHOUT ACCESS** — they were **not** reconstructed from memory or training data.
> Supersedes / is consistent with the C0 `provider_field_coverage_matrix.md`.

## Label legend

* **VERIFIED** — confirmed from a primary source I inspected (public provider doc, this date).
* **PARTIALLY VERIFIED** — capability advertised at provider level, but the specific value/quality
  cannot be confirmed without account access.
* **NOT AVAILABLE** — provider documents it as absent / out of scope.
* **UNKNOWN** — not determinable without authenticating, a paid trial, or a sales quote.
* **NOT VERIFIED WITHOUT ACCESS** — a per-instrument field that necessarily requires an API call
  this trial is not authorized to make.

## A. Provider-level capability (EODHD)

| Field | EODHD finding | Label |
|---|---|---|
| US daily historical | NYSE/NASDAQ within 70+ exchanges | VERIFIED |
| UK / LSE daily historical | `LSE` / MIC `XLON`, ~6,388 active tickers, GBP | VERIFIED |
| Raw daily OHLCV | offered | VERIFIED |
| Adjusted daily OHLCV | adjusted series offered | VERIFIED |
| Adjustment methodology | not documented in fetched pages | UNKNOWN |
| Split events | Splits API / Bulk | VERIFIED (existence); completeness UNKNOWN |
| Cash-dividend events | Dividends API / Bulk (LSE incl. payment date per C0) | VERIFIED (existence); completeness UNKNOWN |
| Active instruments | yes | VERIFIED |
| Delisted / inactive instruments | dedicated delisted API | VERIFIED (existence); depth NOT VERIFIED WITHOUT ACCESS |
| Listing date | via reference / fundamentals | PARTIALLY VERIFIED |
| Delisting date | via delisted dataset | PARTIALLY VERIFIED |
| Ticker / name-change metadata | advertised (ID-mapping + delisted) | PARTIALLY VERIFIED |
| Stable identifiers (ISIN/FIGI/CUSIP/LEI/CIK) | ID-mapping API | VERIFIED (existence); point-in-time UNKNOWN |
| Historical volume | EOD volume | VERIFIED |
| Sector / industry metadata | fundamentals feed | VERIFIED |
| Sector / industry **history** | not confirmed | UNKNOWN |
| Reproducible export / local storage | bulk export offered; storage permitted in T&C | PARTIALLY VERIFIED |
| Point-in-time universe research | reconstructable from delisted + dates | PARTIALLY VERIFIED |
| Bulk download | Bulk API (EOD/splits/divs) advertised | VERIFIED (existence); **explicit bulk-download right not in fetched T&C → UNKNOWN** |
| Rate limit | 100,000 requests/day per key | VERIFIED (T&C) |
| Local storage permitted | during active subscription; delete within 1 month of termination | VERIFIED (T&C) |
| Redistribution restrictions | prohibited (non-pro); resell/repackage needs written approval | VERIFIED (T&C) |
| Derived-data restrictions | clause absent from fetched T&C | UNKNOWN |
| API caching policy | clause absent from fetched T&C | UNKNOWN |
| Expected subscription tier | EOD All-World £19.99/mo (C0 doc-verified) | VERIFIED (pricing page) |

## B. Per-instrument fields (REQUIRED by task; could NOT be verified — no access)

For **every** sample instrument (`AAPL.US`, `LEH.US`, `BARC.LSE`, `TT.LSE`, `SGLN.LSE`, `FB→META`,
`YNDX→NBIS`), the following fields are **NOT VERIFIED WITHOUT ACCESS**. This table is the
work-sheet for a future, separately approved trial — **not** an assertion of values.

```text
provider permanent identifier        NOT VERIFIED WITHOUT ACCESS
current and historical symbol        NOT VERIFIED WITHOUT ACCESS
exchange / MIC                       NOT VERIFIED WITHOUT ACCESS
currency                             NOT VERIFIED WITHOUT ACCESS
price unit (GBP vs GBX)              NOT VERIFIED WITHOUT ACCESS   <-- LSE pence trap; must check on trial
timezone (if supplied)               NOT VERIFIED WITHOUT ACCESS
listing date                         NOT VERIFIED WITHOUT ACCESS
delisting date                       NOT VERIFIED WITHOUT ACCESS
raw daily OHLCV                      NOT VERIFIED WITHOUT ACCESS
adjusted daily OHLCV                 NOT VERIFIED WITHOUT ACCESS
adjustment factor / methodology      UNKNOWN (not in public docs fetched)
split events                         NOT VERIFIED WITHOUT ACCESS
cash-dividend events                 NOT VERIFIED WITHOUT ACCESS
ticker / name-change continuity      NOT VERIFIED WITHOUT ACCESS
sector                               NOT VERIFIED WITHOUT ACCESS
industry                             NOT VERIFIED WITHOUT ACCESS
earliest available date              NOT VERIFIED WITHOUT ACCESS
latest available date                NOT VERIFIED WITHOUT ACCESS
bar count                            NOT VERIFIED WITHOUT ACCESS
missing sessions                     NOT VERIFIED WITHOUT ACCESS
duplicate sessions                   NOT VERIFIED WITHOUT ACCESS
```

> The per-instrument trial **could not run**: no approved EODHD access exists, and creating access
> is outside this authorization. Field classification per the task's four-state scheme
> (VERIFIED / PARTIALLY VERIFIED / NOT AVAILABLE / UNKNOWN) is applied at provider level in §A;
> at instrument level every required field resolves to NOT VERIFIED WITHOUT ACCESS or UNKNOWN.
