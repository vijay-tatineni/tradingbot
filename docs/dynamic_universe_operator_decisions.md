# Dynamic Universe Hybrid v1 — Open-Decision Register

> **Design/planning only.** No decision below is enacted; no threshold is silently frozen.
> Part of `design/dynamic-universe-hybrid-v1`; master index
> `experiments/dynamic_universe_hybrid_v1_DRAFT.md`. This is **Task 6**. Each row:
> *decision · options · recommended · reason · cost/risk · work blocked · changes the trading
> hypothesis?*

"Changes the trading hypothesis?" flags whether the choice alters **what is traded or how a
trade is decided** (which would require separate pre-registration) vs. pure
infrastructure/operations.

---

### D1 — Historical-data provider  *(foundational blocker #1)*
* **Options:** EODHD · Polygon · Tiingo · Norgate (US-only) · Sharadar/NDL (US-only) ·
  status-quo IBKR+IG (survivor-only).
* **Recommended:** **EODHD** primary (US+UK, corporate actions, delisted, bulk, low–mid cost),
  IBKR as recon secondary — see `historical_data_provider_selection.md`. All capability claims
  are **[I] re-verify on a trial**.
* **Reason:** only realistic single source covering the actual **US+UK** universe
  survivorship-free with corporate actions.
* **Cost/risk:** subscription cost (unquoted, [I]); licensing/redistribution limits;
  capability claims unverified until trial.
* **Blocks:** Phases 8–10 (ingestion, historical validation, paper).
* **Hypothesis?** **No** (data quality), **but** enables the eventual dynamic-universe
  hypothesis, which itself needs separate pre-registration (D16).

### D2 — Sector/industry source
* **Options:** primary provider fundamentals (EODHD/Polygon reference) · manual operator
  classification · none (disable sector cap).
* **Recommended:** primary provider's GICS-style classification.
* **Reason:** `sector`/`industry` are **absent today** (verified, `instruments.json`); the §2
  "≤2 open per sector" rule cannot run without them.
* **Cost/risk:** provider coverage/accuracy for UK names; point-in-time sector history is harder.
* **Blocks:** sector-cap enforcement (Phase 5 risk view).
* **Hypothesis?** **Yes (minor)** — the sector cap is a risk rule; its data source affects which
  trades are blocked. Document the source in the pre-registration.

### D3 — Canonical instrument ID format
* **Options:** `<REGION>_<SYMBOL>` (e.g. `US_AAPL`) · provider-permid-based · UUID.
* **Recommended:** human-readable `<REGION>_<SYMBOL>` as the surface key, **anchored** on
  `research_provider_permid` for identity stability across renames/reuse.
* **Reason:** readability for operators + a permanent anchor (the NBIS↔`YNDX` case shows tickers
  drift).
* **Cost/risk:** dual-listing collisions; ticker reuse after delisting — needs a tie-break rule.
* **Blocks:** M1 registry migration (Phase 3).
* **Hypothesis?** **No** (identity/infra).

### D4 — `universe.db` vs another database
* **Options:** new `universe.db` (recommended) · extend `regime.db` · extend `positions.db` ·
  one consolidated DB.
* **Recommended:** **new `universe.db`** for the control plane; `execution.db` (or extended
  `positions.db`) for intents/positions; `research_data.db` for bars — see
  `dynamic_universe_storage_and_migration_plan.md`.
* **Reason:** do not couple the load-bearing universe control plane to the experimental
  `regime.db`; separate lifecycles/blast radius.
* **Cost/risk:** more DB files to back up; cross-DB FKs are app-enforced (SQLite).
* **Blocks:** M1–M7 migrations (Phase 3+).
* **Hypothesis?** **No** (infra).

### D5 — Hysteresis values
* **Options:** `N_data=2` sustained sessions (proposed, mirrors `PENDING_DAYS_TO_PROMOTE=2`) ·
  other values.
* **Recommended:** start `N_data=2`; revisit with shadow evidence. **Proposed, not frozen.**
* **Reason:** gaining eligibility should be sticky-slow; precedent in `bot/regime/smoothing.py`.
* **Cost/risk:** too high → slow to re-admit a recovered feed; too low → flapping.
* **Blocks:** Phase 4 evaluator.
* **Hypothesis?** **Yes (minor)** — affects when an instrument may newly enter; pre-register.

### D6 — Cooldown values
* **Options:** minutes-based (current `reentry_cooldown_mins`, default 30) · **session-based**
  (proposed: cooldown to next session open).
* **Recommended:** **session-based** for the daily-bar dynamic universe (no same-session churn).
* **Reason:** the strategy is daily/next-session; minute cooldowns are a 4hr-bot artifact.
* **Cost/risk:** session-based blocks faster re-entry; needs exchange-calendar counting.
* **Blocks:** Phase 4 cooldown logic.
* **Hypothesis?** **Yes** — re-entry timing is part of the strategy; pre-register.

### D7 — Manual/TTI candidate TTL
* **Options:** **5 completed trading sessions** (already approved, task §2) · other.
* **Recommended:** 5 sessions, counted on the instrument's exchange calendar.
* **Reason:** approved; keeps discovery inputs fresh without permanent injection.
* **Cost/risk:** expiry could drop a still-valid idea (mitigated by resubmission).
* **Blocks:** `candidate_sources` TTL (Phase 3/4).
* **Hypothesis?** **No** (discovery input; downstream gates unchanged).

### D8 — Master-universe size
* **Options:** start small (current ~29 known) · mid (a few hundred US+UK liquid names) · broad.
* **Recommended:** **operator decision deferred to Phase 1/3**; do **not** size around the
  current 14/29 (task §2). Likely a mid liquid US+UK set once a provider exists.
* **Reason:** size is bounded by provider coverage + the ADV/price structural gates, not by the
  frozen experiment's symbols.
* **Cost/risk:** larger universe → more data cost, more candidates, more compute.
* **Blocks:** registry population (Phase 3), ingestion scope (Phase 8).
* **Hypothesis?** **Yes (scope)** — universe definition is part of the dynamic-universe
  hypothesis; pre-register the membership methodology (D16).

### D9 — US/UK initial scope
* **Options:** US-only first (enables Norgate/Sharadar survivorship specialists) · US+UK from
  the start (forces EODHD/Polygon-class provider).
* **Recommended:** **decide before D1** — this is the pivot that determines the provider.
  Recommendation leans US+UK (matches the existing live universe), accepting EODHD.
* **Reason:** the live universe already spans LSE; US-only specialists don't cover UK.
* **Cost/risk:** US+UK widens provider cost/complexity; US-only narrows scope vs current.
* **Blocks:** D1, Phases 1/8.
* **Hypothesis?** **Yes (scope)** — pre-register.

### D10 — IG execution scope
* **Options:** IG execution/reconciliation-only (task default, recommended) · IG also as a
  data source (blocked by entitlement) · IG retired.
* **Recommended:** **execution/reconciliation-only**; not a research source.
* **Reason:** verified demo cash-equity entitlement block (`docs/TECH_DEBT.md`); task §7 default.
* **Cost/risk:** IG equities won't trade until entitlement resolved (D11).
* **Blocks:** Phase 6 IG path scope.
* **Hypothesis?** **No** (execution venue).

### D11 — IG equity entitlement limitation
* **Options:** upgrade IG account entitlements (external) · switch IG to supported types
  (indices/FX/CFD) · accept IG dashboard-only (current "mode 3").
* **Recommended:** operator decision; **external dependency** — pursue entitlement upgrade only
  if IG execution of equities is wanted.
* **Reason:** verified `unauthorised.access.to.equity.exception` on `*.CASH.IP`.
* **Cost/risk:** account/support cost; switching instruments would need separate re-validation.
* **Blocks:** any live IG equity execution (Phase 6/10 IG leg).
* **Hypothesis?** **No** (venue capability); but switching IG instruments **would** (D9-like).

### D12 — NBIS/`YNDX` mapping re-verification
* **Options:** re-verify `UD.D.YNDX.CASH.IP` against live IG reference data · drop NBIS from IG
  until confirmed.
* **Recommended:** **re-verify before any IG action on NBIS**; treat as `UNRESOLVED` until then.
* **Reason:** verified symbol↔EPIC divergence (`YNDX` = legacy Yandex) — likely stale.
* **Cost/risk:** trading a wrong instrument if unverified — **safety-relevant**.
* **Blocks:** IG NBIS routing (Phase 6).
* **Hypothesis?** **No** (mapping correctness) — but a wrong mapping is a real-money hazard.

### D13 — Primary-gateway assignment rules
* **Options:** per-instrument explicit `primary_execution_gateway` in registry (recommended) ·
  rule-based (e.g. UK→IG, US→IBKR) · operator-assigned.
* **Recommended:** **explicit per-instrument** field (registry), seeded by a documented default
  rule, overridable by operator. No automatic fallback (task §6).
* **Reason:** explicitness + auditability; one gateway per instrument.
* **Cost/risk:** manual upkeep for large universes.
* **Blocks:** router (Phase 5), registry (Phase 3).
* **Hypothesis?** **No** (routing/infra).

### D14 — Dual-codebase consolidation timing
* **Options:** consolidate before registry work (recommended) · in parallel · after.
* **Recommended:** **before Phase 3** (Phase 2), per roadmap — one codebase is a precondition
  for one registry/risk engine. Aligns with pending **PR7 (`7a9dd06`)**.
* **Reason:** registry/runtime on two un-synced trees re-creates the drift problem.
* **Cost/risk:** consolidation touches the live IG service (C7 high-risk step).
* **Blocks:** Phases 3–11 (cleanly).
* **Hypothesis?** **No** (infra).

### D15 — Forward-shadow duration
* **Options:** fixed N sessions · until a minimum decision count · until a calendar window.
* **Recommended:** **operator-set**; a session-count + minimum-distinct-episode threshold (echo
  the breakout 3b lesson — beware low-independent-sample). **Proposed, not frozen.**
* **Reason:** shadow must run long enough to be informative before any activation (Phase 10).
* **Cost/risk:** too short → activate on noise; too long → delayed value.
* **Blocks:** Phase 10 gate.
* **Hypothesis?** **Yes** — the activation gate is part of the validation protocol;
  pre-register.

### D16 — Point-in-time historical universe methodology
* **Options:** as-of membership from provider listing/delisting + liquidity filters
  (recommended) · naive current-survivors (**prohibited** — survivorship bias).
* **Recommended:** as-of point-in-time membership, snapshot-pinned via `dataset_manifest`
  (SHA-256). **Never** current survivors as history.
* **Reason:** verified survivorship bias in current data (`historical_data_feasibility.md`).
* **Cost/risk:** depends entirely on D1 provider; complex to build correctly (Phase 8 = L).
* **Blocks:** Phase 9 historical validation.
* **Hypothesis?** **Yes (core)** — the universe-construction method **is** the dynamic-universe
  hypothesis and **requires separate pre-registration** before any historical results.

---

## Decision dependency summary

```text
D9 (US/UK scope)  ──▶ D1 (provider)  ──▶ D8 (universe size), D16 (PIT methodology) ──▶ Phases 8–9
D14 (consolidation timing) ──▶ Phase 2 ──▶ Phases 3+
D4 (DB boundary)  ──▶ Phase 3 migrations
D5/D6/D7 (hysteresis/cooldown/TTL) ──▶ Phase 4 evaluator   [all PROPOSED, not frozen]
D10/D11/D12 (IG scope/entitlement/EPIC) ──▶ Phase 6 IG path
D2 (sector), D13 (gateway assignment) ──▶ Phase 3/5
D15 (shadow duration) ──▶ Phase 10 gate
```

**No numeric threshold (D5, D6, D7, D8, D15) is frozen here** — all are proposals requiring
operator approval. The hypothesis-affecting decisions (D2, D5, D6, D8, D9, D15, D16) must be
captured in the **separate dynamic-universe pre-registration** before any historical result is
produced.
