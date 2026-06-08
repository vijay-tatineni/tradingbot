# Dynamic Universe — Instrument State Machine (Hybrid v1)

> **Design/feasibility only.** No code, migrations, or service changes. Part of the
> `design/dynamic-universe-hybrid-v1` deliverable set. Master index:
> `experiments/dynamic_universe_hybrid_v1_DRAFT.md`.
>
> Covers deliverable **item 4** (universe-state schema + immutable history schema) and
> **item 5** (state-transition table). Proposes hysteresis/cooldown numbers as
> **REQUIRING OPERATOR APPROVAL** (task §3 — "do not silently freeze their numeric
> values"). Transition-by-transition tests are enumerated in DRAFT.md §15 (item 14).

---

## 1. Scope and the one non-negotiable semantic

This machine governs **per-instrument entry eligibility** in the dynamic universe. It is a
*daily-evaluated* state, stored in new tables (`universe_state`, `universe_state_history`),
**never** in `instruments.json` (task §1/§15; pinned by the §14 test "config remains
unchanged by daily evaluation").

The single rule that the task repeats and that every transition must respect:

> **Losing entry-eligibility never liquidates an open position.** Removing an instrument
> from daily eligibility (→ `EXIT_ONLY`, `ADMIN_PAUSED`, `COOLDOWN`, `DATA_INELIGIBLE`,
> `WATCHLIST`) blocks *new* entries only. Any existing position stays in the
> position-management set and continues to receive full exit management (initial stop,
> 3×ATR ratchet trailing stop, SMA50 trend-break) under the frozen exit rules.

This is the design realisation of: "Daily 'remove' means blocking new entries. It must not
remove an existing position from exit management" and "If a position exists, it becomes
exit-only and remains fully managed."

---

## 2. States

| State | Meaning | New entries? | Exit mgmt of open pos? | Can be set by daily eval? |
|---|---|---|---|---|
| `HARD_DISABLED` | Structurally forbidden; broker-ineligible or administratively disabled. | **Never** | n/a (should hold no position) | **No** — dominant, only the future admin endpoint clears it |
| `DATA_INELIGIBLE` | Stale / missing / corrupt / insufficient (<250 bars) / unresolved data, or unresolved corporate action. | No | **Yes, if a position exists** | Yes |
| `WATCHLIST` | Monitored, structurally eligible, but no live breakout candidate today (or not yet promoted). | No | Yes, if a position exists | Yes |
| `ENTRY_ELIGIBLE` | Passed structural eligibility **and** is a deterministic breakout candidate for the next session. | **Yes** | Yes | Yes |
| `POSITION_OPEN` | Live position under normal management. | No (no pyramiding) | Yes | Yes (entered from ENTRY_ELIGIBLE on fill) |
| `EXIT_ONLY` | No new entry, no reversal, no pyramiding; existing position fully managed until flat. | **Never** | Yes | Yes |
| `COOLDOWN` | Temporarily blocked after an exit or a frozen churn rule. | No (until cooldown clears) | Yes, if a position exists | Yes |
| `ADMIN_PAUSED` | Operator-blocked from new entries; exits continue. | **Never (while paused)** | Yes | Set by operator action, surfaced in daily eval |

### Dominance order (highest wins)

```text
HARD_DISABLED  >  ADMIN_PAUSED  >  DATA_INELIGIBLE  >  EXIT_ONLY  >  COOLDOWN
               >  POSITION_OPEN  >  WATCHLIST  >  ENTRY_ELIGIBLE
```

`HARD_DISABLED` is absolute: it cannot be overridden by AUTO/TTI/MANUAL candidacy, by a
positive breakout signal, by operator dashboard toggles, by full/layer saves, or by
walk-forward application — exactly the writer paths the deployed
`validate_hard_disabled_instruments` guard already rejects (`bot/guardrails.py`, commit
`72e00cb`). The state machine does **not** reimplement that check; it *defers* to it.

### Reuse of existing mechanisms (do not reinvent)

| State / concept | Existing component to reuse | Reference |
|---|---|---|
| `HARD_DISABLED` authority | `validate_hard_disabled_instruments` over `INSTRUMENT_SECTIONS` | `bot/guardrails.py` (`72e00cb`) |
| `ADMIN_PAUSED` | `InstrumentPauseRegistry` (pause/clear/is_paused; table `instrument_entry_pauses`) | `bot/degradation/instrument_pause_registry.py:14` |
| `COOLDOWN` | `PositionTracker` watch state (`watch_positions`, `check_reentry`, `cooldown_mins`) | `bot/position_tracker.py:430` |
| Hysteresis / pending-promotion pattern | `SmoothedStateStore` + `smoothing.py` thresholds | `bot/regime/smoothing_store.py`, `bot/regime/smoothing.py` |
| Entry-gate plumbing | `RegimeOrchestrator.pre_trade` returns False to block | `bot/regime/orchestrator.py:146` |
| Daily idempotent evaluation | `RegimeClassificationScheduler.maybe_run` + `cache.has_for_day(symbol, day)` | `bot/regime/scheduler.py:84` |
| Position protection during "remove" | `EXIT_ONLY` mirrors existing `allow_new_entries:false` semantics | `bot/guardrails.py` no-edge guard; layer1 exit path |

`ADMIN_PAUSED` maps **directly** onto the existing `InstrumentPauseRegistry`: a paused
instrument already blocks entries via `RegimeOrchestrator._evaluate_gates_with_context`
(`orchestrator.py:95`) while exits continue. The dynamic-universe layer reads that registry
rather than maintaining a second pause concept.

---

## 3. Events (transition triggers)

```text
E_HARD_DISABLE          admin endpoint sets hard_disabled (future, not built here)
E_ADMIN_PAUSE           operator pauses (InstrumentPauseRegistry.pause)
E_ADMIN_UNPAUSE         operator clears   (InstrumentPauseRegistry.clear)
E_DATA_FAIL             structural data check fails (<250 bars, stale, corrupt, unresolved CA)
E_DATA_OK               structural data check passes again, sustained ≥ N_data sessions (hysteresis)
E_CANDIDATE             instrument is an AUTO/TTI/MANUAL candidate today AND passes structural eligibility
E_BREAKOUT_SIGNAL       frozen deterministic breakout conditions true on completed bar (next-session entry)
E_NO_SIGNAL             no breakout candidate this session
E_SLOT_WON              risk/slot selection selected this candidate for an order intent
E_SLOT_LOST             candidate valid but lost slot contention (cap/sector/heat)
E_FILL                  broker fill confirmed → position opened
E_FLAT                  position closed (stop / trail / trend-break / liquidation)
E_EXIT_ONLY_MARK        instrument loses entry-eligibility while holding a position (any "remove")
E_COOLDOWN_START        post-exit or churn rule begins cooldown
E_COOLDOWN_CLEAR        cooldown elapsed AND (frozen) re-entry recovery condition met
E_DAILY_EVAL            daily scheduler tick (idempotent per trading session)
```

---

## 4. State-transition table (from × event → to)

`—` = event not applicable / ignored in that state. **POS** column = effect on any open
position. Unless noted, **POS = fully managed, never liquidated by the transition.**

| From \ Event | E_HARD_DISABLE | E_ADMIN_PAUSE | E_ADMIN_UNPAUSE | E_DATA_FAIL | E_DATA_OK | E_CANDIDATE | E_BREAKOUT_SIGNAL | E_NO_SIGNAL | E_SLOT_WON | E_SLOT_LOST | E_FILL | E_FLAT | E_EXIT_ONLY_MARK | E_COOLDOWN_START | E_COOLDOWN_CLEAR | POS |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **HARD_DISABLED** | HARD_DISABLED | HARD_DISABLED | HARD_DISABLED | HARD_DISABLED | HARD_DISABLED | HARD_DISABLED | HARD_DISABLED | HARD_DISABLED | — | — | — | — | — | — | — | should be flat; if a legacy position exists it is EXIT_ONLY-managed, never re-entered |
| **DATA_INELIGIBLE** | HARD_DISABLED | ADMIN_PAUSED | — | DATA_INELIGIBLE | WATCHLIST (after N_data) | DATA_INELIGIBLE | — | DATA_INELIGIBLE | — | — | — | DATA_INELIGIBLE | EXIT_ONLY | COOLDOWN | — | open pos fully managed |
| **WATCHLIST** | HARD_DISABLED | ADMIN_PAUSED | — | DATA_INELIGIBLE | WATCHLIST | ENTRY_ELIGIBLE | — | WATCHLIST | — | — | — | — | EXIT_ONLY | COOLDOWN | WATCHLIST | (usually flat) |
| **ENTRY_ELIGIBLE** | HARD_DISABLED | ADMIN_PAUSED | — | DATA_INELIGIBLE | ENTRY_ELIGIBLE | ENTRY_ELIGIBLE | ENTRY_ELIGIBLE | WATCHLIST | (intent emitted) ENTRY_ELIGIBLE | WATCHLIST | POSITION_OPEN | — | EXIT_ONLY | COOLDOWN | — | flat until fill |
| **POSITION_OPEN** | HARD_DISABLED* | ADMIN_PAUSED | — | DATA_INELIGIBLE | POSITION_OPEN | POSITION_OPEN (no pyramiding) | POSITION_OPEN (no pyramiding) | POSITION_OPEN | — | — | POSITION_OPEN | COOLDOWN | EXIT_ONLY | — | — | **fully managed** |
| **EXIT_ONLY** | HARD_DISABLED | EXIT_ONLY | — | EXIT_ONLY | EXIT_ONLY | EXIT_ONLY (no entry) | EXIT_ONLY (no entry) | EXIT_ONLY | — | — | EXIT_ONLY | COOLDOWN | EXIT_ONLY | — | — | **fully managed until flat; no reversal/pyramiding** |
| **COOLDOWN** | HARD_DISABLED | ADMIN_PAUSED | — | DATA_INELIGIBLE | COOLDOWN | COOLDOWN | COOLDOWN | COOLDOWN | — | — | POSITION_OPEN** | — | EXIT_ONLY | COOLDOWN | WATCHLIST | open pos (if any) fully managed |
| **ADMIN_PAUSED** | HARD_DISABLED | ADMIN_PAUSED | WATCHLIST*** | ADMIN_PAUSED (data flag noted) | ADMIN_PAUSED | ADMIN_PAUSED (no entry) | ADMIN_PAUSED (no entry) | ADMIN_PAUSED | — | — | ADMIN_PAUSED | COOLDOWN | EXIT_ONLY | — | — | **fully managed** |

Footnotes:

* `*` **POSITION_OPEN → HARD_DISABLED**: only via the future admin endpoint, and only as a
  *deliberate, audited* operator action. It does **not** force liquidation; the position
  becomes exit-only-managed (`EXIT_ONLY` behaviour) and can never be re-entered. The
  deployed guard prevents the instrument from being silently re-enabled afterwards.
* `**` **COOLDOWN → POSITION_OPEN on E_FILL**: only reachable if the (frozen) re-entry
  recovery + cooldown-elapsed conditions are met (`PositionTracker.check_reentry`,
  `position_tracker.py:433`) *and* a fresh breakout candidate won a slot. Cooldown does not
  itself open a position.
* `***` **ADMIN_PAUSED → WATCHLIST on E_ADMIN_UNPAUSE**: returns to the *neutral* monitored
  state, never directly to ENTRY_ELIGIBLE — the instrument must re-earn eligibility through
  the next daily structural + breakout evaluation. This prevents an unpause from
  instantaneously firing an entry on stale state.

### Position protection — explicit per "remove" transition

Every transition that removes entry-eligibility while a position is open routes the
position to exit-only management, never to liquidation:

* `* → EXIT_ONLY` (E_EXIT_ONLY_MARK): position retained, all three frozen exits active,
  no new entry, no reversal, no pyramiding.
* `* → DATA_INELIGIBLE` with open position: **exits still run on last-good data / broker
  price**; only *new entries* are blocked. (Design note: exit management must not depend on
  the same data feed that failed eligibility — it uses the broker position + price path,
  mirroring `bot/layer1.py` reconciliation which uses broker price for forced closes,
  `layer1.py:94`.)
* `* → ADMIN_PAUSED` with open position: identical to existing `InstrumentPauseRegistry`
  semantics — entries blocked, exits continue (`orchestrator.py` gate is entry-only).
* `* → COOLDOWN` with open position: cooldown blocks *re-entry*, not exit; an open position
  is impossible to be in pure post-exit cooldown, but a churn-rule cooldown applied while
  holding still leaves exits fully managed.

---

## 5. Hysteresis & cooldown — PROPOSED, REQUIRING OPERATOR APPROVAL

Numbers below are **proposals** anchored on existing precedent, not frozen values. They are
listed in DRAFT.md item 17 for explicit approval. Precedent: `bot/regime/smoothing.py`
already uses `PENDING_DAYS_TO_PROMOTE = 2`, `SAME_REGIME_PERSISTENCE_THRESHOLD = 0.85`,
`NEW_REGIME_PENDING_THRESHOLD = 0.70`, `TRENDING_TO_RANGING_HYSTERESIS = 0.75`.

```text
N_data   (DATA_INELIGIBLE → WATCHLIST):    require 2 consecutive sessions of passing
         structural data checks before regaining eligibility. (mirrors PENDING_DAYS_TO_PROMOTE=2)
         Rationale: one fresh bar after a stale gap should not instantly re-enable.

ENTRY_ELIGIBLE → WATCHLIST demotion:        immediate on E_NO_SIGNAL (no hysteresis needed —
         losing candidacy only blocks NEW entries, which is safe).

COOLDOWN duration (post-exit):              reuse per-instrument reentry_cooldown_mins
         (today default 30 min, persisted in watch_positions; position_tracker.py:50).
         Proposed dynamic-universe default: cooldown spans to the NEXT session open
         (not minutes) so a same-session re-entry churn is impossible. APPROVAL NEEDED:
         minutes-based (current) vs session-based (proposed).

Churn-rule cooldown:                        proposed — block re-entry of an instrument that
         has opened+closed ≥ 2 times within 5 sessions, for 5 sessions. APPROVAL NEEDED
         (this is a NEW frozen rule and must be pre-registered, not chosen ad hoc).

Candidate TTL (TTI/MANUAL):                 expire after 5 completed trading sessions unless
         resubmitted (task §2 — already approved). "Trading session" is exchange-specific
         (see §6 / DRAFT.md scheduler) — counted per the instrument's own exchange calendar.
```

**Why hysteresis only on the DATA path:** gaining entry-eligibility must be sticky-slow
(avoid flapping a marginal data feed on/off), but *losing* it is always safe-fast (blocking
new entries can never harm an open position). This asymmetry matches the deployed
fail-closed philosophy.

---

## 6. Daily evaluation cadence & timezones (summary; full design in DRAFT.md item 6)

State is re-evaluated by a daily, **idempotent-per-session** pass, reusing the existing
scheduler pattern: `RegimeClassificationScheduler.maybe_run` already (a) gates on a
per-instrument post-close time (LSE 17:00 UTC, US 21:30 UTC — `scheduler.py:33,35`) and (b)
dedupes with `cache.has_for_day(symbol, trading_date)` (`scheduler.py:84`). The
universe-state pass adopts the same two gates so re-running it within a session is a no-op
(the §14 idempotent-rerun test). "Five completed trading sessions" for candidate TTL is
counted on the instrument's exchange calendar via `bot/market_hours.py` /
`bot/bar_schedule.py` (LSE/US/EUR timezones + holidays).

---

## 7. Schemas

### 7.1 Current state (mutable, one row per instrument)

```text
universe_state
─────────────────────────────────────────────────────────────────────────
canonical_instrument_id  TEXT PRIMARY KEY  FK → canonical_instruments
state                    TEXT NOT NULL     -- one of the 8 states (enum)
since                    TEXT NOT NULL     -- when this state was entered (ISO)
effective_session_date   TEXT NOT NULL     -- the trading session this state applies to
reason_code              TEXT NOT NULL     -- machine code, e.g. "DATA_STALE","SLOT_LOST_SECTOR_CAP"
reason_detail            TEXT              -- human detail
data_pass_streak         INTEGER NOT NULL DEFAULT 0  -- consecutive passing sessions (hysteresis)
cooldown_until_session   TEXT              -- session date until which cooldown holds, or NULL
candidate_id             TEXT              -- FK → candidate_sources (if eligible via a candidate)
last_eval_at             TEXT NOT NULL
─────────────────────────────────────────────────────────────────────────
```

### 7.2 Immutable history (append-only, never updated/deleted)

```text
universe_state_history
─────────────────────────────────────────────────────────────────────────
id                       INTEGER PRIMARY KEY AUTOINCREMENT
ts                       TEXT NOT NULL          -- wall-clock of the transition
canonical_instrument_id  TEXT NOT NULL
from_state               TEXT                   -- NULL for first observation
to_state                 TEXT NOT NULL
event                    TEXT NOT NULL          -- the E_* trigger
reason_code              TEXT NOT NULL
effective_session_date   TEXT NOT NULL
had_open_position        INTEGER NOT NULL       -- 1 if a position existed at transition time
position_protected       INTEGER NOT NULL       -- 1 = transition did NOT liquidate (must be 1 for every "remove")
actor                    TEXT NOT NULL          -- "daily_eval" | "operator:<user>" | "admin_endpoint"
flag_snapshot_json       TEXT                   -- feature-flag snapshot (mirrors shadow_decisions.flag_snapshot_json)
─────────────────────────────────────────────────────────────────────────
```

`universe_state_history` is the audit spine: it is **append-only** (no UPDATE/DELETE),
mirrors the append-only design of the existing `regime_blocked_entries` and
`shadow_decisions` tables, and lets every state change be replayed and audited. The
`position_protected` column is asserted `= 1` for every removal transition by the test plan
(DRAFT.md §15) — a structural guarantee that "remove never liquidates."

---

## 8. What this document deliberately does NOT do

* Does **not** freeze the proposed hysteresis/cooldown numbers (task §3) — they are
  approval items.
* Does **not** add an alpha/quality ranking to state promotion (task §2) — entry-eligibility
  is structural + frozen-breakout only; slot ordering is ADV/spread/ID (DRAFT.md §2).
* Does **not** build the admin hard-disable override endpoint (task §5/§15).
* Does **not** persist any of this in `instruments.json`.
