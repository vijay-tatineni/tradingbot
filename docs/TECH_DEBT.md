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
