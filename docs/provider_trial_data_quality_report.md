# Provider Trial — Data-Quality Report (Workstream A)

> Outcome-blind. **No sample data was downloaded** (no approved EODHD/Polygon access; creating
> access is outside this authorization — see `provider_trial_result.md` §0). Therefore the
> per-instrument data-quality checks below **could not be executed**. This document records the
> exact check battery, the procedure, and the pass/fail thresholds so a **future, separately
> approved** trial can run them deterministically. No price movement is interpreted as strategy
> evidence; no PF/Sharpe/returns computed.

## Status of the data-quality battery

```text
EXECUTED: NONE — no provider data available without authorized access.
```

## Check battery (to run per sample instrument once access is approved)

For the sample set (`AAPL`, `LEH`, `BARC.LSE`, `TT.LSE`, `SGLN.LSE`, `FB→META`), each check is a
**mechanical data-integrity test on the provider's series and metadata** — never a returns/PF/
Sharpe/breakout computation.

| # | Check | Method | Flag condition | Status |
|---|---|---|---|---|
| 1 | Duplicate dates | group by date, count>1 | any duplicate date | NOT RUN (no data) |
| 2 | Negative prices/volume | min(O,H,L,C,V) < 0 | any negative | NOT RUN |
| 3 | high < low | row-wise H<L | any row | NOT RUN |
| 4 | high below open/close | H < max(O,C) | any row | NOT RUN |
| 5 | low above open/close | L > min(O,C) | any row | NOT RUN |
| 6 | Unexpected date gaps | compare to exchange trading calendar (NYSE / LSE) | gap not explained by holiday | NOT RUN |
| 7 | Extreme move w/o corporate action | |Δln(close)| over threshold with no split/div on date | unexplained jump | NOT RUN |
| 8 | Currency inconsistency | currency field constant & matches exchange (USD/GBP/GBX) | mismatch / GBP-vs-GBX confusion | NOT RUN |
| 9 | Ticker-change continuity | series spans `FB→META` without break; identifier stable | discontinuity at rename | NOT RUN |
| 10 | Raw-vs-adjusted consistency | adjusted = raw on/after last corporate action; diverges only before | inconsistency | NOT RUN |
| 11 | Split-factor consistency | adjusted ratio at split date == documented split factor | factor mismatch | NOT RUN |

**LSE-specific watch items** (documented for the future run, since UK is the decisive constraint):

* **GBX vs GBP** — LSE equities are frequently quoted in pence (GBX). Check #8 must confirm whether
  EODHD returns pence or pounds and whether volume/price units are internally consistent. This is a
  known UK data-integrity trap and **must be verified on the trial**.
* **Delisted UK depth** — `TT.LSE` (Thomas Cook) tests whether EODHD's delisted dataset actually
  carries an LSE-delisted name with a delisting date, not just US delistings.

## Why not executed

The only no-credential price source available in this environment is `yfinance` (research scripts
only, known survivorship limitation) — it is **not** the candidate under trial (EODHD/Polygon) and
substituting it would not verify the candidate. Per the authorization boundary, the data-download
portion is **stopped** and the operator signup/trial checklist is produced instead
(`provider_operator_signup_checklist.md`).

## Result

```text
Data-quality verdict: DEFERRED — requires approved trial access.
No data quality issues can be confirmed or denied without provider data.
```
