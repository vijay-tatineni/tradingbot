# Tech Debt Log

Known compromises made during `claude-strategy` implementation, with rationale.

---

## Main loop orchestration in bot/layer1.py

**Status:** Deferred
**Spec:** §3.1

Orchestration and signal logic are co-located in `bot/layer1.py`. The right
structure is `bot/orchestrator.py` for orchestration and `bot/layer1.py` for
signal logic only. This refactor is deferred to keep PR scope manageable.
Plan to address in a follow-up PR after `claude-strategy` ships and stabilises.

## TripleConfirmationEngine wrapping decisions

**Status:** Active (PR1)
**Spec:** §8.1

The `TripleConfirmationEngine` wraps `SignalEngine.evaluate()` for candidate
generation. The existing `SignalEngine` in `bot/signals.py` is a clean,
stateless, pure-function engine — it was the natural seam.

Exit management (`manage_exit`) returns HOLD and defers to the legacy exit
path in `bot/layer1.py` (two-tier stop system in `PositionTracker`). The
legacy path remains authoritative until `enable_position_tagged_exit_policy`
is flipped live. Extracting the exit logic into `TripleConfirmationEngine`
would require decoupling the `PositionTracker` state machine, which is too
invasive for this PR.

## bot/llm/sentiment.py uses regex parsing for structured output

**Status:** Deferred
**Spec:** Q1 answer

`bot/llm/sentiment.py` uses regex parsing for structured LLM output.
Consider migrating to tool-use following the pattern established in
`bot/regime/classifier.py`. The classifier demonstrates the correct
approach: Anthropic API tool-use with Pydantic schema validation.

## Feature flags module location

**Status:** Active (PR1)

The spec directory layout shows `bot/config/flags.py`, but `bot/config.py`
already exists as a module file (not a package). Creating `bot/config/`
as a package would shadow the existing `bot/config.py` import and break
all existing code. Flags are placed at `bot/regime/flags.py` instead.
Consider renaming `bot/config.py` → `bot/config/main.py` in a future
cleanup PR to enable `bot/config/flags.py` per the spec layout.

## Model ID: claude-sonnet-4-20250514 → claude-sonnet-4-6

**Status:** Active (PR1)

The spec references `claude-sonnet-4-20250514` which is deprecated
(retiring 2026-06-15). Updated to `claude-sonnet-4-6` per Anthropic
documentation checked 2026-05-18. Pricing confirmed: $3/MTok input,
$15/MTok output.

## Earnings overlay deferred

**Status:** Deferred — revisit after 60 days of shadow data
**Spec:** §10.2, §3.1

The earnings overlay (originally specified in §10.2 of v3) is not being
built in this work. No evidence yet that earnings gaps have hurt the bot's
actual trading; building protection against an unconfirmed problem carries
ongoing maintenance cost for no measured benefit.

**Revisit criteria:** After 60 days of shadow data collected post-merge,
query `shadow_decisions` for entries on individual stocks (non-ETF
instruments) within 1-2 trading days of known earnings dates. If 3+ such
entries are recorded in the 60-day window, build the earnings overlay as a
follow-up PR. Use manual calendar entry pattern (like macro), no external
data source needed for the scale of ~15 instruments × ~4 earnings/year.
If shadow data shows zero such entries, no further action needed.

## /root/trading-ig/ is a stale full copy

**Status:** Noted (PR4)

`/root/trading-ig/` is a full copy of the codebase from April 16-17, used
by the IG broker systemd service (`ExecStart=/usr/bin/python3 /root/trading-ig/main.py --broker ig`).
It has an identical `main.py` but is missing all regime modules (degradation,
overlays, regime, shadow, strategies). It needs to be either:
- Converted to use `/root/trading/` as the single source (symlink or shared install), or
- Manually synced after each release.

The regime orchestrator wiring in `/root/trading/main.py` does NOT
automatically apply to `/root/trading-ig/`. The IG instance will continue
running without regime integration until this is addressed.

## /root/trading/trading_v6/ is dead code

**Status:** Noted (PR4)

`trading_v6/` contains an old v6 version of the bot. It's not referenced
by any systemd service or startup script. Safe to delete in a cleanup PR.

## Pre-existing test-ordering issue: test_ig_broker.py

**Status:** Pre-existing, not caused by claude-strategy

`tests/test_ig_broker.py::test_create_broker_ig_without_credentials` fails
when run as part of the full suite but passes in isolation. This is a
test-ordering state pollution issue that pre-dates this branch. 486 of 487
existing tests pass; this one failure is not a regression.

## Per-bot macro calendars not shared

**Status:** Open question (PR4)

IBKR and IG each have their own `regime.db` with their own `macro_events`
table. Macro events entered via IBKR's calendar UI are not visible to IG's
`MACRO_LOCKOUT` overlay. Three resolution paths exist:

1. Enable calendar UI on IG too (manual duplication, currently disabled).
2. Disable macro overlay on IG (accept no macro protection).
3. Share the macro calendar DB across both bots (architectural change for
   PR 6 or later).

Currently neither bot has macro events populated and
`enable_event_overlays_live` is false on both, so this question doesn't
bite yet. Revisit before promoting `enable_event_overlays_live=true` on
either bot.

## IG sync 2026-05-19 (one-time bridge)

**Status:** Noted (PR4)

`/root/trading-ig/` was rsync'd from `/root/trading/` covering `bot/`,
`tests/`, `main.py`, `api_server.py`, `CLAUDE.md`, and `specs/prompts/`.
Plus an explicit `feature_flags` block was added to
`/root/trading-ig/instruments_ig.json` matching `SAFE_DEFAULTS`.

The two codebases are now identical except for:
- Instance config: `instruments_ig.json`, `.env`, `TASK_SPEC_IG_INSTANCE.md`
- Runtime state: `*.db`, `*.log`

This is a one-time bridge. The codebases will drift again on the next
change to `/root/trading/`. PR 6 should consolidate to a single source
using `main.py`'s existing `--config` and `--broker` flags from commit
`7a9dd06`.

## IBKR config missing explicit feature_flags block

**Status:** Open (PR4)

`/root/trading/instruments.json` has no explicit `feature_flags` block;
the IBKR bot runs on `FeatureFlags.SAFE_DEFAULTS`. Add an explicit block
matching its current implicit state before any live flag promotion. Same
diff as applied to the IG side on 2026-05-19.

## IG demo data-permission errors are pre-existing

**Status:** Pre-existing, not caused by PR4

`bot_stderr.log` shows ~398 historical instances of
`'NoneType' object has no attribute 'fetch_market_by_epic'` plus ~20 per
restart cycle. The IG demo account lacks a data subscription for certain
UK epics (e.g. `KA.D.BARC.CASH.IP`); other instruments fail epic
verification at startup. The bot operates cleanly otherwise and trades
on permitted instruments only.

Not a regime/sync issue. A separate decision is needed on whether to
upgrade the IG market-data subscription or prune the instrument universe.

## Sync-target test redundancy

**Status:** Noted (PR4)

`tests/regime/test_classifier.py::TestPromptSync::test_prompt_sync` reads
from `specs/prompts/classifier_v1.md` to verify the in-code prompt matches
its documented mirror. The test is structurally meaningless on a
sync-target codebase like `/root/trading-ig/` — if the source-of-truth
side passes, the target side passes automatically via rsync.

Two fixes possible:
- (a) Add `pytest.skip("Sync test only meaningful on source-of-truth side")`
  when `specs/prompts/classifier_v1.md` is absent.
- (b) Eliminate IG-side test runs entirely once PR 6 consolidates codebases.

The 2026-05-19 sync also surfaced that the inventory step missed `specs/`
entirely — PR 6 should explicitly enumerate which top-level directories
are needed for runtime vs documentation vs source-of-truth.

## Dashboard rendering uses dual source of truth

**Status:** Noted (PR6)

`bot/dashboard.py:_write_html()` is an f-string template that regenerates
`web/dashboard.html` every time `Dashboard.__init__` runs — on bot
startup and on every test that instantiates `Dashboard(cfg)`. The
committed `web/dashboard.html` is therefore a build artifact, not an
authoritative source. Manual edits to `web/dashboard.html` survive only
until the next regeneration.

This bit PR 6: hours were lost discovering that
`tests/test_disabled_instruments.py::test_disabled_shown_on_dashboard`
was silently overwriting in-progress edits to `web/dashboard.html` by
instantiating `Dashboard(cfg)` against the real config.

PR 6's regime tabs are embedded directly in the f-string template
alongside the existing Layer 1 / P&L sections. The f-string escaping
(`{{` `}}` for literal braces, `${{var}}` for JS template literals) is
fragile — `tests/regime/test_dashboard_template.py::TestFStringEscaping`
guards against regressions but a future refactor should eliminate the
approach.

Future work: extract the template to a separate file (Jinja2 or read
raw `web/dashboard.template.html`), have `_write_html()` substitute
runtime fields by name. Then `web/dashboard.html` becomes a true cached
copy of an authoritative template, and the template can be edited
directly without `{{` escaping.

## IG sync 2026-05-19 (PR 6 dashboard tabs)

**Status:** Noted (PR6)

`/root/trading-ig/` was rsync'd from `/root/trading/` to pick up the
PR 6 regime dashboard tabs:

- `bot/regime/dashboard_data.py`
- `api_server.py` (six new JWT endpoints + `init_overlay_registry`
  at module import)
- `bot/dashboard.py` (f-string template with regime CSS / HTML / JS)
- `web/dashboard.html` (regenerated build artifact)
- `tests/regime/test_dashboard_data.py`
- `tests/regime/test_dashboard_endpoints.py`
- `tests/regime/test_dashboard_template.py`

After rsync: `cogniflowai-ig-api.service` and `cogniflowai-ig-bot.service`
both restarted; 67 dashboard tests pass on IG side; all six endpoints
return 401 unauthenticated at both ports 8084 (api direct) and 8083
(nginx). IG bot cycle #1 completed cleanly in shadow mode.

The two codebases now have identical regime dashboards. They will drift
again on the next change to `/root/trading/`. PR 7 should consolidate
to a single source using `main.py`'s `--config` / `--broker` flags from
commit `7a9dd06`, eliminating the rsync ritual entirely.

## IG sync 2026-05-19 (Classify tab)

**Status:** Noted (PR7)

`/root/trading-ig/` was rsync'd from `/root/trading/` to pick up the
Classify tab (manual operator-triggered regime classification):

- `api_server.py` (three new JWT endpoints: `/api/regime/budget`,
  `/api/regime/instruments`, `/api/regime/classify`, plus
  `_cached_features` helper, in-memory rate limiter, process-wide
  classify lock)
- `bot/regime/classifier.py` (`classify()` gained `force=False` param)
- `bot/dashboard.py` (new Classify tab CSS, HTML pane, modal scaffold,
  ~190 lines of operator-side JS)
- `web/dashboard.html` (regenerated build artifact — see also the
  "Dashboard rendering uses dual source of truth" entry above)
- `tests/regime/test_classify_endpoints.py` (new, 16 tests)
- `tests/regime/test_dashboard_template.py` (updated to assert the
  new tab scaffold, JS function names, and copy strings)

After rsync: `cogniflowai-ig-api.service` and `cogniflowai-ig-bot.service`
both restarted; 78 dashboard/classify tests pass on IG side; all three
new endpoints return 401 unauthenticated at port 8083; IG bot
re-registered RegimeOrchestrator and started Cycle #1 cleanly in
shadow mode at 22:14 UTC.

Architecture caveat: the api_server does not hold a broker connection.
Manual classify reads features from the most recent
`regime_classification_cache` row per instrument and re-rolls Claude.
For an instrument the daily scheduler has never reached (truly new
add) the endpoint returns 409 with operator-facing guidance to wait
for the next post-close window. See the commit message of
`bd40f24` for the design rationale.

## IG sync 2026-05-20 (Regime tab dedup + smoothed join fix)

**Status:** Noted (PR7)

`/root/trading-ig/` was rsync'd from `/root/trading/` to pick up the
Regime dashboard tab bug fixes:

- `bot/regime/dashboard_data.py` — `get_regime_states()` now dedups to
  the latest cache row per instrument (window function) and `LEFT JOIN`s
  `smoothed_regime_state` to populate `smoothed_regime` / `days_in_regime`.
  The prior code returned one row per `(instrument, trading_date)` and
  joined the empty `shadow_decisions` table, so the tab showed 28 rows
  with blank smoothed columns.
- `tests/regime/test_dashboard_data.py` and
  `tests/regime/test_dashboard_endpoints.py` — added the
  `smoothed_regime_state` table to the schema fixtures plus an
  `_insert_smoothed()` helper, and migrated the smoothing assertions
  off `shadow_decisions`.

After rsync: both api services restarted; both `/api/regime/states`
endpoints return 401 unauthenticated; IBKR returns 14 rows with smoothed
populated; IG returns an empty array (cache still empty due to the
entitlement issue documented in this file — see "IG demo account lacks
cash-equity historical-data entitlement").

The two codebases drift again on the next change. The PR 7 consolidation
(`7a9dd06`) that eliminates the rsync ritual is still pending.

## IG sync 2026-05-22 (classifier rationale clip)

**Status:** Noted (PR7)

`/root/trading-ig/` was rsync'd from `/root/trading/` to pick up the
classifier rationale-length fix (commit `eab27c3`):

- `bot/regime/classifier.py`, `bot/regime/classifier_schema.py`,
  `bot/regime/classifier_prompt.py` — rationale cap raised 280 → 350 and
  an over-long rationale is now clipped (visible ellipsis) instead of
  failing the whole response into an UNCLEAR/0.0 fallback.
- `specs/prompts/classifier_v1.md`, `specs/CLAUDE_STRATEGY_SPEC_v3.md`,
  `tests/regime/test_classifier.py` — prompt mirror, spec §9.2/§9.7, and
  the new truncation tests.

After rsync: both bot + api services restarted on each side; IG-side
classifier tests pass (12); IG `/api/regime/classify` returns 401
unauthenticated. The fix could not be exercised end-to-end on IG because
its `regime_classification_cache` is empty (the entitlement issue above),
so `_classify_one_locked` returns 409 there — verified instead on IBKR,
where AVGO/SCCO/TSM re-classified to confidence 0.62 (real calls, no
schema-validation fallbacks).

## Classifier cache-hit rate not surfaced

**Status:** Open (PR6)

The `cache_hit` column was dropped from the Regime dashboard tab during
PR 6 design because it's a per-call runtime metric, not a per-state
attribute persisted in `regime_classification_cache`. The classifier's
cache-hit rate is operationally useful (it controls daily Anthropic API
spend) but isn't currently exposed in the dashboard.

Future work: add a `regime_cache_metrics` table or extend
`regime_classification_log` to track hit/miss per call, then surface
aggregate cache-hit % and daily cost on a "Classifier ops" panel.
Defer until shadow data reveals it's needed.

## Overlays dashboard tab limited to MACRO_LOCKOUT

**Status:** Open (PR6)

The `/api/overlays/active` endpoint calls
`bot.overlays.registry.active_overlays()` from the API server process.
This works for `MACRO_LOCKOUT` (reads from `macro_events` via the DB)
but NOT for `DATA_QUALITY` or `LOW_LIQUIDITY`, which require market
data ctx that only exists in the trading bot's process memory.

The Overlays tab will show `MACRO_LOCKOUT` events when active.
`DATA_QUALITY` and `LOW_LIQUIDITY` pauses surface via the Pauses tab
if they hard-fail and trigger instrument pauses.

Future work: have the trading bot write an `active_overlays_snapshot`
table on each cycle, dashboard reads from it. Defer until shadow phase
shows whether the limitation is operationally painful.

## Anthropic API pricing requires periodic re-verification

**Status:** Recurring task

`bot/regime/cost_tracker.py` hardcodes pricing for `claude-sonnet-4-6`
confirmed 2026-05-18: $3/MTok input, $15/MTok output. Pricing changes
occasionally; the existing constants will silently produce wrong cost
numbers if Anthropic adjusts rates.

Re-verify against https://docs.claude.com on each significant
maintenance window (quarterly at minimum). The `cost_tracker` comment
notes the verification date; update it whenever pricing is re-checked.

## PR 7: codebase consolidation

**Status:** Planned

`/root/trading/` and `/root/trading-ig/` are two copies of the codebase
kept in sync via manual rsync. This has bitten the project twice during
PR 6 (PR 4 sync gaps, PR 6 sync gaps), and will continue to bite on
every change touching `bot/`, `main.py`, or `api_server.py`.

PR 7 should consolidate to a single codebase using `main.py`'s existing
`--config` / `--broker` flags from commit `7a9dd06`:

- `/root/trading-ig/` becomes a thin directory holding only
  `instruments_ig.json`, `.env`, `TASK_SPEC_IG_INSTANCE.md`, and
  runtime state (DBs, logs).
- IG systemd service updates to
  `ExecStart=/usr/bin/python3 /root/trading/main.py --broker ig --config /root/trading-ig/instruments_ig.json --data-dir /root/trading-ig/data`.
- `main.py` extended to accept `--data-dir` so DBs and logs go to
  instance-specific locations.
- Test plan: run both services side-by-side from the consolidated
  codebase, confirm no shared-state issues.

Estimated 4-6 hours of focused work. Worth doing once shadow phase is
underway and PR 6 has stabilised.

## Mean-reversion engine is a skeleton only

**Status:** Deferred

`bot/strategies/mean_reversion.py` exists as a skeleton from PR 1 to
satisfy the `StrategyEngine` ABC. It is NOT a functional strategy. The
router will dispatch `RANGING` regimes to `MeanReversionEngine` when
`enable_mean_reversion_live=true`, but with no real implementation
behind it, those entries would be no-ops.

Validation and full implementation of mean-reversion logic (entry
signal, position sizing, exit conditions, parameter tuning) is its own
substantial research and development effort. Keep
`enable_mean_reversion_live=false` and
`enable_mean_reversion_shadow=true` only until a real implementation
lands.

## Integration-gap pattern (operational note)

**Status:** Mitigated, not eliminated

Across PRs 1-6, six "tests pass but production integration missing"
bugs were caught in review: IG broker test ordering (PR 1),
orchestrator not wired into `main.py` (PR 4), overlay ctx bridge
missing (PR 5), nginx route for `calendar.html` (PR 5),
`specs/prompts/` missing in IG sync (post-PR 5), `api_server.service`
not restarted after code changes (PR 6).

The pattern is consistent: code is written correctly, unit tests pass,
but the running production system doesn't actually use the new code
because some glue (service restart, file copy, route registration,
runtime initialisation) is missing.

Mitigation added in PR 6: deploy protocol in `CLAUDE.md` requires
service restart + curl verification after any code change affecting a
long-running service. This catches the most common subset
(service-restart gaps) but not all (runtime initialisation gaps,
missing static files, route registration).

Going forward, the discipline that worked across PRs 1-6 should
continue: after each PR, run `grep -rn` for the new component's name
in production code, run end-to-end curl/browser verification, not just
test-suite green.

## IG demo account lacks cash-equity historical-data entitlement

**Status:** Blocking IG-side regime pipeline. Pre-existing IG account limitation.

The IG demo account at `/root/trading-ig/` rejects historical-bar
requests on `*.CASH.IP` epics with
`unauthorised.access.to.equity.exception`. This affects all 10
configured instruments (BARC, ANTO, SU, YNDX, MSFT, AAPL, PLTRUS,
AVGO, SGLNLN, SSLNLN).

Visible consequence: the regime classification scheduler runs every
cycle on IG but `broker.fetch_bars()` returns `None` for every
instrument, so `regime_classification_cache` stays empty. The Classify
tab loads but every classify attempt returns 409.

Less visible consequence: IG layer1 trading is also non-functional for
the same reason — no bars means no signal evaluation. This has been
silently true since well before the regime work; this restart just made
it visible via the scheduler's per-instrument error logs.

Three resolution paths:

1. **Fix IG account entitlements** — enable cash-equity historical
   data on the demo account via IG support. The right fix; unblocks
   both layer1 and the regime pipeline. External dependency.
2. **Switch IG to IG-supported instrument types** — indices (FTSE100,
   S&P 500 CFD), forex pairs, or commodity CFDs that the demo tier
   supports. Small config edit to `instruments_ig.json`. Requires
   re-backtesting on different instruments.
3. **Accept IG as dashboard-only** — IBKR is the trading bot; IG runs
   but doesn't trade and doesn't classify. The Classify tab and
   dashboard work but produce no data.

Currently operating in mode 3 by default. Revisit when IG side becomes
operationally important.

## Classifier rationale clips to 350 chars on most calls

**Status:** Accepted, not blocking.

Despite the prompt's "max 350 characters, prefer 250" guidance, Claude
generates rationales at or near the 350-char cap for most instruments.
The clip-on-overflow safeguard (commit `eab27c3`) preserves the
classification (regime + confidence) — only the explanation text gets
cut. The full rationale is lost.

Resolution paths if this becomes operationally painful:

1. Tighten the prompt with explicit sentence-count or word-count limits,
   then iterate.
2. Raise the cap further (currently 350; tightening was the original goal
   so this would reverse progress).
3. Accept and leave alone — the rationale field is for human inspection
   during shadow phase, not for downstream consumption. The
   classification itself is what matters.

Currently operating in accept-and-leave-alone mode. Revisit only if
rationales prove inadequate for shadow-phase analysis.

## Instruments Editor: Add/Delete restored (broker-aware)

**Status:** Done (claude-strategy)

The "Add new instrument" form and per-row delete button were dropped from
`web/instruments.html` in the v8.0 rewrite (`1c018d0`, 2026-04-06) —
removed together. Both are restored here, ported onto the current v8.0
markup/CSS (the `global-panel`/grid styling) as an Add panel plus a
per-card 🗑 delete button.

Key facts for future maintainers:
- `web/instruments.html` is a hand-edited static file served directly by
  nginx (`root /root/trading/web`, port 8082; IG: `/root/trading-ig/web`,
  port 8083). Unlike `web/dashboard.html` it is NOT generated by
  `bot/dashboard.py`, so direct edits are safe and persist.
- Add and Delete persist via the full-array replace endpoint
  `POST /api/instruments/layer1`, NOT the per-symbol
  `POST /api/instruments/update` — the latter rejects unknown symbols
  (update-only, cannot create). Both first `GET /api/instruments` to read
  the clean on-disk array so the WF-enriched fields injected by
  `/wf-recommendations` (`resolved_indicators`, `wf_*`) are never written
  back. No backend changes were needed.
- Broker-aware: `applyBrokerToAddForm()` reads `settings.broker` (returned
  in the `/api/instruments` and `/wf-recommendations` responses) and
  renders IBKR fields (`sec_type`, `loss_limit`, `notes`) or IG fields
  (`ig_epic` required, `target_notional` required). Common fields: symbol,
  name, flag, currency, exchange, qty, long_only, trail/take-profit/
  emergency stops, timeframe. `exchange` is common (SMART for IBKR, LSE
  etc. for IG), not IBKR-only. `target_notional` is required (not
  defaulted) on IG so position sizing is an explicit operator decision.
  This keeps a single source file that rsyncs verbatim to both instances
  and stays correct on each.
- Static file → no service restart needed on either side; nginx serves it
  directly (browser hard-refresh only).

## IG sync 2026-05-25 (instruments Add/Delete)

**Status:** Noted (PR7)

`/root/trading-ig/web/instruments.html` was rsync'd from `/root/trading/`
to pick up the restored broker-aware Add/Delete UI (single source file —
the broker-aware logic above means the same file is correct on both
instances). Verified on IG (port 8083, `broker=ig`): a TEST instrument
with a fake epic `KA.D.TEST.CASH.IP` + `target_notional` persisted to
`instruments_ig.json` with the correct IG schema (`ig_epic` +
`target_notional`, no `sec_type`), existing BARC untouched, then deleted
cleanly. IBKR side verified the same way (TEST round-tripped, NBIS
untouched). No service restart (static file). The two `instruments.html`
files are byte-identical again; they drift on the next change until the
PR 7 consolidation (`7a9dd06`) lands.

## pre_commit_check.py gate has pre-existing baseline failures

**Status:** Documented, not fixed.

`scripts/pre_commit_check.py` reports 10 failures on a clean HEAD (verified
by stashing unrelated changes and re-running): a `<nav>` assertion against
`web/dashboard.html` (which uses styled `<div>`s instead of a `<nav>`
element — functionally a nav bar, with `Instruments` / `Logged in as` /
`logout()` all present, but the check is stricter than the markup), and 9
hardcoded-path failures across `trading_v6/` (dead code), `web/data.json`
(runtime artifact), `.claude/settings.local.json`, `scripts/backup_dbs.sh`,
`backtest/database.py`, `backtest_2yr.py`, and
`tests/test_systemd_services.py`.

None of these affect runtime. Triage and fix as part of a future cleanup
PR.

Construction work stops here. The §14 regime pipeline is genuinely
complete on IBKR; IG-side blockers are upstream of our code.

## Strategy engine cluster is unwired

**Status:** Deferred — pending day-30 classifier evaluation
**Spec:** §8, §11.1

Surfaced by the post-gap-#10 audit on 2026-06-01. The
`StrategyEngine` ABC (`bot/strategies/base.py`) and its concrete
subclasses — `TripleConfirmationEngine`, `MeanReversionEngine`,
`NoOpEngine` — exist as classes but are **never instantiated in
production**. The router (`bot/regime/router.py`) emits engine names
as strings (`selected_engine="TripleConfirmationEngine"`, etc.) but
nothing constructs the engines or calls their `evaluate()`. Layer1
continues to dispatch through the legacy
`bot/signals.py:SignalEngine`.

By transitive dead-code: `bot/strategies/registry.py:get_engine()`
has only one caller (`bot/shadow/exit_policy.py`), which is itself
never invoked from production. `bot/shadow/position_metadata_store.py`
is instantiated in `main.py:145`, passed into the orchestrator as
`position_metadata_store=...`, stored as `self._pm_store` at
`orchestrator.py:40`, and never read again — same shape as gap #9.

Flipping `enable_router_live` or `enable_position_tagged_exit_policy`
to true does **nothing functional today**: the router would activate
its decision path but route to classes that aren't instantiated.
Genuine regime-aware execution requires, end-to-end:

1. Instantiate `TripleConfirmationEngine` and dispatch through it
   from layer1 for `TRENDING` regimes.
2. Instantiate `NoOpEngine` and respect its HOLD outcomes (so
   `UNCLEAR` and DATA_QUALITY-blocked cases actually skip live
   entries).
3. Implement `MeanReversionEngine` from skeleton to functional
   strategy (separate research effort — see
   "Mean-reversion engine is a skeleton only" above).
4. Wire `PositionMetadataStore` so the orchestrator's `post_trade`
   records `entry_engine` per fill.
5. Wire `bot/shadow/exit_policy.py` so exits dispatch via the
   engine recorded at entry.

Estimated effort: ~2-3 weeks for steps 1, 2, 4, 5; mean-reversion
implementation (step 3) is its own project.

If the classifier proves valuable at the day-30 evaluation (i.e.
shadow disagreement data shows the router would have improved P&L
or avoided drawdowns), this becomes the highest-priority work.
If not, the engine cluster can be deleted along with the router
strings and the unused stores. This is the largest "decide what to
do with what was built" call still outstanding from PRs 1-7.

See the "Integration-gap pattern" section above — this is the same
shape as gaps #8, #9, #10, just larger.

## Degradation framework is dormant

**Status:** Deferred — consider before any `overlays_live` promotion
**Spec:** §12

Surfaced by the same 2026-06-01 audit. `bot/degradation/` ships a
full framework — `FailureTracker`, `DegradationPolicy`,
`DegradationThreshold`, `DegradationEvent` — but **none of these
classes are instantiated in production**. The only wired piece is
`InstrumentPauseRegistry` (`main.py:143`), which is the **output**
of the framework: the orchestrator reads it to decide whether to
gate entries. Nothing on the input side writes to it.

`bot/degradation/recover_overlay.py` is a standalone CLI (run via
`python -m bot.degradation.recover_overlay`); it gives operators a
manual way to register and clear pauses. It is not part of the
running bot, and is the *only* path by which the pause registry
ever gets populated today.

Practical implication: a malfunctioning data source (stale bars,
runaway classifier, broker quote drift) can keep producing
degraded signals indefinitely with no automatic safety stop. The
system relies entirely on operator vigilance and the CLI for
intervention. With `overlays_live=False` and `router_live=False`
today, the blast radius is bounded — degraded signals still flow
through the legacy gates. Once either flag is promoted to live,
the lack of automatic failure detection becomes a real risk.

Wiring requires, at minimum:
1. Instantiate `FailureTracker` in `main.py` and pass it into the
   classifier, scheduler, smoothing store, and broker (the
   subsystems whose failures should count).
2. Each subsystem records its own success/failure outcomes via
   `tracker.record(...)`.
3. Per-cycle, `DegradationPolicy.evaluate(tracker)` consults the
   thresholds (already defined in `bot/degradation/policies.py`)
   and emits `DegradationEvent`s into `InstrumentPauseRegistry`.
4. Surface degradation events in the dashboard (the read endpoint
   `dashboard_data.get_degradation_events` is already wired and
   waiting for rows to display).

Estimated effort: ~1 week to wire the failure-recording call sites
plus the policy evaluation loop, given the framework code already
exists.

This should land before any live promotion of `enable_event_overlays_live`
or `enable_router_live`. Without automatic degradation, those
flags shift more decisions onto a pipeline whose failure modes are
not observable in real time.

See "Integration-gap pattern" above — same shape, applied to
operational safety rather than feature delivery.

## CostTracker.get_daily_spend filtered on the wrong column

**Status:** Fixed 2026-06-01

`CostTracker.get_daily_spend(day)` filtered `WHERE trading_date = ?`
when it should have filtered `WHERE date(ts) = ?`. The two columns
coincide in normal scheduler use (the bot classifies today's
trading_date today), so the bug was invisible in production. It
surfaced when the classifier forward-returns evaluator ran in
`--backfill` mode on 2026-06-01: 196 calls written with historical
`trading_date` values reported $0.0000 spent, and the script's
mid-run `is_budget_exceeded(today)` check never would have fired
against backfill cost no matter how large the run got. The actual
run stayed within cap by luck.

Patched to `date(ts) = ?` so the filter follows the wall-clock day
the calls were logged on — the meaningful unit for budget
enforcement. Param renamed `trading_date` → `day` with a docstring
spelling out the semantics. All five callers (classifier
pre-flight, three `api_server.py` sites, eval scripts) audited and
already pass today's wall-clock date, so behaviour for normal
operation is unchanged; only the backfill scenario flips from
"silently uncapped" to "correctly capped". Regression test added
in `tests/regime/test_cost_tracker.py` covering exactly the
backfill scenario.

Lesson for future budget-sensitive scripts: budget checks must
filter on log timestamp (`date(ts)`), never on the
classification's `trading_date`. When the two are equal it's a
coincidence of the bot's normal cadence, not a property of the
data model.

## Regime filter is direction-agnostic (intent, not a bug)

**Status:** Documented 2026-06-01

`RegimeOrchestrator.apply_regime_filter` allows an entry only when the
instrument's smoothed `effective_regime` is `TRENDING`. It checks regime
*structure* (TRENDING / RANGING / UNCLEAR), not *direction*. A long signal
in a TRENDING-down market passes the filter, and a short in a TRENDING-up
market passes too — direction is owned by the underlying signal engine
(triple-confirmation), not the filter. The filter's only job is to
suppress chop (RANGING / UNCLEAR).

Recorded here so it isn't "fixed" later by someone who reads a blocked
against-trend trade as a defect. Consequence to keep in mind when reading
the Filter comparison tile / `filter_performance` aggregation: the
would-have-blocked shadow P&L includes trending-but-against-trend entries,
so the comparison is "regime gate on vs off", not "good-direction trades
only". If a directional gate is ever wanted, it belongs in the signal
engine or as a separate gate — not by overloading the regime filter.

## Dashboard dual-source-of-truth bit twice in the regime-filter work

**Status:** Open — root cause fixed for the Filter tile, pattern remains

`web/dashboard.html` is generated wholesale from the f-string in
`bot/dashboard.py:_write_html()`, which runs on every `Dashboard(...)`
instantiation (bot startup *and* any test that constructs a Dashboard).
Commit `c2f6656` added the Filter tile — both the tab/pane/render JS *and*
the `tab.render(tab.usesRawData ? data : rows)` dispatch the tile depends
on — directly to the generated `web/dashboard.html`, never to the
generator. The next regeneration silently reverted both, leaving the
`/api/regime/filter_performance` endpoint orphaned. Fixed 2026-06-01 by
porting both into `bot/dashboard.py` and confirming regeneration now
reproduces the committed artifact byte-for-byte.

The underlying hazard remains: there is no guard that the committed
`web/dashboard.html` equals `_write_html()` output. Future tile edits made
to the HTML by hand will be reverted the same way. Suggested fix for a
later pass: a test that constructs the Dashboard, reads the generated
file, and asserts it equals the committed `web/dashboard.html` — turning
silent drift into a red test.

## Deferred minor cleanups (regime-filter review, 2026-06-01)

**Status:** Open — batch for a future tidy-up pass

Surfaced during the regime-filter review; deferred to keep the restore
commit focused.

- **(4a) `.gitignore` misses SQLite sidecars.** ✅ Resolved (2026-06-02).
  `*.db` is ignored but `*.db-wal` / `*.db-shm` / `*.db-journal` were not, so
  `regime.db-wal` and `regime.db-shm` showed as untracked. `logs/` was also
  untracked. These patterns are now in `.gitignore`.
- **(4b) Orchestrator reaches into RegimeCache internals.**
  `RegimeOrchestrator._lookup_latest_rationale` opens its own sqlite
  connection against `self._regime_cache._db_path` (a private attr).
  Add a `get_latest_rationale(instrument)` method to `RegimeCache` so
  there is one connection owner / path.
- **(4c) `datetime.utcnow()` deprecation in JWT.** `api_server.py:101-102`
  uses `datetime.utcnow()` for JWT `iat`/`exp` (raises DeprecationWarning
  under the test run). Switch to `datetime.now(timezone.utc)` before a
  Python bump removes it.

---

## Filter experiment data semantics: blocks ≠ shadow trades

**Status:** Reference note. Not a defect — documents expected behaviour so the
`filter_performance` / Filter-tile read isn't misinterpreted as "experiment
broken" when the closed-shadow sample looks small early on.

When reading the regime-filter experiment's counterfactual data, the count of
`regime_blocked_entries` and the count of `shadow_hypothetical_trades` measure
two different things and will diverge by a large factor. That divergence is
correct.

- **Deduplication is by design.** `ShadowTradeSimulator.open()` refuses to
  stack — at most one open shadow position per instrument. Repeated blocks on
  the same instrument *while a shadow is already open* are no-ops, not data
  loss. The blocked signal being re-evaluated each cycle is essentially the
  same signal the existing shadow is already tracking.
- **Expected ratio.** Blocks ≈ (number of cycles the signal fires) × (number
  of blocked instruments). Shadow trades ≈ number of *distinct shadow
  lifecycles* (each runs from open until its exit). A 100:1 or higher
  blocks-to-shadow-trades ratio is normal: it reflects the same instrument
  being re-blocked every cycle while one shadow position already tracks it.
- **Market-hours tick gating.** Shadow positions only tick during their
  instrument's market hours (`_process_instrument` returns early on a closed
  market, before the tick loop). LSE shadows freeze 16:30–08:00 UTC; US
  shadows freeze 21:00–13:30 UTC. This is correct — you can't price a closed
  market — but it means a shadow only progresses during its session.
- **Tier-2 exits evaluate on bar close.** Trailing stop and take profit are
  checked only on bar close; for daily-timeframe instruments that is once per
  day. The emergency stop is checked per tick. So most shadow closures land at
  end-of-day or on daily bar boundaries, not intraday.
- **Practical implication for interpretation.** The closed-shadow-trade sample
  grows slowly — roughly 5–15 per week initially, scaling up as the filter
  blocks more variety. After 2–4 weeks, expect ~30–80 closed shadow trades to
  compare against the live path. Do **not** read a low closed-trade count in
  week 1 as "experiment broken."
- **Snapshot at time of writing (2026-06-02, day 1 of live filter):** 6 shadow
  lifecycles (1 closed, 5 open), 1097 blocks accumulated. The closed MSFT
  trade hit its trail stop at −3.50% — the first counterfactual data point
  captured.

---

## Instruments Editor toggle-enable bypasses disabled_reason

**Status:** Known foot-gun. Documented after the 2026-06-02 XAUUSD incident.
Mitigation not yet implemented — recorded for a follow-up.

The dashboard's `POST /api/instruments/toggle-enable` endpoint flips an
instrument's `enabled` field without checking the `disabled_reason` field.
On 2026-06-02 09:22, this allowed XAUUSD and XAGUSD (both disabled with
reason `no_cfd_market_data_paper_account`) to be enabled via the UI. The
bot then registered phantom positions, took fake stop-out losses on bad
CFD data, and generated 12 minutes of error churn until manually disabled
at 09:32. A fictitious -$657.74 USD loss froze in `pnl_cache` and required
SQL cleanup.

**Mitigation:** add a guard to the toggle-enable endpoint that blocks (or
requires explicit confirmation override) when an instrument has any
non-null `disabled_reason`. For reasons matching
`paper_account|no_cfd_market_data|no_data_subscription`, block entirely
with an error message. For other reasons, require confirmation with the
reason text shown.

**Severity:** moderate — caused real operational churn and required manual
cleanup, but no real money at risk (paper account).

---

## Backtest short-side cost/fill paths lack dedicated tests (Fix 2 follow-up)

**Status:** Implemented, not yet covered by tests. Recorded during Fix 2
(commission + spread/slippage + gap-through). Must be closed BEFORE the
full-universe Phase 2 run, since some instruments are shortable CFDs.

`backtest/simulator.py`'s cost layer handles shorts symmetrically:
adverse entry/exit fills use `is_buy = not is_buy` (closing a short is a
BUY, fills higher), and gap-through `_exit_level()` flips the `min`/`max`
for SELL direction. The LONG path is tested in `tests/test_simulator_costs.py`
AND reproduced bit-for-bit by the zero-cost test. The SHORT path has only
one test — `test_entry_fill_adverse_for_short` (entry side). Untested:
  - short EXIT adverse fill (buy-to-cover should fill ABOVE the level)
  - short stop gap-through (gap UP through a short's stop -> fill at open)
  - short TP gap-through (gap DOWN through a short's TP -> fill at open)

AAPL (long-only) is unaffected, so this did not block Fixes 1–3. Add the
three cases above before enabling shorts in Phase 2.

---

## Dashboard backtest path is still costless (Fix 2 / Fix 3 deferral)

**Status:** Intentional deferral. The CLI backtest path (`backtest/run.py`
-> `run_simple_backtest`) is now the AUTHORITATIVE costed + realistically
sized path: it applies base transaction costs (commission, spread,
slippage, gap-through) and threads `default_target_notional` into sizing.

The DASHBOARD path (`api_server.py` -> `run_walk_forward` at line ~903 and
`full_optimise` at line ~1091) still calls `simulate_trades` WITHOUT a
`cost_config` and WITHOUT `default_target_notional`, so it remains
frictionless and fixed-qty. This was left unwired deliberately to avoid a
`cogniflowai-api.service` restart and to keep the live regime experiment's
dashboard numbers stable during the experiment window.

**Consequence:** dashboard backtest/optimise numbers are OPTIMISTIC
(costless) and use fixed `qty`, not realistic notional sizing. They will
NOT match the CLI's costed numbers. Treat the CLI output as ground truth
until this is wired.

**To close:** pass `cost_config=CostConfig.from_settings(...)` and
`default_target_notional=settings.get("default_target_notional")` into the
`api_server.py` backtest calls, then restart `cogniflowai-api.service` and
re-verify the routes per CLAUDE.md.
