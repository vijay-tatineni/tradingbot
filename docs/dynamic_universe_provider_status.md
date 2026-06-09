# Dynamic Universe — Provider Status (pointer)

> Documentation pointer only. No provider access was purchased, created, or called in
> this task. Date: 2026-06-09 (UTC).

## Status

```text
Approved EODHD access:   NONE  (re-confirmed: no EOD*/EODHD* key in either .env)
Verdict:                 TRIAL ACCESS REQUIRED BEFORE DECISION  (unchanged)
```

## What this means for the shadow foundation

The IBKR-only dynamic-universe **shadow foundation** (`bot/universe/`) is structural and
operational: it builds the registry, state engine, and hypothetical evaluation on
existing/synthetic data. It does **not** depend on a new provider.

**Historical validation** of the universe (survivorship-aware corporate-action-correct
US+UK history) remains **blocked** pending the operator-approved **seven-case EODHD
sample trial** defined previously:

```text
AAPL.US, LEH.US, BARC.LSE, TT.LSE, SGLN.LSE, FB/META, YNDX/NBIS
```

See `docs/eodhd_trial_result.md`, `docs/eodhd_field_coverage_matrix.md`,
`docs/eodhd_licensing_and_storage_review.md`, and
`docs/provider_operator_signup_checklist.md` (from prior tasks) for the exact operator
steps. Do not purchase access, create an account, download data, call EODHD, or build
full historical ingestion without explicit approval.
