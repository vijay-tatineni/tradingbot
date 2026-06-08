# Provider Operator Signup / Trial Checklist (Workstream A)

> **This is the explicit stop point of the trial.** No approved EODHD/Polygon access exists in the
> environment, so the agent **did not** create an account, start a paid trial, purchase a
> subscription, provide payment details, or bypass any provider terms. The steps below are for a
> **human operator** to perform under explicit approval. Completing them is **not** authorized by
> this task — they require a separate go-ahead.

## Decision gate BEFORE any signup

```text
[ ] D-UK: Is the UK (LSE) leg in scope for v1 history?
        - YES  -> EODHD is the primary candidate (only US+UK survivorship-free fit).
        - NO   -> US-only survivorship specialists (Norgate / Sharadar) re-open as primaries;
                  EODHD/Polygon decision changes. (See dynamic_universe_operator_decisions.md.)
```
This single decision is the most consequential open item and is pre-registration-affecting. Do not
sign up before it is recorded.

## EODHD (recommended primary candidate) — operator steps

```text
[ ] 1. Review full Terms & Conditions / licence (NOT just the pricing page). Confirm in writing:
        - internal research use permitted
        - local storage of downloaded EOD/corporate-action data permitted
        - redistribution restrictions (expected: no redistribution)
        - derived-data restrictions
        - API retention / caching limits
        - personal vs commercial: pricing page states listed tiers are "Personal use";
          commercial use routes to "Startups & Enterprise Data Solution". Confirm which tier the
          research use-case legally requires.
[ ] 2. Choose tier. Doc-verified (2026-06-08):
        - Free: 20 API calls/day (insufficient even for the 6-instrument trial battery if bulk).
        - EOD Historical All-World: £19.99/mo (£16.66 annual), 100,000 calls/day, 30+yr, splits,
          dividends, adjusted.  <-- minimum tier for the sample trial.
        - + Fundamentals (£59.99) if sector/industry history is needed for the sector cap.
        - All-in-One £99.99 bundles EOD + Fundamentals + Corporate Events + Bonds.
[ ] 3. Obtain API token. Store ONLY in the deployment .env as a new key (suggested name
        EODHD_API_KEY). Never commit it; never paste it into any doc, log, or chat.
[ ] 4. Confirm LSE access on the chosen tier (exchange code LSE / MIC XLON) and that delisted
        + corporate-action endpoints are included at that tier.
[ ] 5. Run the small outcome-blind sample trial (6 instruments) from
        provider_field_coverage_matrix.md §B and the battery in
        provider_trial_data_quality_report.md. Verify GBX-vs-GBP for LSE names.
[ ] 6. Store any downloaded sample OUTSIDE git (e.g. /root/research_data_tmp/), create a manifest
        (filename, provider, download time, date range, row counts, SHA-256), document the
        provider's retention/deletion requirement, and DO NOT commit raw provider data.
```

## Polygon (US-first alternative) — operator steps (only if UK is descoped)

```text
[ ] 1. Confirm there is NO LSE/UK equity coverage (doc finding: US-only) before relying on it.
[ ] 2. Review terms for delisted + corporate-action reliability (third-party reports flag gaps in
        delisted tickers, dividends, and splits — verify on a trial before trusting).
[ ] 3. Evaluate flat-file/S3 bulk access tier and rate limits.
```

## IBKR reconciliation (no signup — already integrated)

```text
[ ] Do NOT open a second IBKR session for reconciliation: the bot is live on IBKR now and a second
    session risks contention. Use the documented offline reconciliation procedure in
    provider_trial_result.md §3a instead, under separate approval.
```

## Hard stops (unchanged)

```text
[ ] Do NOT purchase or enable provider access without explicit operator approval.
[ ] Do NOT perform full historical ingestion.
[ ] Do NOT download a broad universe.
[ ] Do NOT copy any credential into git, logs, or documents.
```
