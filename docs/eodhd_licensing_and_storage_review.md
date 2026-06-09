# EODHD Licensing & Storage Review (Workstream 1)

> Reviews the **applicable EODHD terms, not only the pricing page**, per task §"Licensing and
> storage". Source: bounded public-doc fetch of the EODHD Terms & Conditions on **2026-06-09 (UTC)**
> (`https://eodhd.com/financial-apis/terms-conditions`) plus the C0 pricing-page findings. Reading
> public terms is **not** sign-up, authentication, purchase, or download. No credential exists; no
> account was created. Unclear/absent terms are labelled **UNKNOWN** with the clarification needed.

## Label legend

* **VERIFIED** — quoted/paraphrased from the fetched T&C or pricing page (this date).
* **PARTIALLY VERIFIED** — addressed but tier-/category-dependent; confirm for the research use-case.
* **UNKNOWN** — no clause found in the fetched terms; requires provider clarification before reliance.

## Findings

| Term | Finding | Label |
|---|---|---|
| **Local storage rights** | "EOD Historical Data Information may be stored on the subscriber's premises during the active subscription period." | **VERIFIED** |
| **Data retention / deletion** | "Upon termination or expiration of the subscription, the subscriber is required to delete all copies of the data in their possession within **one (1) month**." | **VERIFIED** — a concrete deletion obligation; must be tracked operationally |
| **Internal research use** | Non-Professional users may analyze data "for private, non-commercial purposes." Internal **business/commercial** research falls under Professional use. | **PARTIALLY VERIFIED** — which tier our use-case legally requires must be confirmed |
| **Commercial / internal business use** | Professional users (businesses/organizations) must obtain "prior written approval from EOD Historical Data representatives" for reselling or repackaging. | **PARTIALLY VERIFIED** — internal-only business research vs. resale needs explicit confirmation |
| **Redistribution restrictions** | Non-Professional users are prohibited from "selling, reselling, retransmitting, redistributing, displaying, or granting access to the Information or Services." | **VERIFIED** — no redistribution |
| **Derived-data restrictions** | No clause addressing derived-data / aggregation found in the fetched terms. | **UNKNOWN** — clarify whether derived series (e.g. point-in-time universe snapshots) may be stored/shared internally |
| **API caching restrictions** | No explicit API-caching policy found. | **UNKNOWN** — clarify caching/local-DB persistence limits |
| **Bulk-download rights** | Bulk API is advertised on the product pages, but the fetched T&C contain **no explicit bulk-download-rights clause**. | **UNKNOWN** — confirm bulk EOD/splits/dividends download is contractually permitted on the chosen tier |
| **Rate limits** | "One API key allows querying **100,000 API requests per day**." (Free tier: 20/day, C0.) | **VERIFIED** |
| **Required subscription tier** | EOD Historical All-World **£19.99/mo** (C0 pricing page) is the minimum tier carrying EOD + splits + dividends + adjusted + 30+yr. Fundamentals (sector/industry history) is a separate add-on. | **VERIFIED (pricing)** |
| **US coverage** | NYSE/NASDAQ within 70+ exchanges. | **VERIFIED (docs)** |
| **UK coverage** | LSE / MIC `XLON`, GBP, ~6,388 active. | **VERIFIED (docs, C0)** |
| **Delisted-data coverage** | dedicated delisted endpoint; per-ticker depth not verifiable without access. | **PARTIALLY VERIFIED** |

## Items requiring provider clarification before any purchase

```text
[ ] Q1  Is internal (non-redistributed) business/commercial research permitted on the
        £19.99 "Personal use" EOD tier, or does it require the Professional/Enterprise plan?
[ ] Q2  Are derived datasets (point-in-time universe snapshots, adjusted-series caches)
        permitted to be stored and used internally? (No clause found.)
[ ] Q3  What are the API caching / local-database persistence limits? (No clause found.)
[ ] Q4  Is bulk download of EOD/splits/dividends contractually permitted on the chosen tier? (No clause found.)
[ ] Q5  Confirm the 1-month post-termination deletion obligation and how it interacts with
        a reproducible research archive (hashes/manifests may need to be purged of raw data).
```

## Storage discipline (binds any future approved trial)

Per task §"Trial storage" — if/when sample data is downloaded under separate approval:

```text
- store OUTSIDE git in a clearly named temporary research directory (e.g. /root/research_data_tmp/);
- preserve raw provider responses unchanged;
- create a manifest: provider, endpoints, timestamps, files, row counts, date ranges, SHA-256 hashes;
- DO NOT commit data, credentials, signed URLs, or account details;
- record the provider's 1-month post-termination deletion obligation in the manifest and honour it.
```

## Conclusion (licensing/storage)

Storage and reproducibility are **contractually supported** during an active subscription, with a
**mandatory 1-month deletion** on termination. The redistribution and rate-limit positions are
clear. **Four contractual gaps (Q1–Q4) are UNKNOWN** and must be clarified with EODHD before any
purchase. This does **not** by itself block a trial decision, but **does** block any assertion that
the data may be used for outside-client or investment-management purposes — which is **NOT verified
and must not be assumed**.
