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
