# Claude Code Handoff Prompt: claude-strategy branch (Spec v3)

Paste the entire block below into Claude Code at the root of the `tradingbot` repository. Claude Code will read it, then read the spec, and propose a PR plan before writing code.

---

## INSTRUCTIONS FOR CLAUDE CODE

You are implementing the spec at `specs/CLAUDE_STRATEGY_SPEC_v3.md` on a new branch called `claude-strategy` (branched from `main`).

This is a long-running, multi-PR implementation. **Do not start writing production code yet.** Your first job is to confirm understanding and propose a PR plan. I will approve the plan, then you build PR-by-PR.

---

### STEP 1 — Read these sections of the spec, in this order

1. §0 How to Read This Spec
2. §1 Objective and Non-goals
3. §2 Architecture Overview
4. §5 Overall Design Rules (all 13 rules)
5. §6 Feature Flags (especially §6.2 semantics and §6.3 dependency graph)
6. §7 Core Dataclasses
7. §16 Testing Requirements (the full section — testing strategy is non-negotiable)
8. §19 Deliverables Checklist
9. §20 Open Questions (all answered; included below for reference)

Read the rest of the spec on demand as you reach each section during implementation. Do NOT try to read the whole 1,665-line spec in one go before starting; you'll lose the plot.

---

### STEP 2 — Decisions already made (do not relitigate)

The spec is v3 after two review rounds. The following decisions are final:

**Architecture:**
- Main loop stays in `bot/layer1.py`. Orchestrator extraction is tech debt for a future PR — log it in `docs/TECH_DEBT.md`.
- Match `bot/llm/sentiment.py`'s tool-use pattern for structured JSON in the new classifier. Do not invent a new pattern.
- Earnings calendar: Finnhub is the bulk source, with manual override/addition semantics per §15.4.3.
- Macro calendar: manually maintained in SQLite, edited via the §15.4 web UI, freshness monitored per §10.7.
- Low-liquidity: compute the 20-day median volume per instrument per time-of-day bucket from OHLCV. Cache in DB, refresh nightly.
- IBKR and IG broker `fill_id`: use broker-supplied execution IDs when available, fall back to `f"{position_id}-{seq:04d}"` for either broker.
- Overlay hard-failure recovery: CLI command `bot recover-overlay <name>` (see §13.6).
- Calendar storage: your choice between SQLite-only and SQLite+JSON-cache. Default to **SQLite-only** unless profiling proves overlay reads need cache acceleration. Note the decision in `docs/calendar_ui.md`.
- Dashboard auth: reuse the existing JWT middleware that nginx applies to the dashboard on port 8082.
- Telegram alerts: reuse the existing `CogniflowAI_Trading_Alerts_Bot` for all alert types.

**Numerical defaults:**
- Confidence thresholds: 0.85 (same-regime persistence), 0.70 (new-regime pending), 0.75 (TRENDING→RANGING hysteresis).
- `max_daily_cost_usd = 5.0` for the classifier.
- Macro alert thresholds: `LOW_THRESHOLD_DAYS = 30`, `CRITICAL_THRESHOLD_DAYS = 14`.
- Coverage target for new code: 85% line coverage.

**Earnings override resolution order:**
`manual_override` > `manual_addition` > `finnhub`. Manual overrides win over Finnhub; manual additions cover tickers Finnhub doesn't have. See §15.4.3 for stale-override detection.

**Pricing (critical):**
Do NOT hardcode `claude-sonnet-4-6` input/output token prices from your training data. Look them up at implementation time from `https://docs.claude.com`. Note the source URL and the date you checked in a comment in `bot/regime/cost_tracker.py`.

---

### STEP 3 — Non-negotiable rules

These rules will catch you if you slip. They're explicit because previous design iterations got them wrong:

1. **All flags default to off or shadow.** Missing config keys never enable live behaviour. Config loader emits a warning for missing flags, never silently defaults to live.

2. **Overlay hard-failure pauses dependent instruments. It does NOT auto-disable the overlay flag.** This is the v2 review fix in §13.3. Reverting to flag-disable behaviour would weaken protection exactly when things are broken. The `InstrumentPauseRegistry` is the correct mechanism.

3. **Main loop has THREE independent entry gates**, not one (see §14):
   - `instrument_pause_registry.is_paused(instrument)` — always checked first
   - `enable_event_overlays_live AND overlays_present` — overlays can bite without router live
   - `enable_router_live` — router-driven blocks (regime UNCLEAR etc.)

   `test_overlay_independence.py` enforces the §14.2 truth table. If your implementation can't pass that test, the gates are wired wrong.

4. **`test_no_state_contamination.py` is CI-critical.** Shadow code paths must NEVER write to live tables. Live code paths must NEVER read from `shadow_*` tables.

5. **Exits are never blocked by overlays.** Overlays gate entries only. The asymmetry rule is in §10.5 and `test_overlay_never_blocks_exits.py` enforces it.

6. **Routing invariant**: `selected_engine == "NoOpEngine"` always implies `allow_new_entries == False`. Pinned by `test_routing_noop_never_allows.py`.

7. **Entry-regime exit contract**: a position's exit logic uses the engine that entered it, not the currently routed engine for that instrument. Pinned by `test_entry_regime_exit_contract.py`.

8. **No mixed-strategy fills**: a position with fills tagged by different engines must raise on exit. Pinned by `test_mixed_strategy_fills_raises.py`.

9. **The existing 486 tests must still pass** when shadow-only mode is configured. Behavioural equivalence on the legacy path is non-negotiable — `TripleConfirmationEngine` is a wrapper, not a rewrite.

10. **Every behavioural rule in the spec has a pinning test in `tests/invariants/` or `tests/regression/`.** §16.2 and §16.6 list them.

---

### STEP 4 — Proposed PR plan (review and propose changes)

The spec is too large for one PR. I propose splitting into 4 PRs, each independently reviewable and shippable to the `claude-strategy` branch:

**PR 1 — Foundations** (§7, §8, §9, parts of §16)
- All four core dataclasses
- `StrategyEngine` ABC with `TripleConfirmationEngine` wrapper, `MeanReversionEngine` skeleton, `NoOpEngine`, registry
- Full regime layer: features, classifier (matching sentiment.py tool-use), prompt V1, schema, cache, cost tracker, smoothing, router with §9.8 unambiguous table
- Unit tests for everything above
- Invariant tests for routing (§16.2)
- Golden-file tests for classifier (mocked Claude), smoothing, router (§16.3)
- Regression tests for v2 fixes touching these modules
- Coverage check passes on new code
- All 486 existing tests still pass

**PR 2 — Overlays, Degradation, and Macro Alerts** (§10, §13, parts of §16)
- All four overlays with deterministic logic
- Earnings: Finnhub integration with override/addition semantics (read-only against `manual_*` rows at this PR; UI lands in PR 4)
- Macro: reads from SQLite (schema lands in this PR; UI lands in PR 4)
- Low-liquidity: compute 20-day median bucketed by time-of-day
- Data quality short-circuit
- Overlay precedence and registry
- Instrument dependency map (`bot/overlays/instrument_dependencies.py`)
- Macro calendar monitor (§10.7) with weekly/monthly/startup checks + systemd timer or cron entries
- Degradation framework with v2 overlay-pause semantics
- `InstrumentPauseRegistry` with DB persistence across restarts
- `bot recover-overlay <name>` CLI with self-test
- Unit + invariant + regression tests for these modules
- Integration test `test_overlay_independence.py` (placeholder if main loop integration is in PR 4 — wire fully when main loop is done)

**PR 3 — Shadow and Position Metadata** (§11, §12, parts of §16)
- ShadowPositionSimulator with hypothetical trade lifecycle
- Counterfactual logger with `shadow_decisions` and `shadow_hypothetical_trades` schemas
- Async shadow execution with bounded queue
- Position metadata tagger with composite `(position_id, fill_id)` key supporting partial fills
- Exit policy enforcement via `get_exit_engine`
- `test_no_state_contamination.py` — CI-critical test
- Golden-file test for shadow simulator
- Regression tests for partial-fill composite key, exit contract, mixed-strategy fills

**PR 4 — Main Loop Integration, Flags, Observability, Calendar UI** (§6, §14, §15, parts of §16)
- Flags module with dependency graph enforcement and startup logging (§6.4 format)
- Main loop integration in `bot/layer1.py` with v2 decoupled entry gates (§14)
- Dashboard tabs: regime, overlays, routing, shadow vs live, degradation, instrument pauses
- Telegram alerts for all hard-degradation events and daily summary
- Logs per §15.3
- Calendar UI module `bot/calendar_ui/` with routes, store, audit, validators
- SQLite schemas for `macro_calendar`, `earnings_calendar`, `calendar_edit_audit`
- HTTP API endpoints per §15.4.4
- Confirmation header enforcement per §15.4.6
- CSV import with transaction rollback per §15.4.7
- Static HTML/CSS/JS frontend (vanilla, no SPA framework)
- Finnhub earnings sync nightly job
- Audit log UI tab
- Integration tests: `test_full_shadow_pipeline.py`, `test_flag_combinations.py`, `test_overlay_independence.py` (full), `test_calendar_ui_end_to_end.py`
- API tests: `test_routes_macro.py`, `test_routes_earnings.py`, `test_routes_csv_import.py`
- All remaining regression tests
- Pre-commit hooks + CI pipeline configuration
- Nightly regression replay scaffolding (gated, not enabled)
- Documentation: `docs/regime_strategy.md`, `docs/shadow_mode.md`, `docs/degradation.md`, `docs/calendar_ui.md`, `docs/TECH_DEBT.md`
- CLAUDE.md updated with the two new rules (see §19)

If you see a better PR split, propose it before starting. Some refinements I'd consider acceptable:
- Splitting PR 4 into "main loop + flags + observability" and "calendar UI" if PR 4 grows too large
- Pulling the Calendar UI HTTP API into PR 2 alongside earnings/macro reading code (so the UI ships earlier and macro maintenance can begin before main loop integration is done)
- Moving the macro calendar monitor (§10.7) earlier if you can land it without the UI

What's NOT acceptable:
- Merging PR 1 without the regression tests for v2 fixes that touch those modules
- Skipping `test_no_state_contamination.py` to ship PR 3 faster
- Hardcoding Anthropic pricing from training data
- Implementing the classifier without matching `sentiment.py`'s tool-use pattern

---

### STEP 5 — What I want from you before any code is written

Reply with:

1. **Your understanding of the spec in 5-10 sentences.** Especially the relationship between regime classification (Claude), smoothing (deterministic), overlays (deterministic), router (deterministic), and the three independent entry gates in the main loop. If you describe Claude as making per-trade BUY/SELL decisions, you've misread the spec.

2. **Confirmation of the PR split** or a counter-proposal with rationale.

3. **Any clarifying questions on parts of the spec you find ambiguous.** Do not guess on:
   - §13 degradation semantics (especially overlay hard-failure)
   - §14 entry gate logic and the §14.2 truth table
   - §12.1 partial-fill composite key
   - §15.4.3 earnings override resolution order
   - §6.3 flag dependency graph
   - §16 testing structure (which test files go where)

   If anything in those sections is unclear, ask before building.

4. **An estimate of effort per PR** in working sessions, not calendar time. This helps me plan reviews.

Once I approve your plan, you start PR 1.

---

### STEP 6 — During implementation (rules of engagement)

- **Branch**: all work on `claude-strategy`, branched from `main`. Each PR is a sub-branch (`claude-strategy/pr1-foundations`, `claude-strategy/pr2-overlays`, etc.) merging into `claude-strategy`.

- **PR titles**: prefix all with `claude-strategy: ` followed by a concise description.

- **Commit granularity**: one commit per spec subsection where practical. Commit messages reference the spec section number.

- **Tests run before commit**: pre-commit hook runs unit tests for changed files. CI runs the full suite. Use `--no-verify` only when actively debugging the hook itself, not to bypass failing tests.

- **No silent fallbacks**: every degradation, fallback, and error path emits a log line. Critical events emit a Telegram alert.

- **Versioning**: prompts get versioned strings (e.g. `CLASSIFIER_PROMPT_V1`). Every classifier decision logs the prompt version, model version, and input hash.

- **Tech debt log**: any compromise you make during implementation goes in `docs/TECH_DEBT.md` with rationale. Don't hide compromises in comments only.

- **Open question protocol**: if you hit something the spec doesn't cover, prefer asking over guessing. The cost of a 5-minute clarification is much less than the cost of building something and rebuilding it.

---

### STEP 7 — Acceptance criteria for the overall claude-strategy branch (before merging to main)

The branch is mergeable when:

1. All 486 existing tests pass
2. New tests bring total to whatever you implement; coverage on new code ≥ 85%
3. `test_no_state_contamination.py` passes
4. All invariant tests in `tests/invariants/` pass
5. All regression tests in `tests/regression/` pass (one per v2 review fix)
6. Full pipeline runs for 24 hours on paper account (IBKR DUQ141950) in full shadow mode without any hard-degradation events
7. Shadow logs show classifications, routings, and at least one hypothetical trade opened and closed
8. Calendar UI passes manual smoke test (create / edit / delete / CSV import)
9. `bot recover-overlay <name>` CLI works on a simulated overlay failure
10. `docs/TECH_DEBT.md` exists and is up to date

The promotion gate (flipping live flags) is separate and happens after 30 days of shadow data per §17.

---

### ANSWERED OPEN QUESTIONS (from §20, for your reference)

1. `sentiment.py` uses tool-use for structured JSON. Match this pattern.
2. Main loop in `bot/layer1.py`. Orchestrator extraction logged in `docs/TECH_DEBT.md` for future PR.
3. Confidence thresholds: 0.85 / 0.70 / 0.75 confirmed.
4. Earnings calendar: Finnhub with override/addition semantics.
5. Macro calendar: manual via §15.4 UI, freshness monitored per §10.7.
6. Low-liquidity: compute 20-day median per instrument per time-of-day bucket.
7. `max_daily_cost_usd = 5.0`.
8. Dashboard auth: existing JWT via nginx 8082.
9. CLAUDE.md additions: layer1.py wrapping rule + Anthropic pricing lookup rule.
10. Telegram channel: reuse `CogniflowAI_Trading_Alerts_Bot`.
11. Broker fill IDs: use IBKR/IG execution IDs, fall back to `position_id-seq` for either broker.
12. Overlay recovery: CLI `bot recover-overlay <name>`.
13. Earnings UI editability: both overrides and additions with stale-override detection.
14. Calendar storage: SQLite-only preferred unless profiling shows otherwise.

---

Begin with STEP 5 reply only. Do not write code yet.
