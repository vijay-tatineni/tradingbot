# Pre-Enable R2A-0 — completion (P3-R1-A / P3-R1-B / P3-R1-C)

> **Status:** `IMPLEMENTED — awaiting independent review`. Default-off, un-wired,
> broker-free, provider-injected, additive. Branch
> `feature/dynamic-universe-preenable-r2a0` from `breakout-strategy @ 550fa625`.
> **This is NOT a resolved/enablement sign-off.** The three residuals remain OPEN until an
> independent review confirms them; nothing here authorizes runtime enablement, scheduler
> wiring, production migration, shadow soak, paper/live trading, or Phase R2A-1+.

R2A-0 is the first reviewable tranche of the frozen Phase R2 plan (see
`docs/dynamic_universe_pre_enable_blockers.md` and the operator-ratified R2 design). It
closes the three consolidated R1–R1.3 pre-enable residuals and nothing else. The larger R2
blockers (P3-6 identity, P3-7 verified mappings, P3-4 candidates, P3-5 heat, BLOCKER-S FX
sizing) are deliberately **out of scope** here and untouched.

## Scope delivered

### P3-R1-A — reused explicit provider `close_event_id` across lifecycles
- **Risk closed:** a provider that reused the same explicit `close_event_id` across two
  distinct open→close lifecycles was interpreted as a replay, masking the second genuine
  close → cooldown bypass.
- **Design (operator-frozen):** the explicit/synthetic close-event id is now bound to its
  **lifecycle discriminator** — canonical instrument id + hashed position id + opened
  trading date — as a persisted *qualified key* (`universe_state.last_close_event_key`,
  additive migration **v4**). On a close:
  - same explicit id **+ same** discriminator → replay (no cooldown reset);
  - same explicit id **+ different** discriminator → **provider-contract violation** →
    `POSITION_RECONCILIATION` → entry blocked, **no cooldown manufactured**, authoritative
    anchor kept at `POSITION_OPEN` (the unverified flat is not trusted).
  A synthetic id already bakes the discriminator into the id, so the qualified key only adds
  protection on the provider-controlled explicit-id path.
- **Persistence:** the qualified key is written atomically with the rest of the transition
  via `persist_transition_atomic` and folded into the complete transition snapshot/hash.
- **Contract documented:** `PositionSnapshot.close_event_id` now documents that a provider
  `close_event_id` MUST be globally unique per close lifecycle.
- **Files:** `bot/universe/evaluator.py` (`_close_event_key`, `_position_continuity`),
  `bot/universe/registry.py` (`_STATE_COLUMNS`, `_TRANSITION_STATE_FIELDS`),
  `bot/universe/migrations.py` (v4), `bot/universe/models.py` (contract docs).

### P3-R1-B — legacy NULL `transition_snapshot_hash`
- **Frozen policy (simpler and safer):** an **advanced** replay (current state has moved
  past the replayed date) of a history row whose `transition_snapshot_hash` is NULL now
  **fails closed** with `StateHistoryConsistencyError`. The incomplete immutable content is
  never trusted or inferred. A **same-date** replay is unaffected — the current-state row
  still reflects the transition, so the full constituent comparison remains available and
  safe.
- **No new NULL rows:** `append_history` now computes and stores the complete deterministic
  transition snapshot + hash (like `persist_transition_atomic`), so no supported
  history-writing API can create a new NULL-hash row.
- **No backfill:** legacy/raw NULL-hash rows are left in place and handled by the fail-closed
  guard — no guessed hashes.
- **Files:** `bot/universe/registry.py` (`_reconcile_duplicate`, `append_history`).

### P3-R1-C — remaining test completeness
Added `tests/universe/test_r2a0_pre_enable.py` (9 tests, no runtime behavior change — pure
assertions over the paths above):
- explicit `close_event_id` reused across distinct lifecycles → contract violation /
  reconciliation (unit + integration), plus a same-lifecycle replay guard (no false positive);
- NULL-hash **advanced** replay → fail closed; NULL-hash **same-date** replay → full
  comparison (idempotent no-op + divergent-conflict); `append_history` writes a non-NULL hash;
- every transition-hash **material field** (history- and state-derived, including the new
  `last_close_event_key`) changes the hash;
- bare `POSITION_EXITED` and bare `POSITION_EXITED_TODAY` (no open date, no close id) →
  ambiguous identity → reconciliation, no cooldown.

## Migration

- **v4 (additive, forward-only):** `ALTER TABLE universe_state ADD COLUMN
  last_close_event_key TEXT`. The reviewed **v1–v3 chain is not edited**.
- **Superseded by R2A-0.1 (independent-review Finding 3):** the original R2A-0 v4 carried *no*
  back-fill, which left a `v3→v4`-upgraded row with a processed event but a NULL key unable to
  participate in the reuse-violation check (a reachable masking on a non-fresh upgrade). R2A-0.1
  corrects this: the v4 migration now sets `position_reconciliation_required = 1` for any row with
  `last_processed_position_event_id IS NOT NULL AND last_close_event_key IS NULL` (fail closed).
  No production `universe.db` currently exists, but **v3→v4 now fails closed for imported,
  rehearsal, restored, or future pre-v4 databases** — not merely a fresh DB. See
  `docs/dynamic_universe_pre_enable_r2a0_1_completion.md`.

## Tests

- `pytest tests/universe` → **230 passed** (221 prior + 9 new).
- `pytest tests` → **4 failed, 1562 passed**; the 4 failures are the pre-existing
  `tests/test_breakout_indicators.py` ambient-DB isolation baseline (identical at
  `breakout-strategy @ 550fa625`). **No new failure outside that file.**
- The migration-head version pin (`HEAD_VERSION`) and the v3-era `assert migrate(db)==3`
  pins were mechanically updated to **4** to track the additive v4 (no R1 logic re-reviewed).

## Posture (unchanged)

- `enable_dynamic_universe_shadow` remains **False** by default; absent from live config.
- `bot.universe` remains **un-wired** — not imported by `main.py`/`api_server.py`; no
  scheduler started.
- **Broker-free:** no IBKR/IG/EODHD call; all providers (bars/position/fx) are injected stubs
  in tests.
- Deterministic / idempotent / auditable / fail-closed throughout.

## Status

`P3-R1-A`, `P3-R1-B`, `P3-R1-C`: **IMPLEMENTED — awaiting independent review** (NOT resolved).
