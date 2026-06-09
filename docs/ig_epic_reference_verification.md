# IG EPIC & Instrument Reference Verification (Workstream 1)

> **Read-only.** No IG dealing/order/reference endpoint was called by this task; no order/position/
> watchlist/preference/account state was created or changed; no service was restarted; the running IG
> session was not interrupted; no credential/token/account ID is exposed. Inspection date
> **2026-06-09 (UTC)**.

## 1. Safe-access determination (checked FIRST)

A read-only IG reference call is permitted **only if all** boundary conditions hold. Assessment:

| Condition | Status | Evidence |
|---|---|---|
| Approved IG access already exists | partial | `IG_*` keys present in `/root/trading-ig/.env`; an IG **demo** session is live (PID 503086 cycling) |
| Endpoint demonstrably read-only | yes (for `fetch_market_by_epic`) | `bot/brokers/ig.py:166` market-info fetch is read-only |
| **Does not require restarting/replacing the running session** | **FAILS** | IG REST sessions are **stateful** (`IGService.create_session()`, `ig.py:107`; login-failure backoff `ig.py:114-125`; `reconnect()` = `logout()`+`login`, `ig.py:136-140`). I hold **no safe handle to the running bot's session**; making a call would require a **competing login** for the same account, which can replace/invalidate the live session. |
| Cannot create/modify order/position/watchlist/preference/account | yes (market-info only) | n/a |
| Credentials/headers handled without logging secrets | risky | a competing login would handle live credentials outside the service |

Two further facts make a call both unsafe and unproductive:

* The IG **demo account lacks cash-equity historical-data entitlement** (33,656
  `unauthorised.access.to.equity.exception` in the IG logs) — reference-snapshot access on
  `*.CASH.IP` epics is not assured on this account.
* An **earlier transient `error.security.api-key-invalid` (403)** episode (184 log lines) shows the
  credentials' authentication is not consistently reliable.

**Determination: safe authenticated IG reference access is UNCERTAIN → IG was NOT called.** Per the
task, every mapping is therefore classified `UNVERIFIED_NO_SAFE_ACCESS`, and the exact operator-run
verification procedure is provided (§4).

## 2. Runtime log evidence (read-only) — no positive confirmation available

The deployed IG bot's `qualify_contracts` (`ig.py:149-181`) calls `fetch_market_by_epic` at startup
and logs `Verified IG epic: <symbol> → <epic>` on success. In the entire retained window
(2026-04-16 → 2026-06-09):

```text
"Verified IG epic" (successful resolution):                       0
"Could not verify epic …: 'NoneType' … fetch_market_by_epic":   548   (qualify ran while not logged in)
```

So the running service provides **no positive runtime evidence** that any of the 10 EPICs resolve to
the intended instrument. Source list = the live `instruments_ig.json` (10 enabled cash-equity epics;
the two trees' `layer1_active` arrays are byte-identical).

## 3. Per-mapping classification (all enabled IG instruments)

Fields requested (returned instrument name, type, market status, exchange, currency, expiry/CASH,
deal-size/stop/value-per-point/margin, trading hours, account-tradeability, identity) are **all
unobtainable without a safe IG reference call** → recorded as **NOT AVAILABLE (no safe access)**.

| Symbol | Configured EPIC | Ccy | Repo evidence | Runtime evidence | Classification |
|---|---|---|---|---|---|
| NBIS | `UD.D.YNDX.CASH.IP` | USD | symbol≠stem (`YNDX` legacy Yandex) | not resolved | **UNVERIFIED_NO_SAFE_ACCESS** — identity UNRESOLVED, **highest risk** |
| PLTR | `SE.D.PLTRUS.CASH.IP` | USD | symbol≠stem (`PLTRUS`) | not resolved | **UNVERIFIED_NO_SAFE_ACCESS** — second priority |
| SGLN | `KA.D.SGLNLN.CASH.IP` | GBP | stem `SGLNLN` (ETF) | not resolved | **UNVERIFIED_NO_SAFE_ACCESS** |
| SSLN | `KA.D.SSLNLN.CASH.IP` | GBP | stem `SSLNLN` (ETF) | not resolved | **UNVERIFIED_NO_SAFE_ACCESS** |
| AVGO | `UA.D.AVGO.CASH.IP` | USD | stem matches | not resolved | **UNVERIFIED_NO_SAFE_ACCESS** |
| SU | `EC.D.SU.CASH.IP` | EUR | stem matches (Paris-listed) | not resolved | **UNVERIFIED_NO_SAFE_ACCESS** |
| ANTO | `KA.D.ANTO.CASH.IP` | GBP | stem matches | not resolved | **UNVERIFIED_NO_SAFE_ACCESS** |
| AAPL | `UA.D.AAPL.CASH.IP` | USD | stem matches | not resolved | **UNVERIFIED_NO_SAFE_ACCESS** |
| MSFT | `UC.D.MSFT.CASH.IP` | USD | stem matches | not resolved | **UNVERIFIED_NO_SAFE_ACCESS** |
| BARC | `KA.D.BARC.CASH.IP` | GBP | stem matches | not resolved | **UNVERIFIED_NO_SAFE_ACCESS** |

No mapping is classified `VERIFIED_REFERENCE_MATCH`, `VERIFIED_BUT_NOT_ACCOUNT_TRADEABLE`,
`CONFLICTING_IDENTITY`, `CLEARLY_WRONG`, or `RETIRED_OR_UNAVAILABLE` — those require IG reference
data this task did not (and could not safely) obtain.

### NBIS ↔ YNDX — explicit four-way identity question (UNRESOLVED)

The EPIC stem `YNDX` is the legacy Yandex ticker; the configured symbol/name is `NBIS`/"Nebius Group
NV". Without IG reference data I **cannot determine** which of the following the EPIC currently
represents — and identity must **not** be inferred from ticker similarity:

```text
[ ] the intended current NBIS (Nebius) security
[ ] a legacy Yandex instrument
[ ] a renamed/restructured instrument (broker kept the EPIC stable across the rename — plausible)
[ ] a different or unavailable/suspended product
```

A broker holding an EPIC stable across a corporate rename is plausible, so this is a **naming
mismatch, not disconfirming evidence** → the honest state is **UNVERIFIED / identity UNRESOLVED**,
flagged **highest-risk** (Yandex's 2022 suspension and 2024 restructuring history). It must be the
first EPIC an operator verifies and must remain order-blocked until then.

### PLTR ↔ PLTRUS — explicit verification requirement (UNRESOLVED)

`PLTRUS` is plausibly IG's naming for US-listed Palantir, but the EPIC→instrument binding and the
**product type** (cash share vs. other) are **not confirmed** → **UNVERIFIED**. Second-priority
operator verification.

## 4. Operator-run verification procedure (read-only; under explicit approval)

```text
[ ] 1. Run OUTSIDE the live ig-bot session window, OR via a separate read-only IG session that does
        not displace cogniflowai-ig-bot (confirm IG account concurrent-session policy first).
[ ] 2. For each epic, call the READ-ONLY market-info endpoint (fetch_market_by_epic / GET
        /markets/{epic}). Do NOT call any dealing/position endpoint.
[ ] 3. Record per epic: instrumentName, instrumentType, marketStatus, expiry (expect DFB/CASH),
        currency, lotSize/minDealSize, minControlledRiskStopDistance/minNormalStopOrLimitDistance,
        valuePerPoint/contractSize, marginFactor, and openingHours.
[ ] 4. NBIS/YNDX first: confirm instrumentName resolves to the intended Nebius security (not a
        suspended/legacy Yandex line). PLTR/PLTRUS second: confirm Palantir + correct product type.
[ ] 5. Confirm tradeability on the configured account type (note the demo cash-equity entitlement
        gap may make some checks impossible on demo — a live/entitled account may be required).
[ ] 6. Reclassify each mapping using the WS1 scheme; commit only the redacted classification — never
        raw IG responses, tokens, or account IDs.
```

**No order-capable mapping may be recommended for use until reclassified `VERIFIED_REFERENCE_MATCH`
and confirmed account-tradeable.**
