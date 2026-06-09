# EODHD Trial — Data-Quality Report (Workstream 1)

> Outcome-blind. **No sample data was downloaded** (no approved EODHD access; creating access is
> outside this authorization — see `eodhd_trial_result.md` §0). The per-instrument data-quality
> checks below therefore **could not be executed**. This document records the exact check battery,
> method, and flag conditions so a **future, separately approved** trial can run them
> deterministically. No price movement is interpreted as strategy evidence; no PF / Sharpe /
> returns / breakout signals computed.

## Status

```text
EXECUTED: NONE — no provider data available without authorized access.
DATA-QUALITY VERDICT: DEFERRED — requires approved trial access.
```

## Check battery (run per sample instrument once access is approved)

Sample set: `AAPL.US`, `LEH.US`, `BARC.LSE`, `TT.LSE`, `SGLN.LSE`, `FB→META`, `YNDX→NBIS`. Each
check is a **mechanical data-integrity test on the provider's series/metadata** — never a
returns/PF/Sharpe/breakout computation.

| # | Check | Method | Flag condition | Status |
|---|---|---|---|---|
| 1 | Duplicate dates | group by date, count>1 | any duplicate session | NOT RUN (no data) |
| 2 | Negative prices / volume | min(O,H,L,C,V) < 0 | any negative | NOT RUN |
| 3 | high < low | row-wise H < L | any row | NOT RUN |
| 4 | high below open or close | H < max(O,C) | any row | NOT RUN |
| 5 | low above open or close | L > min(O,C) | any row | NOT RUN |
| 6 | Unexpected long gaps | compare to NYSE / LSE trading calendar | gap not explained by holiday | NOT RUN |
| 7 | Extreme move w/o corporate action | \|Δln(close)\| over threshold with no split/div that date | unexplained jump | NOT RUN |
| 8 | Raw-vs-adjusted consistency | adjusted == raw on/after last corporate action; diverges only before | inconsistency | NOT RUN |
| 9 | Split-factor consistency | adjusted ratio at split date == documented split factor | factor mismatch | NOT RUN |
| 10 | Ticker-change continuity | series spans `FB→META` / `YNDX→NBIS` without break; identifier stable | discontinuity at rename | NOT RUN |
| 11 | Currency consistency | currency field constant & matches exchange | mismatch | NOT RUN |
| 12 | **GBP vs GBX scaling** | LSE names: confirm whether EODHD returns pence or pounds; price/volume internally consistent | pence/pound confusion or scale break | NOT RUN |

**LSE-specific watch items** (documented for the future run — UK is the decisive constraint):

* **GBX vs GBP (check #12)** — LSE equities are frequently quoted in pence (GBX). This is a known
  UK data-integrity trap and **must be verified on the trial**. It also matters directly for our IG
  universe (`BARC`, `ANTO`, `SGLN`, `SSLN` are configured `currency:"GBP"`) — see
  `ig_mapping_verification_register.md`.
* **Delisted UK depth (checks #6/#10)** — `TT.LSE` (Thomas Cook) tests whether the delisted dataset
  actually carries an **LSE-delisted** name with a delisting date, not just US delistings.

## Why not executed

The only no-credential price source in this environment is `yfinance` (research scripts only, known
survivorship limitation) — it is **not** the candidate under trial (EODHD) and substituting it would
not verify EODHD. Per the authorization boundary the data-download portion is **stopped** and the
operator signup/trial checklist is the next step (`provider_operator_signup_checklist.md`).
