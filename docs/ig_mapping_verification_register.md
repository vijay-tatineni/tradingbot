# IG Mapping Verification Register (Workstream 2)

> **Read-only.** No IG endpoint was called; no config was changed. Mappings are transcribed
> **verbatim** from `instruments_ig.json` (the live IG config). Per task rule:
> **no order-capable mapping is treated as verified solely because it exists in the file.**
> IG reference-data verification is **not authorized** in this task and is deferred. Inspection date
> **2026-06-09 (UTC)**.

## Classification legend

```text
VERIFIED_FROM_EXISTING_EVIDENCE   confirmed from repo/config/prior C0 evidence (transcription only)
UNVERIFIED                        plausible but not confirmed against IG reference data
CONFLICTING                       evidence points two ways
CLEARLY_INVALID                   demonstrably wrong
```

Two distinct verification axes are tracked, because they answer different questions:

* **Transcription** — is the mapping correctly recorded and internally consistent in the file?
* **Order-routing** — does the EPIC actually route to the intended instrument on the live IG account?
  This requires an IG reference-data call → **UNVERIFIED for every order-capable mapping** here.

## A. Symbol ↔ EPIC ↔ currency ↔ exchange (all 10, enabled)

| Symbol | Name | IG EPIC | Ccy | Exchange | Transcription | Order-routing | Note |
|---|---|---|---|---|---|---|---|
| BARC | Barclays | `KA.D.BARC.CASH.IP` | GBP | LSE | VERIFIED_FROM_EXISTING_EVIDENCE | **UNVERIFIED** | stem matches symbol |
| ANTO | Antofagasta | `KA.D.ANTO.CASH.IP` | GBP | LSE | VERIFIED_FROM_EXISTING_EVIDENCE | **UNVERIFIED** | stem matches |
| SU | Schneider Electric | `EC.D.SU.CASH.IP` | EUR | SBF | VERIFIED_FROM_EXISTING_EVIDENCE | **UNVERIFIED** | Paris-listed (EUR), not UK — consistent |
| NBIS | Nebius Group NV | `UD.D.YNDX.CASH.IP` | USD | NASDAQ | **CONFLICTING** (symbol≠stem) | **UNVERIFIED — HIGHEST RISK** | EPIC stem `YNDX` (legacy Yandex) ≠ symbol `NBIS` — see §B |
| MSFT | Microsoft | `UC.D.MSFT.CASH.IP` | USD | NASDAQ | VERIFIED_FROM_EXISTING_EVIDENCE | **UNVERIFIED** | stem matches |
| AAPL | Apple | `UA.D.AAPL.CASH.IP` | USD | NASDAQ | VERIFIED_FROM_EXISTING_EVIDENCE | **UNVERIFIED** | stem matches |
| PLTR | Palantir | `SE.D.PLTRUS.CASH.IP` | USD | NASDAQ | UNVERIFIED (symbol≠stem) | **UNVERIFIED** | EPIC stem `PLTRUS` ≠ symbol `PLTR` — see §B |
| AVGO | Broadcom | `UA.D.AVGO.CASH.IP` | USD | NASDAQ | VERIFIED_FROM_EXISTING_EVIDENCE | **UNVERIFIED** | stem matches |
| SGLN | iShares Physical Gold (ETF) | `KA.D.SGLNLN.CASH.IP` | GBP | LSE | VERIFIED_FROM_EXISTING_EVIDENCE | **UNVERIFIED** | stem `SGLNLN` = IG LSE convention (`LN` suffix) |
| SSLN | iShares Physical Silver (ETF) | `KA.D.SSLNLN.CASH.IP` | GBP | LSE | VERIFIED_FROM_EXISTING_EVIDENCE | **UNVERIFIED** | stem `SSLNLN` = IG LSE convention |

`enabled` state: **all 10 are `enabled:true`.** `gateway` assignment: there is **no per-instrument
gateway field**; broker is set globally (`settings.broker: "ig"`). Duplicate symbols **within** the
file: **none** (10 unique). The two trees' `layer1_active` arrays are **byte-identical** — these
mappings are the same in `/root/trading` and `/root/trading-ig`.

## B. Flagged ticker/name-change mappings

### NBIS ↔ YNDX (`UD.D.YNDX.CASH.IP`) — top mapping blocker

* **Situation:** symbol `NBIS` (Nebius Group N.V.) is mapped to an EPIC whose stem is the **legacy
  Yandex** ticker `YNDX`. Nebius is the renamed/restructured successor entity to Yandex N.V. (2024).
* **Classification: UNVERIFIED (CONFLICTING on the surface).** *Why UNVERIFIED rather than
  CLEARLY_INVALID:* brokers commonly keep an **EPIC stable across a ticker/name change**, so a
  current-symbol→legacy-EPIC mapping is **plausibly correct** — it is not provably wrong from the
  file alone. *Why it is nonetheless the highest-risk item:* Yandex's 2022 trading suspension /
  sanctions history and the 2024 corporate restructuring mean the `YNDX` EPIC could be stale,
  suspended, or pointing at a different instrument. It **cannot** be confirmed without an IG
  reference-data call (not authorized here).
* This exact mapping is already flagged in `canonical_instrument_registry_design.md` §6 item 4:
  "the `UD.D.YNDX.CASH.IP` mapping must be re-verified — it may be a stale legacy mapping.
  **Do not trade it until confirmed.**" This audit upholds that: **order-capability UNVERIFIED;
  treat as a blocker.**

### PLTR ↔ PLTRUS (`SE.D.PLTRUS.CASH.IP`)

* **Situation:** symbol `PLTR` mapped to EPIC stem `PLTRUS`. `PLTRUS` is **plausibly** IG's naming
  for the US-listed Palantir line (IG sometimes suffixes `US`).
* **Classification: UNVERIFIED.** Plausible IG convention, but **not confirmed** against reference
  data. Order-capable → cannot be treated as verified merely because it is in the file.

## C. Hard-disabled instruments

* `XAUUSD` and `XAGUSD` are **not present** in `instruments_ig.json` at all. They exist only in the
  IBKR `instruments.json` as `enabled:false, hard_disabled:true,
  disabled_reason:"no_cfd_market_data_paper_account"`. **No hard-disabled instrument is enabled (or
  present) in the IG universe** → no IG hard-disabled violation today.
* **Safety asymmetry (carried from C0):** the IG tree's `main.py` **lacks** the
  `validate_hard_disabled_instruments` guard the modern tree enforces. So if a hard-disabled
  instrument were ever added to `instruments_ig.json`, the **current** IG path would **not** block
  it. Consolidating the IG service onto the modern tree **adds** this guard to the IG path — a safety
  improvement, not a regression. The deployed hard-disabled invariant remains unchanged and
  authoritative.

## D. IBKR ↔ IG overlap (enabled in both universes)

IBKR `instruments.json` enabled `layer1_active` (excludes the two hard-disabled): `SGLN, SSLN, TSM,
AVGO, ANET, SU, SCCO, ANTO, PLTR, NBIS, AAPL, MSFT, BARC, NVTS` (14).
IG `instruments_ig.json` enabled: `BARC, ANTO, SU, NBIS, MSFT, AAPL, PLTR, AVGO, SGLN, SSLN` (10).

```text
ENABLED IN BOTH (10):  SGLN SSLN AVGO SU ANTO PLTR NBIS AAPL MSFT BARC
IBKR-only enabled (4): TSM ANET SCCO NVTS
```

Currencies agree across the overlap (GBP: BARC/ANTO/SGLN/SSLN; EUR: SU; USD: the rest). The
duplicate-execution analysis of this overlap is in `ig_consolidation_preservation_requirements.md`.

## E. Mapping verification status — summary

```text
Mappings transcribed & internally consistent:        9 / 10  (NBIS↔YNDX surface-conflicting)
Mappings verified for ORDER ROUTING:                 0 / 10  (no IG reference-data call authorized)
Mappings that BLOCK safe consolidation:              ALL order-capable mappings remain UNVERIFIED
                                                     for routing; NBIS↔YNDX is the top blocker,
                                                     PLTR↔PLTRUS second.
```

**No order-capable mapping is certified by this audit.** IG reference-data verification
(`ig.fetch_market_by_epic` per registry design §4) is a **separate, explicitly-authorized** future
task and is **not** performed here.
