# Dynamic Universe Hybrid v1 — Revised Implementation Roadmap

> **Design/planning only.** No phase below is executed in this task. Part of
> `design/dynamic-universe-hybrid-v1`; master index
> `experiments/dynamic_universe_hybrid_v1_DRAFT.md`. This is **Task 5**. It supersedes the
> phase sketch in DRAFT §15 by inserting the two foundational blockers (provider decision,
> codebase consolidation) **before** any registry/runtime work, per the approved direction.

Effort sizes: **S** ≈ days · **M** ≈ 1–2 weeks · **L** ≈ 3+ weeks. Every phase is gated by
explicit operator approval; nothing proceeds from this design without it. The frozen
fixed-14 breakout result stays PROVISIONAL and untouched throughout.

---

## Phase 0 — Design complete ✅
* **Dependencies:** none.
* **Deliverables:** the 5 prior design docs (commit `8acd1b8`) + the 5 planning docs in this
  commit (provider matrix, consolidation, storage/migration, this roadmap, operator decisions).
* **Tests:** documentation only.
* **Stop condition:** n/a (done).
* **Approval:** design approved in principle (granted).
* **Effort:** done.

## Phase 1 — Provider decision (foundational blocker #1)
* **Dependencies:** Phase 0; operator engagement on `docs/historical_data_provider_selection.md`.
* **Deliverables:** chosen research-data provider; finalized canonical data schema
  (`raw_bars`/`corporate_actions`/`dataset_manifest`); US/UK scope decision; sector source.
* **Tests:** none executable — a **trial-data verification checklist** (confirm the [I] claims:
  delisted coverage, corporate actions, listing dates, US+UK) to run *after* approval, on a
  trial, before purchase.
* **Stop condition:** do **not** purchase/download until the operator approves the provider
  AND the trial verifies survivorship-free + corporate-action + US/UK coverage.
* **Approval:** **explicit** — provider selection and any spend.
* **Effort:** S (decision) + external (trial).

## Phase 2 — Single-codebase consolidation (foundational blocker #2)
* **Dependencies:** Phase 0 (Phase 1 not strictly required; can run in parallel).
* **Deliverables:** execute `docs/single_codebase_consolidation_plan.md` C0–C8 — one repo, one
  release commit, config-driven IBKR/IG instances, retired `/root/trading-ig`.
* **Tests:** single full suite covering both gateways (incl. `tests/test_ig_broker.py` + the
  deployed hard-disabled tests); shadow dual-gateway reconciliation (C6); post-switch IG health.
* **Stop condition:** stop if the IG dirty-state freeze (C0) is incomplete, if the IG config
  fails the hard-disabled validator, or if any duplicate-position risk appears at C7.
* **Approval:** explicit before C0 (touching the IG tree) and again before C7 (live IG switch).
* **Effort:** M.

## Phase 3 — Canonical registry & universe-state storage
* **Dependencies:** Phase 1 (schema finalized), Phase 2 (one codebase), DB-placement decision.
* **Deliverables:** migrations M1–M4 (`universe.db`: registry, gateway maps, candidate sources,
  state + append-only history); registry populated from `instruments.json`/`instruments_ig.json`
  using **verified** data only (10 IG EPICs; rest `UNRESOLVED`).
* **Tests:** registry load; hard-disabled binding (XAU/XAG remain hard-disabled — reuse deployed
  guard tests); `config remains unchanged by daily evaluation` precondition; history append-only.
* **Stop condition:** no runtime/eligibility logic yet — storage + population only.
* **Approval:** approve schema + DB placement (universe.db vs other).
* **Effort:** M.

## Phase 4 — Forward shadow eligibility evaluator
* **Dependencies:** Phase 3.
* **Deliverables:** daily, idempotent-per-session evaluator (reusing the
  `RegimeClassificationScheduler` pattern, `bot/regime/scheduler.py:84`) computing structural
  eligibility + frozen breakout candidacy + the 8-state machine; **records only** to
  `universe_state`/`universe_state_history`. **No execution changes. No `instruments.json`
  writes.** Gated behind a new `enable_dynamic_universe_shadow` flag (safe-default false,
  `bot/regime/flags.py` convention).
* **Tests:** the §14 state-transition matrix, hysteresis, cooldown, TTL, idempotent rerun,
  restart recovery, multi-timezone sessions, `config unchanged`, XAU/XAG hard-disabled.
* **Stop condition:** must not alter live/paper execution; must not rewrite config.
* **Approval:** approve hysteresis/cooldown numbers (operator-decisions doc).
* **Effort:** M.

## Phase 5 — Broker-neutral Order Intent & global risk (shadow-only)
* **Dependencies:** Phase 4.
* **Deliverables:** Order Intent model (M5) + gateway router + global/per-account risk views;
  emits **hypothetical** intents only (shadow), no submission. Slot ordering ADV→spread→id.
* **Tests:** slot-contention determinism, sector cap, heat/cash limits, duplicate-intent
  prevention, gateway-unavailable reject, no-fallback, IBKR/IG/both routing.
* **Stop condition:** shadow-only; no broker submission; no mirrored execution.
* **Approval:** review risk architecture.
* **Effort:** M.

## Phase 6 — IG reference-data, deal-size translation & preflight
* **Dependencies:** Phase 2 + Phase 5; IG scope decision; possibly IG entitlement resolution.
* **Deliverables:** populate `gateway_map_ig` from **verified** IG reference data; IG deal-size
  translator (not share-qty); deterministic preflight gate. **No real order submission.**
* **Tests:** deal-size translation correctness (mocked), preflight reject paths
  (deterministic, no fallback), NBIS↔`YNDX` re-verification gate.
* **Stop condition:** no live/demo IG order calls until separately approved.
* **Approval:** explicit; IG entitlement + EPIC verification.
* **Effort:** M.

## Phase 7 — Dual-broker reconciliation & idempotency
* **Dependencies:** Phases 5–6; M6.
* **Deliverables:** both-broker startup reconciliation (canonical-id keyed) **before** new
  entries; `unified_positions`; `UNKNOWN_PENDING_RECONCILIATION` handling; pause-on-mismatch.
* **Tests:** restart recovery; IBKR + IG reconciliation mismatch → pause + block duplicate;
  unknown submission state → no auto-resubmit; no duplicate positions.
* **Stop condition:** prove restart + uncertain-order handling before any activation.
* **Approval:** review reconciliation evidence.
* **Effort:** M.

## Phase 8 — Historical-data ingestion
* **Dependencies:** Phase 1 (provider purchased/approved); M7.
* **Deliverables:** idempotent incremental downloader; `raw_bars` (raw+adjusted) +
  `corporate_actions` + point-in-time universe membership + immutable `dataset_manifest`
  (SHA-256 pinned); DQ rules enforced; dual-read window vs `backtest.db` (not dropped).
* **Tests:** DQ1–DQ8 (feasibility doc §5.7); manifest hash reproducibility; survivorship-free
  membership reconstructs correctly; IBKR recent-bar reconciliation agreement.
* **Stop condition:** **the heavy, externally-dependent phase** — do not start without provider
  approval and the Phase-1 trial verification passing.
* **Approval:** explicit; provider spend + ingestion.
* **Effort:** **L**.

## Phase 9 — Dynamic-universe historical validation
* **Dependencies:** Phase 8 (sufficient, corporate-action-correct, survivorship-free data) **and
  a separate pre-registration** of the dynamic-universe strategy hypothesis.
* **Deliverables:** pre-registered OOS validation harness (mirroring the frozen-breakout
  pre-registration discipline: locked params, frozen gates, sealed seeds, hashes).
* **Tests:** harness integrity (no metric/threshold chosen post-hoc); reproducibility from
  `dataset_manifest`.
* **Stop condition:** **only after** data is sufficient AND the strategy is separately
  pre-registered. **Outcome-blind until the pre-registration is sealed.** Does not authorize
  Phase 3c of the frozen-14 work (unrelated, stays PROVISIONAL).
* **Approval:** explicit pre-registration sign-off.
* **Effort:** L.

## Phase 10 — Paper activation
* **Dependencies:** Phases 7 + 9 gates passed.
* **Deliverables:** dynamic universe drives **paper** orders through the router (still no real
  money); both gateways live in paper.
* **Tests:** end-to-end paper: candidate→eligibility→intent→preflight→paper order→reconcile;
  no duplicate/mirrored execution; hard-disabled dominance live.
* **Stop condition:** only after shadow + historical gates pass; **no real-money** (separate
  gate, not in scope).
* **Approval:** explicit paper go-live.
* **Effort:** M.

## Phase 11 — Claude/TTI shadow overlays
* **Dependencies:** Phase 10; deterministic strategy validated.
* **Deliverables:** Claude structured shadow overlay (SUPPORTIVE/CONFLICTED/DISTRIBUTING/
  UNCLEAR) **after** a deterministic candidate exists; TTI candidate ingestion. Overlays may
  only **reduce/block** risk, never create/size/direct/override (DRAFT §16 constraints).
* **Tests:** overlay can never create a trade, change stops, override hard_disabled, or remove
  a position from exit management; shadow-only logging first.
* **Stop condition:** only after the deterministic strategy validates; overlays start shadow.
* **Approval:** explicit.
* **Effort:** M.

---

## Sequencing notes & rationale

* **Phases 1 and 2 are the two approved blockers and come first.** Registry/runtime work
  (Phase 3+) is pointless on a survivorship-biased dataset or across two un-synced trees.
* Phases 3–7 are **additive and shadow-only** — they build the control plane and prove
  reconciliation without touching live execution, reusing existing infra (scheduler, flags,
  pause registry, shadow stack).
* Phase 8 is the **long pole** (external provider, large ingestion) and gates Phase 9.
* Real-money is **not** in this roadmap — it is a separate, later, explicitly-gated decision
  beyond Phase 10, consistent with the frozen-breakout discipline.

Operator approval is required before **every** phase transition. No deviation from this
sequence without justified, documented repository evidence.
