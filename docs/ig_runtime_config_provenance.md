# IG Runtime Config Provenance (Workstream 2)

> **Read-only.** No service, config, flag, or file was changed; no broker call was made; no secret or
> account ID is exposed (env-var **key names only**). Inspection date **2026-06-09 (UTC)**. Branch
> `planning/dynamic-universe-blocker-clearance` (base `planning/eodhd-trial-ig-equivalence @ ac7cf53`).

## 1. Deployed IG service — exact runtime configuration

| Item | Value (redacted where required) |
|---|---|
| IG bot systemd unit | `cogniflowai-ig-bot.service` (`active`) |
| IG API systemd unit | `cogniflowai-ig-api.service` (`active`) |
| Bot `ExecStart` | `/usr/bin/python3 /root/trading-ig/main.py --broker ig --config /root/trading-ig/instruments_ig.json` |
| API `ExecStart` | `/usr/bin/python3 /root/trading-ig/api_server.py --config /root/trading-ig/instruments_ig.json` |
| `WorkingDirectory` | `/root/trading-ig` |
| `EnvironmentFile` | none in unit; app loads `/root/trading-ig/.env` (gitignored) |
| Env-var key names | `IG_USERNAME IG_PASSWORD IG_API_KEY IG_ACC_TYPE IG_ACC_NUMBER` (+ shared `ANTHROPIC_API_KEY`, `FINNHUB_API_KEY`, `GROQ_API_KEY`, `JWT_SECRET`, `API_TOKEN`, `TELEGRAM_*`, `IB_*`) — **values not read** |
| Running bot process | PID `503086`, **started Wed Jun 3 06:22:38 2026** |
| Running API process | PID `502982` |
| Deployed checkout | `/root/trading-ig` |
| Branch / HEAD | `claude-strategy` @ `d95d258` (unchanged) |
| Effective config file loaded | `/root/trading-ig/instruments_ig.json` (**has** the `feature_flags` block, incl. `enable_regime_filter_live: true`) |
| Active instruments | 10 (per startup banner "Active : 10 instruments") |

## 2. Config epoch of the running process

The running bot (PID 503086) **started 2026-06-03 06:22:38**, which matches the startup banner at
that timestamp in `bot_stdout.log`. The flag-summary line emitted immediately after that banner reads:

```text
regime_filter: live=true
Effective mode: LIVE (some live flags enabled)
```

So the **currently running process loaded `enable_regime_filter_live = true`** from the live
`instruments_ig.json` `feature_flags` block.

### Earlier epoch (config without `feature_flags`)

`bot_stdout.log` also contains an earlier flag-summary line `regime_filter: live=false`, and
`bot_stderr.log` contains `Missing flag 'enable_regime_filter_live' in config, using safe default:
False`. These are from a **pre-2026-06-01 epoch** when `instruments_ig.json` had no `feature_flags`
block (the block was added to the live IG config on/around 2026-06-01; the modern tree's committed
copy still lacks it). This is the documented behaviour of `bot/regime/flags.py`: a missing key →
`SAFE_DEFAULTS` → `False` ("Missing config keys never enable live behaviour").

**Net:** the flag's *configured/loaded* value has moved `false → true` across redeploys; the
**current** process is in the `true` epoch.

## 3. Observed login/session instability (provenance, not present state)

`bot_stderr.log` records two distinct IG failure modes, in this order over time:

```text
- error.security.api-key-invalid (HTTP 403): 184 lines, up to "96 consecutive" — an EARLIER
  transient login-failure episode. During it, qualify_contracts logged 548
  "Could not verify epic …: 'NoneType' object has no attribute 'fetch_market_by_epic'"
  (i.e. self.ig was None — not logged in).
- unauthorised.access.to.equity.exception: 33,656 lines — the CURRENT and dominant state
  (the last line of bot_stderr.log is this exception). It is a SERVER-SIDE error that requires
  an authenticated session, so IG sessions DO occur; the demo account simply lacks cash-equity
  historical-data entitlement (consistent with docs/TECH_DEBT.md).
```

**Logging-level caveat (per task — absence of a line ≠ proof a path didn't run):** the INFO-level
lines `Connected to IG` and `Verified IG epic` do **not** appear in `bot_stdout.log`,
`bot_stderr.log`, or `portfolio_bot.log`. This is most consistent with **INFO-level filtering** on
the IG logger, **not** proof that the session never connected — the 33,656 server-side entitlement
exceptions are positive evidence that authenticated sessions occurred.

## 4. Evidence handling

Raw read-only log greps (counts + redacted representative lines; no secrets/account IDs) are sealed
outside git:

```text
restricted dir   /root/consolidation_evidence/blocker_clearance_20260609/   (mode 0700)
ig_runtime_evidence.txt   sha256 a3888a4afdc8072e818775faa609080698e73ddc8a8382c5e4fd23df6aeb8e29
```

**No raw IG response, credential, or unredacted log is committed to git.**
