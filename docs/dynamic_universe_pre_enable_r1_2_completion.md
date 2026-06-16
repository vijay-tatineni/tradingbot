# Dynamic Universe — Pre-Enable Remediation, Phase R1.2 Completion

> **Branch:** `feature/dynamic-universe-preenable-r1-fix2`, from
> `feature/dynamic-universe-preenable-r1-fix1 @ 5c0911992b8b3ac9c97b32d8b91c8fb42257e23d`
> (which is based on `breakout-strategy @ 46b8f2571f32cf1deec71688cad230445bd25bdd`).
> **Scope:** exactly the three **P2 pre-enable** findings from the independent R1/R1.1 review
> — **P2-A, P2-B, P2-C**. Nothing else.
>
> **STATUS — RESOLVED FOR DEFAULT-OFF / UN-WIRED MERGE** (consolidated R1–R1.3 review,
> `R1_SERIES_APPROVED_FOR_DISABLED_MERGE`; P0/P1/P2 = 0). P2-A, P2-B, P2-C are cleared for
> merging while the feature remains default-off, un-wired, and not migrated in production.
>
> > This status does not authorize runtime enablement, scheduler wiring, production migration,
> > shadow soak, paper trading, live trading, or Phase R2.
>
> Not a claim that all pre-enable work is complete: residuals P3-R1-A/B/C remain OPEN —
> mandatory before runtime enablement. P3-4 / P3-5 / P3-6 / P3-7 / BLOCKER-S (FX-normalized
> sizing) remain OPEN for R2. See `docs/dynamic_universe_pre_enable_blockers.md`.

## The three corrections

### P2-A — historical replay rejected after the current state legitimately advanced
- **Defect (review):** `Registry._reconcile_duplicate` raised `StateHistoryConsistencyError`
  whenever the current-state row's `evaluated_trading_date` differed from the replayed history
  date. Replaying the exact Day-D transition after later evaluations advanced the state to
  D+2 therefore failed instead of being an idempotent no-op (fail-closed, but contrary to the
  contract; the specific reason P3-3 could not be cleared).
- **Fix (`bot/universe/registry.py`):** the immutable, append-only history row is treated as
  the authoritative record of the key's transition. The reconciliation regime is chosen by
  the ordering of the current-state evaluated date vs the replayed history date:
  - **equal** → full comparison (history fields AND current-state lifecycle/cooldown/
    authoritative markers): identical ⇒ idempotent no-op; divergent ⇒ `TransitionConflictError`;
    stored `current_state` ≠ stored history `new_state` ⇒ `StateHistoryConsistencyError`.
  - **later** (state legitimately advanced) → compare only the immutable history content:
    identical ⇒ idempotent no-op; divergent ⇒ `TransitionConflictError`. The current-state row
    is **not** required to still equal the history `new_state`.
  - **earlier**, or current-state row missing, or no evaluated date ⇒
    `StateHistoryConsistencyError` (impossible ordering / broken pair).
- **Bounded limitation (documented):** cooldown bookkeeping is not folded into the history
  feature-hash, so a cooldown-only divergence on an already-advanced replay (identical state
  label and hash) is not re-flagged in the advanced regime; the same-date regime compares it
  in full.

### P2-B — v3 migration lost the authoritative open anchor at the migration boundary
- **Defect (review):** the v3 back-fill set `position_reconciliation_required=1` only for
  `last_observed_position_status='UNKNOWN'` rows. A v2 row OPEN at the boundary
  (`last_observed_position_status='POSITION_OPEN'`) migrated to `last_authoritative=NULL,
  recon=0` — the open anchor was lost, so a later evidence-bearing close was treated as an
  ordinary flat and bypassed cooldown (the exact class R1.1 fixed).
- **Fix (`bot/universe/migrations.py`, v3 corrected IN PLACE):** the back-fill derives the
  authoritative anchor from the v2 `last_observed_position_status`, using
  `evaluated_trading_date` as documented observation provenance:
  - `POSITION_OPEN` → `last_authoritative_position_status='POSITION_OPEN'` (+ id-hash +
    provenance); if no provenance date → `position_reconciliation_required=1`.
  - `NO_POSITION` / `POSITION_EXITED` / `POSITION_EXITED_TODAY` → clean `NO_POSITION` anchor,
    non-blocked (a v2 row resting at `NO_POSITION` carries no dangling-open signal).
  - `UNKNOWN`, NULL/missing, or any unrecognised status → `position_reconciliation_required=1`,
    never inferred flat.
- **Migration-version decision:** **corrected v3 in place** rather than adding a v4. The repo
  policy (`migrations.py` docstring) forbids editing a *released* migration; v3 is unreleased
  — present only on the in-review branch, never merged/deployed/run in production — so editing
  it is permitted and is the task's stated preference. (Any DB already at v3 would not re-run
  the corrected back-fill, which is fine because no such DB exists.) The migration remains
  strictly additive and atomic; no schema v4 is introduced.
- **Conscious documented default:** if R1 ran without a position provider, every v2 row is
  `UNKNOWN`, so the whole universe is conservatively blocked for reconciliation on upgrade —
  the intended fail-safe; each instrument unblocks on its next authoritative snapshot. No
  production migration is run by this change.

### P2-C — `EXIT_ONLY` overloaded for position uncertainty
- **Defect (review):** the `position_reconciliation_required` block and the `UNKNOWN`/
  non-authoritative case were both surfaced as `State.EXIT_ONLY`, which elsewhere implies a
  position definitely exists — future consumers keying on `EXIT_ONLY` could wrongly assume a
  position to manage/exit.
- **Fix (`bot/universe/models.py`, `state_machine.py`, `evaluator.py`):** a dedicated
  `State.POSITION_RECONCILIATION` now represents position *uncertainty*. The state machine maps
  **both** `reconciliation_required` (durable flag, reason `position_reconciliation_required`)
  **and** `position_unknown` (transient, reason `position_status_unknown`) to it. `EXIT_ONLY`
  is reserved strictly for an **authoritatively-open** position (current authoritative snapshot
  is `POSITION_OPEN`) with entries suppressed.
  - Crucially, `position_unknown` drives the STATE only; it does **not** set the durable
    `position_reconciliation_required` FLAG (a transient unknown must never stick). The
    evaluator persists the flag solely from an authoritative unsupported open→flat — so an
    `UNKNOWN → clean-authoritative-flat` with no prior open is not stuck blocked.
  - `POSITION_RECONCILIATION` blocks all new entries, asserts nothing about whether a position
    exists, never forces liquidation, and never decrements cooldown. It clears only on an
    authoritative `POSITION_OPEN` (→ `POSITION_OPEN`) or an evidence-bearing close (→ `COOLDOWN`,
    exactly once).
  - **Contention:** `POSITION_RECONCILIATION` conservatively counts as occupying a slot/sector
    (the position *may* exist), preserving the pre-R1.2 behaviour where the overloaded
    `EXIT_ONLY` reserved a slot.
- **Consumer review:** the only runtime consumers of `EXIT_ONLY` are the state machine
  (producer) and the contention slot/sector set in `evaluator._apply_contention` (updated). No
  consumer now needs to handle reconciliation uncertainty via `EXIT_ONLY`.

## R1.1 safety behaviour preserved (re-verified)

`OPEN→UNKNOWN→OPEN` ⇒ POSITION_OPEN, no cooldown · `OPEN→UNKNOWN→NO_POSITION+evidence` ⇒
COOLDOWN remaining 3 · `OPEN→UNKNOWN→NO_POSITION` without evidence ⇒ POSITION_RECONCILIATION ·
`UNKNOWN→NO_POSITION` with no prior open ⇒ no manufactured exit · bare `position_id` ⇒ not
closure evidence · same close-event replay ⇒ no cooldown reset · new later close ⇒ new cooldown.

## Tests & probes

- `pytest tests/universe` → **202 passed** (187 prior + 15 new R1.2 tests).
- `pytest tests` → only `tests/test_breakout_indicators.py` fails (4), identically to
  `breakout-strategy @ 46b8f257` — pre-existing, unrelated.
- New tests: P2-A replay/conflict/consistency + a `POSITION_RECONCILIATION` fault-injection
  rollback test (`test_atomic_persistence.py`), P2-B migration fixtures incl. the OPEN-boundary
  anchor + end-to-end close→cooldown (`test_r1_migration.py`), P2-C reconciliation-state
  behaviour + `EXIT_ONLY`-requires-authoritative-open (`test_state_machine.py`,
  `test_position_continuity.py`), and `POSITION_RECONCILIATION` reached organically in the
  offline rehearsal.
- Explicit §8 probes (Day-D-replay-after-advance, divergent-key conflict, v2 `POSITION_OPEN`
  and `UNKNOWN` migration, OPEN→UNKNOWN→unsupported-flat, reconciliation→OPEN,
  reconciliation→close, close-replay-no-reset) all pass.

## Out of scope (unchanged)

P3-4 candidate-source integration · P3-5 inherited/open-book heat · P3-6 canonical identity
redesign · P3-7 IBKR mapping verification · BLOCKER-S FX-normalized sizing — all remain OPEN
for Phase R2. No runtime wiring, no enablement, no production DB/migration, no service action.
