# EODHD Trial — Manifest (Workstream 1)

> Records exactly what was downloaded during the trial. **No approved EODHD access exists**
> (`eodhd_trial_result.md` §0), so the download portion of the trial did not run.

## Download status

```text
APPROVED EODHD ACCESS:   NO  (no EOD*/EODHD* key in /root/trading/.env or /root/trading-ig/.env)
DATA DOWNLOADED:         NONE
PROVIDER FILES CREATED:  NONE
TEMPORARY RESEARCH DIR:  NOT CREATED (no data to store)
ACCOUNT CREATED:         NO
PAID TRIAL STARTED:      NO
PAYMENT DETAILS ENTERED: NO
CREDENTIALS HANDLED:     NONE (env-var key names only were inspected; no values read/logged/committed)
```

## What WAS produced (public-doc inspection only — no provider data)

| Artifact | Type | Location | Committed? |
|---|---|---|---|
| `eodhd_trial_result.md` | analysis | `docs/` (this branch) | yes (redacted) |
| `eodhd_field_coverage_matrix.md` | analysis | `docs/` | yes (redacted) |
| `eodhd_data_quality_report.md` | analysis (battery only, not run) | `docs/` | yes (redacted) |
| `eodhd_licensing_and_storage_review.md` | analysis (incl. T&C fetch) | `docs/` | yes (redacted) |
| EODHD T&C public page | external doc (fetched, not stored) | `https://eodhd.com/financial-apis/terms-conditions` | not stored |

## Retention / deletion obligations (for a FUTURE approved trial)

If a separately approved trial downloads sample data, the EODHD T&C impose:

```text
- Local storage permitted ONLY during the active subscription period.
- ALL copies of the data must be deleted within ONE (1) MONTH of subscription termination/expiry.
- The future manifest must record this obligation and the deletion deadline, and raw data must be
  purged accordingly while keeping only non-data metadata (hashes/manifests) if compatible with Q5.
```

## Manifest template (to fill on a future approved download)

```text
provider:            EODHD
endpoint(s):         <e.g. /eod/AAPL.US, /splits/AAPL.US, /div/BARC.LSE, /delisted, /fundamentals>
download_timestamp:  <UTC ISO8601>
files:               <filename per response>
row_counts:          <per file>
date_ranges:         <earliest..latest per file>
sha256:              <per file>
storage_dir:         /root/research_data_tmp/eodhd_<UTC>/   (OUTSIDE git)
deletion_deadline:   <subscription_end + 1 month>
notes:               raw responses preserved unchanged; no credentials/signed URLs committed
```

**No manifest rows exist because no data was downloaded.**
