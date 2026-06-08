# Provider Field-Coverage Matrix (Workstream A)

> Outcome-blind. No download performed (no approved EODHD/Polygon access — see
> `provider_trial_result.md` §0). Provider-**level** cells are verified against **public docs on
> 2026-06-08**. Per-**instrument** cells require API access and are therefore
> **NOT VERIFIED WITHOUT ACCESS** — they were **not** reconstructed from memory or training data.

## Label legend

* **VERIFIED** — confirmed from a primary source I inspected (public provider doc, this date).
* **PARTIALLY VERIFIED** — capability advertised at provider level, but the specific value/quality
  cannot be confirmed without account access.
* **NOT AVAILABLE** — provider documents it as absent / out of scope.
* **UNKNOWN** — not determinable without authenticating, a paid trial, or a sales quote.
* **NOT VERIFIED WITHOUT ACCESS** — a per-instrument field that necessarily requires an API call
  this trial is not authorized to make.

## A. Provider-level capability (EODHD primary candidate)

| Field | EODHD | Label | Polygon | Label |
|---|---|---|---|---|
| US daily historical | NYSE/NASDAQ in 70+ exchanges | VERIFIED | full US equities | VERIFIED |
| UK / LSE daily historical | `LSE`/`XLON`, ~6,388 active | VERIFIED | none found | NOT AVAILABLE |
| Raw daily OHLCV | offered | VERIFIED | offered | VERIFIED |
| Split-adjusted OHLCV | offered (adjusted series) | VERIFIED | offered | PARTIALLY VERIFIED |
| Adjustment methodology | not documented in fetched pages | UNKNOWN | check needed | UNKNOWN |
| Split events | Splits API / bulk | VERIFIED | endpoint exists | PARTIALLY VERIFIED |
| Cash-dividend events | Dividends API / bulk | VERIFIED | endpoint exists (reported gaps) | PARTIALLY VERIFIED |
| Active instruments | yes | VERIFIED | yes | VERIFIED |
| Delisted/inactive instruments | dedicated delisted API | VERIFIED | reference endpoint (reported weak) | PARTIALLY VERIFIED |
| Listing date | via reference/fundamentals | PARTIALLY VERIFIED | reference | UNKNOWN |
| Delisting date | via delisted dataset | PARTIALLY VERIFIED | UNKNOWN | UNKNOWN |
| Ticker/name-change metadata | advertised | PARTIALLY VERIFIED | partial (ticker events) | UNKNOWN |
| Stable identifiers | ISIN/FIGI/CUSIP/LEI/CIK map | VERIFIED | tickers + ids | PARTIALLY VERIFIED |
| Historical volume | EOD volume | VERIFIED | yes | VERIFIED |
| Sector/industry metadata | fundamentals feed | VERIFIED | reference (current) | PARTIALLY VERIFIED |
| Sector/industry **history** | not confirmed | UNKNOWN | not confirmed | UNKNOWN |
| Reproducible export / local storage | bulk export offered | PARTIALLY VERIFIED (rights TBC) | flat files (US) | PARTIALLY VERIFIED |
| Point-in-time universe research | reconstructable from delisted+dates | PARTIALLY VERIFIED | reconstructable (US) | PARTIALLY VERIFIED |
| Bulk download | Bulk API (EOD/splits/divs) | VERIFIED | flat files | PARTIALLY VERIFIED |
| Rate limits | 20/day free; 100k/day paid tiers | VERIFIED | tier-dependent | UNKNOWN |
| Expected subscription tier | EOD All-World £19.99/mo | VERIFIED | mid SaaS | UNKNOWN |
| Local storage permitted | "Personal use"; commercial→Enterprise | PARTIALLY VERIFIED | UNKNOWN | UNKNOWN |
| Redistribution restrictions | not on pricing page | UNKNOWN | UNKNOWN | UNKNOWN |
| Derived-data restrictions | not on pricing page | UNKNOWN | UNKNOWN | UNKNOWN |
| API retention limits | not stated | UNKNOWN | UNKNOWN | UNKNOWN |

## B. Per-instrument fields (REQUIRED by task; could NOT be verified — no access)

For **every** sample instrument (`AAPL`, `LEH`, `BARC.LSE`, `TT.LSE`, `SGLN.LSE`, `FB→META`), the
following fields are **NOT VERIFIED WITHOUT ACCESS**. The trial is documented so an operator can
fill them in once a (separately approved) trial key exists:

```text
provider instrument identifier      NOT VERIFIED WITHOUT ACCESS
symbol                              NOT VERIFIED WITHOUT ACCESS
exchange                            NOT VERIFIED WITHOUT ACCESS
currency                            NOT VERIFIED WITHOUT ACCESS
timezone (if supplied)              NOT VERIFIED WITHOUT ACCESS
listing date                        NOT VERIFIED WITHOUT ACCESS
delisting date (if applicable)      NOT VERIFIED WITHOUT ACCESS
raw daily OHLCV                     NOT VERIFIED WITHOUT ACCESS
adjusted daily OHLCV                NOT VERIFIED WITHOUT ACCESS
split events                        NOT VERIFIED WITHOUT ACCESS
cash-dividend events                NOT VERIFIED WITHOUT ACCESS
ticker/name-change metadata         NOT VERIFIED WITHOUT ACCESS
sector and industry                 NOT VERIFIED WITHOUT ACCESS
earliest available date             NOT VERIFIED WITHOUT ACCESS
latest available date               NOT VERIFIED WITHOUT ACCESS
bar count                           NOT VERIFIED WITHOUT ACCESS
missing-session count               NOT VERIFIED WITHOUT ACCESS
duplicate dates                     NOT VERIFIED WITHOUT ACCESS
API response timestamp              NOT VERIFIED WITHOUT ACCESS
provider adjustment methodology     UNKNOWN (not in public docs fetched)
```

> The per-instrument trial **could not run**: no approved EODHD/Polygon access exists, and creating
> access is outside this authorization. This table is the work-sheet for a future, separately
> approved trial — not an assertion of values.
