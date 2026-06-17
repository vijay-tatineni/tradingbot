# Dynamic Universe v1 — Shadow Operations

> How to migrate, seed, run (test/shadow only), inspect outputs, recover, and back up.
> The shadow foundation is non-integrated; nothing here runs in production by default.

## Migration

```python
from bot.universe.db import migrate
migrate("/path/to/universe.db")   # idempotent; creates/updates the 6 tables to version 2
```
Schema head is **version 2** (Pre-Enable R1, strictly additive: session-based cooldown
fields, durable exit-event markers, append-only history triggers). Upgrading a v1 DB is
additive and does not back-fill — see `docs/universe_db_schema.md`.

## Registry seed (idempotent, read-only on configs)

```python
from bot.universe.seed import seed_registry
seed_registry("/path/to/universe.db")           # reads repo instruments.json + instruments_ig.json
# or seed_registry(db, instruments_path, instruments_ig_path)
```
Behaviour: creates canonical records; IBKR mappings `CONFIG_DERIVED`; **IG mappings
`UNVERIFIED` + `order_routing_blocked=1` (always)**; `primary_gateway=IBKR`;
`hard_disabled` preserved (XAUUSD/XAGUSD never enabled). Safe to rerun (pure upserts;
config files never rewritten — asserted by tests).

## Running the shadow evaluator (TEST/SHADOW ENVIRONMENT ONLY)

```python
from bot.universe.registry import Registry
from bot.universe.evaluator import ShadowEvaluator

flags = SomeFlags(enable_dynamic_universe_shadow=True)   # NEVER set in live config
ev = ShadowEvaluator(Registry(db), bars_provider, flags, equity=100_000)
ev.maybe_run("2026-06-10")        # flag false → {'ran': False, ...} no-op
```
`bars_provider(rec)` returns `{bars, corp_action_status, sector, spread, fresh_bar,
admin_paused}`; it must NOT touch a broker. The `DailyUniverseScheduler` wraps this with
post-close gating and restart-safe idempotency.

## Shadow outputs

* `universe_state` — current state, hysteresis counters, cooldown remaining, snapshot hash.
* `universe_state_history` — append-only per (instrument, trading_date, evaluator
  version), with `feature_snapshot_json` (bar_count, price, adv20, indicators_available,
  ohlc_valid, corp_action_status, sector, entry_signal, trend_break, atr14, sma50,
  primary_gateway, ibkr_mapping_ok) and reason codes.
* `maybe_run` returns per-instrument outcomes plus `selected` (with `hypothetical_order`:
  entry_price, initial_stop, qty, risk_usd, notional_usd, primary_gateway=IBKR) and
  `rejected` (with `rejected_reason`: slot_cap_reached / sector_cap_reached /
  portfolio_heat_exceeded). **No PF / Sharpe / returns / rankings are computed or logged.**

## Position-status seam (broker-free; task §3 / P3-8 / P3-9, R1)

The evaluator takes an injected `PositionSnapshotProvider`
(`get_position_status(canonical_instrument_id, trading_date) -> PositionStatus |
PositionSnapshot`). It is broker-free by contract (a fixture / non-production snapshot —
never a broker call) and drives the POSITION_OPEN / EXIT_ONLY / COOLDOWN lifecycle
organically. **The provider is authoritative (P3-8, R1.1):** with **no provider injected** —
or on a provider exception / timeout / malformed / unrecognised / stale / future / out-of-order
value — the observation is non-authoritative → `UNKNOWN` (fail-safe: no flat assumption, no
entry, no forced liquidation, cooldown held). The evaluator persists the LATEST observation
(`latest_observed_*`) separately from the LAST AUTHORITATIVE evidence (`last_authoritative_*`),
which a non-authoritative observation NEVER erases — so an exit during an outage survives.
**Exit detection is durable and exactly-once (P3-9, R1.1):** cooldown starts from an explicit
durable `POSITION_EXITED` signal, or an authoritative `last_authoritative=POSITION_OPEN →
NO_POSITION` transition with EXPLICIT closure evidence, de-duplicated by
`last_processed_position_event_id` + the lifecycle-qualified `last_close_event_key` (with a
stale-close guard). **Close identity is strictly lifecycle-qualified (R2A-0.1; supersedes R1.3 /
Finding 1):** to START cooldown the close must carry a COMPLETE valid lifecycle — a `position_id`
(→ hash), a valid `opened_trading_date` AND a valid `closed_trading_date` (`opened <= closed <=
evaluation date`, `opened` not future) — plus the `close_event_id` when supplied. An explicit
`close_event_id` is PREFERRED but NOT sufficient alone. The qualified key is
`close-key:v2:cid|pid_hash|opened|closed|close_event_id`. A close missing/with-malformed any
required field, or the SAME explicit id reused under a DIFFERENT lifecycle (different
opened/closed date or position hash), routes to `POSITION_RECONCILIATION` — entry blocked, no
cooldown, no processed marker, authoritative OPEN anchor retained — never a cooldown bypass or a
masked second close. **Providers MUST supply `position_id` AND both lifecycle dates (and SHOULD
supply a globally-unique-per-lifecycle `close_event_id`) for every cooldown-starting close.** An
authoritative open→flat
WITHOUT evidence sets a durable `position_reconciliation_required` block, surfaced since R1.2
as the dedicated `POSITION_RECONCILIATION` state (blocks entry, no liquidation, holds cooldown;
cleared only by an authoritative `POSITION_OPEN` or an evidence-bearing close). A
non-authoritative observation (`UNKNOWN`/stale/future/missing provider) also resolves to
`POSITION_RECONCILIATION` (R1.2 / P2-C) — `EXIT_ONLY` now means a position AUTHORITATIVELY
exists, never uncertainty. Cooldown never starts from `UNKNOWN → NO_POSITION`, a no-evidence
open→flat, or a provider error.

```python
ev = ShadowEvaluator(Registry(db), bars_provider, flags, equity=100_000,
                     position_provider=my_provider)   # my_provider: NO broker access
```

## Scheduler completed-bar availability (task §5)

The fixed post-close UTC gates are conservative across both DST regimes, early closes
and half-days (they can only run LATE, never early). They cannot prove a bar EXISTS on
a holiday or that a late bar has arrived, so inject a `bar_available_fn(rec,
trading_date) -> bool`: instruments whose completed bar is not confirmed are SKIPPED and
logged (no history written) and retried idempotently next cycle. A raising check is
treated as unavailable (fail-safe skip). `maybe_run` returns `missing_bar_skipped`.

```python
sched = DailyUniverseScheduler(ev, flags, bar_available_fn=lambda rec, td: bar_exists(rec, td))
```

## Failure behaviour

* Missing/short bars → DATA_INELIGIBLE (insufficient_history / indicators_unavailable).
* Corporate-action status unknown/unavailable → **shadow** records
  `corporate_action_status_unknown` (warning, non-blocking); **paper/live** (default,
  fail-closed) records `corp_action_data_unavailable` and FAILS eligibility. A detected
  `anomaly` blocks in both. Never a silent pass. See state-transitions doc §"Corporate-
  action policy".
* Position status UNKNOWN → `position_status_unknown`, safe non-entry `POSITION_RECONCILIATION`
  hold (R1.2 / P2-C — no longer `EXIT_ONLY`, which now means an authoritative open exists).
* Completed bar not yet available (holiday/late) → scheduler skip + retry (no history).
* Unknown sector → `sector_unknown` recorded (non-blocking).
* Idempotency conflict on history insert → skipped (already recorded); other DB errors
  propagate (not silently swallowed).
* State + history are persisted ATOMICALLY (P3-3, R1) via
  `Registry.persist_transition_atomic` in one `BEGIN IMMEDIATE` transaction — both commit or
  both roll back, so a crash never leaves `universe_state` advanced without its history row
  (no double-advance on the next run). History rows are physically append-only (v2 triggers).

## Offline rehearsal (task §6)

`tests/universe/rehearsal.py::run_rehearsal(db_path)` runs a deterministic, fully
offline end-to-end rehearsal on synthetic fixtures (no broker, no data provider, no live
DB, no production config writes). It exercises seed, idempotent migration rerun,
AUTO/TTI/MANUAL candidates + 5-session TTL, entry/removal hysteresis, ADMIN_PAUSED,
DATA_INELIGIBLE, HARD_DISABLED, the full POSITION_OPEN ⇄ EXIT_ONLY → COOLDOWN lifecycle
with E+1..E+3 blocking and E+4 release, the POSITION_RECONCILIATION uncertainty hold (R1.2),
slot/sector/heat contention, IBKR-primary +
IG-routing-blocked, multi-timezone scheduling, same-day idempotency, restart recovery,
missing-bar skip, corporate-action UNKNOWN warning, and position-status UNKNOWN safe
behaviour. The exit (E) is driven by a **durable `OPEN → NO_POSITION` + `closed_trading_date`
transition (P3-9)** — not the deprecated transient `POSITION_EXITED_TODAY` — so the rehearsal
exercises the robust exit path rather than masking the blocker. It reports **operational
metrics only** — never returns / PF / Sharpe /
winners / rankings (`format_report`). Determinism and the no-performance-metric guard
are asserted in `tests/universe/test_rehearsal.py`.

## Backup / restore

`universe.db` is gitignored. Back up by copying the file (and recording its SHA-256) to
a restricted directory; restore by copying back and running `migrate` (idempotent). It
holds only research/shadow state — it is never the source of live execution state.

## Provider / historical-validation status

Historical validation of the shadow universe remains **BLOCKED** pending approved
provider access — see `docs/dynamic_universe_provider_status.md`. The verdict
`TRIAL ACCESS REQUIRED BEFORE DECISION` stands; no EODHD call or ingestion is performed.

## R2A-1 canonical identity & verified broker mappings (P3-6 / P3-7) — default-off

Identity resolution is an OFFLINE, controlled operation — NOT part of the per-cycle evaluator.
An operator/tool resolves a verified reference (from a broker-free security-master provider
implementing `IdentityReferenceProvider`) and persists it via
`IdentityStore.resolve_identity_atomic(reference, trading_date, resolver_version,
canonical_instrument_id=...)`. This is the ONLY way an opaque `instrument_uid`/`listing_uid`
is created. The evaluator never calls a reference provider; it only READS persisted identity
through the pre-entry gate, and only when `enforce_verified_identity=True`.

Operational invariants in this tranche:
* IG `order_routing_blocked` is frozen `1`; IG routing is never eligible; no IG broker call.
* No IBKR/IG/EODHD call is made by the identity layer (broker-free; structurally asserted by
  `tests/universe/test_r2a1_isolation.py`).
* The feature stays disabled (`enable_dynamic_universe_shadow=False`) and the gate stays
  default-off; un-wired into `main.py`/`api_server.py`.
* A verified mapping must be re-verified within 90 calendar days (`reverify_after_date`); an
  expired mapping is treated as `STALE` and blocks entry until re-resolved.

## R2B persisted candidate sources (P3-4) — default-off

Candidates (MANUAL/TTI/AUTO) must be PERSISTED via `CandidateStore` before they can affect
selection; the evaluator consumes them only through `effective_candidates(trading_date)`.
Submission is an offline/controlled operation (`submit_candidate_atomic` /
`submit_auto_batch_atomic`) — the evaluator never generates candidates on the hot path. A
candidate is usable only with a verified R2A-1 `instrument_uid` + `listing_uid` (never resolved
from a ticker); unresolved/ambiguous/unverified submissions are persisted REJECTED and audited.

Operational invariants in this tranche:
* Precedence MANUAL>TTI>AUTO; suppressed candidates are retained/auditable; an ACTIVE
  higher-precedence candidate that fails validation BLOCKS the instrument
  (`candidate_source_conflict`) rather than falling through to a lower-precedence listing.
* MANUAL/TTI TTL = 5 completed sessions (effective E..E+4, expired E+5); AUTO is per-session
  and atomically replaced per batch. TTL counting is idempotent per date.
* The candidate gate is **default-off** (`require_candidate_source=False`); the feature stays
  disabled (`enable_dynamic_universe_shadow=False`) and un-wired into main.py/api_server.py.
* Broker-free: no IBKR/IG/EODHD call; structurally asserted by
  `tests/universe/test_r2b_selection.py`. Candidate expiry affects NEW entries only — open
  positions continue to be managed.
