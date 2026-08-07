# Dynamic Universe — Pre-Enable R2B residuals completion (R2B-P3-1..4)

**Status:** `IMPLEMENTED — awaiting independent review`. NOT resolved. The implementation
session did **not** run the independent review; a separate frozen-scope independent review is
required. The feature remains **disabled** (`enable_dynamic_universe_shadow=False`), the
candidate gate **default-off** (`require_candidate_source=False`), the heat gate **default-off**
(`enforce_portfolio_heat=False`), and the whole subsystem **un-wired** into
`main.py`/`api_server.py`. No deployment, service restart, production migration, production
`universe.db`, broker/provider call, shadow mode, or paper trading is part of this change.

**Base:** `breakout-strategy` @ `54b5641cb02ff66da0c8f35786fe8aba1bf25229` (the merged R2C).
**Branch:** `feature/dynamic-universe-preenable-r2b-residuals`.

## Scope

Implements ONLY the four remaining mandatory R2B pre-enable residuals. No runtime wiring,
scheduler startup, shadow mode, paper trading, broker routing, live providers, or new strategy
behaviour. Frozen risk/precedence/TTL constants are untouched.

## Migration policy

**No schema v8 required.** The schema head stays **v7**. R2B-P3-3 idempotency is enforced by a
check-then-insert under the existing `BEGIN IMMEDIATE` write lock on the v6 `candidate_audit`
table — a partial-unique index was considered and rejected as unnecessary for the
single-writer-per-cycle usage (and to avoid a schema bump). `v1`–`v7` migration definitions are
not edited. No production `universe.db` is created or migrated.

## R2B-P3-1 — candidate-store error observability

- `CandidateStore.effective_candidates` wraps its read in `try/except sqlite3.Error` and, on
  any failure, returns `EffectiveSelection(store_unavailable=True)` instead of propagating.
- The evaluator additionally guards store construction (`migrate`) and the call itself. On a
  read failure it: blocks EVERY new-entry candidate with `candidate_store_unavailable`; records
  NO selection audit; does NOT tick TTL (no mutation); never crashes the cycle. Open positions
  (derived per-instrument) are unaffected and keep being managed.
- The TTL tick and the selection-audit write are individually wrapped so a transient write
  failure is logged and retried next session (TTL counting is idempotent per date) rather than
  aborting the cycle.

## R2B-P3-2 — unknown persisted source / status / malformed ACTIVE row

- New `_candidate_row_malformed` defensive read-path validator runs on the HIGHEST-precedence
  candidate per instrument: an unknown source, a present-but-non-integer TTL, or a
  present-but-unparseable effective/generation date → `candidate_malformed`. A NULL TTL /
  generation date is treated as the pre-existing *expired/stale* (not malformed) case to avoid
  changing existing semantics.
- A row carrying a literal unknown (non-enum) status is caught by a separate defensive pass and
  blocked as `candidate_malformed`, without entering the precedence/suppression path (so it
  never produces a spurious `SUPPRESSED` audit).
- **No fall-through:** a malformed higher-precedence candidate BLOCKS the instrument; selection
  never falls through to a valid lower-precedence candidate. Suppressed siblings are filtered to
  well-formed ACTIVE rows. Identity is never resolved by ticker or symbol.

## R2B-P3-3 — selection audit completeness

- New `CandidateStore.record_selection_audit(trading_date, selection)` persists
  `SELECTED_EFFECTIVE` (per effective candidate) and `SUPPRESSED` (per suppressed candidate,
  `reason_code = suppressed_by_higher_precedence_source`).
- Idempotent per `(candidate_id, trading_date, event_type)` via check-then-insert under one
  `BEGIN IMMEDIATE` transaction; a duplicate same-date evaluation adds no rows. A non-null
  `trading_date` is required (it is part of the idempotency key). Append-only preserved
  (INSERT-only; existing rows never updated/deleted). Called ONLY on the gate-enabled path;
  feature-off writes nothing.

## R2B-P3-4 — transaction consistency and TTL fault coverage

- `deactivate_candidate_atomic` now uses an explicit `BEGIN IMMEDIATE` (status UPDATE +
  append-only audit COMMIT/ROLL BACK together), consistent with `submit_candidate_atomic`,
  `submit_auto_batch_atomic`, and `tick_ttl_atomic`. All multi-row candidate lifecycle writes
  now use `BEGIN IMMEDIATE`.
- `tick_ttl_atomic` gained fault-injection seams (`after_ttl_update`, `after_expiry_update`,
  `after_expiry_audit`, `before_commit`); a fault after any TTL decrement, expiry status flip,
  or EXPIRED audit insert rolls the whole batch back (no partial decrement / orphan audit). The
  connection is discarded on rollback and a retry succeeds cleanly.
- Both `deactivate_candidate_atomic` and `tick_ttl_atomic` accept a `timeout`; a concurrent
  writer either fails cleanly (`OperationalError`, no partial data) or serializes after the lock
  releases.

## Gate order (unchanged) & default-off isolation

The evaluator preserves the existing gate order: identity/listing → broker mapping → candidate
effectiveness (when required) → state/cooldown/reconciliation → FX sizing / portfolio heat →
contention. Candidate-store failures affect NEW-entry eligibility only. With all flags off
(`enable_dynamic_universe_shadow`, `require_candidate_source`, `enforce_portfolio_heat` all
default `False`) the candidate store is never read or written and selection is unchanged.

## Tests

- New `tests/universe/test_r2b_residuals.py` (21 tests) maps the §7 acceptance list 1:1:
  store-unavailable (missing table / corrupt DB → `store_unavailable`; evaluator block-all;
  open-position management); unknown source / unknown status / invalid TTL / invalid effective
  date → `candidate_malformed`; higher-precedence-malformed no-fall-through; `SELECTED_EFFECTIVE`
  / `SUPPRESSED` once-per-date + duplicate-evaluation idempotency + append-only; deactivate /
  TTL-update / TTL-expiry-audit fault rollback + clean retry; concurrent writer fails-cleanly /
  serializes; feature-off zero candidate I/O.
- `tests/universe/test_r2b_selection.py` `_ExplodingCandidateStore` gained a raising
  `record_selection_audit` so the default-off "writes nothing" guarantee covers the new write
  path.
- **Focused:** `pytest tests/universe` → **436 passed** (415 base + 21 new). **No regression.**
- **Full:** `pytest tests` (`-p no:randomly`) → **1768 passed, 4 failed**; all 4 failures are in
  `tests/test_breakout_indicators.py` (the repository's pre-existing missing-`ohlcv` /
  `backtest.db` environment issue — the fresh worktree auto-creates an empty `backtest.db` with
  no `ohlcv` data), unrelated to this residual cleanup. No new failure outside that file.

## Not authorized by this change

Runtime enablement, scheduler/`main.py` wiring, production migration, production `universe.db`,
candidate ingestion, FX/portfolio provider calls, shadow soak, paper trading, or live trading.
These residuals remain **mandatory before runtime enablement** until the independent review
resolves them.
