# Dynamic Universe Pre-Enable R2C — FX-Normalized Sizing & Inherited/Open-Book Portfolio Heat

**Status: IMPLEMENTED — awaiting independent review.**
**BLOCKER-S (FX-normalized sizing): IMPLEMENTED — awaiting independent review.**
**P3-5 (inherited/open-book portfolio heat): IMPLEMENTED — awaiting independent review.**

These two blockers are **NOT resolved**. This was an implementation-only session; it did **not**
run the frozen-scope independent review (a separate session will). The feature remains
**disabled** (`enable_dynamic_universe_shadow=False`), the new gate is **default-off**
(`enforce_portfolio_heat=False`), and nothing is wired into `main.py` / `api_server.py`. No
deployment, service restart, production migration, production `universe.db`, shadow mode, paper
trading, or broker/provider call is part of this change. Runtime enablement also remains blocked
by the open R2B residuals **R2B-P3-1..4**.

## Scope
Implements only **BLOCKER-S** (FX-normalized sizing) and **P3-5** (inherited/open-book portfolio
heat). Does NOT implement runtime startup wiring, scheduler activation, shadow mode, paper
trading, broker routing, or live IBKR/IG/EODHD integration.

## What was added
| Area | File | Summary |
|------|------|---------|
| FX-normalized sizing | `bot/universe/sizing.py` (new) | `BaseFxRateProvider` protocol, `FxRate`, `resolve_to_base_fx`, `SizingInputs`/`SizingResult`, `size_position`; deterministic `Decimal`; fail-closed. |
| Open-book heat | `bot/universe/portfolio_heat.py` (new) | `PortfolioRiskProvider` protocol, `PortfolioRiskSnapshot`/`PositionRisk`, `validate_snapshot`, `evaluate_heat`; fail-closed freshness/equity/heat. |
| Combined gate | `bot/universe/risk_gate.py` (new) | `evaluate_entry_risk` (FX→size→snapshot→heat) → `RiskDecision` (also the v7 audit evidence). |
| Schema v7 store | `bot/universe/risk_store.py` (new) | `RiskEvaluationStore.record_evaluation_atomic` (one `BEGIN IMMEDIATE`, content-addressed id, fault seams). |
| Schema v7 | `bot/universe/migrations.py` | Additive `risk_evaluation` + append-only `risk_evaluation_audit` (triggers) + index. v1–v6 unchanged. |
| Reason codes | `bot/universe/models.py` | R2C `fx_*` / `sizing_*` / `portfolio_*` / `open_book_heat_exceeded` codes — **contention-level**, deliberately NOT in `BLOCKING_REASONS`. |
| Params | `bot/universe/params.py` | Additive freshness windows (`MAX_FX_RATE_AGE_SECONDS`=300, `MAX_PORTFOLIO_SNAPSHOT_AGE_SECONDS`/`MAX_OPEN_ORDER_SNAPSHOT_AGE_SECONDS`/`MAX_EQUITY_AGE_SECONDS`=900). FROZEN risk constants untouched. |
| Gate wiring | `bot/universe/evaluator.py` | Default-off `enforce_portfolio_heat` + injected `base_currency` / `base_fx_provider` / `portfolio_risk_provider` / `risk_evaluation_time`. Off → bit-identical, zero provider calls. |
| Tests | `tests/universe/test_r2c_{sizing,heat,gate,persistence}.py`, `_fixtures.py` | FX/sizing, heat, gate integration, schema-v7 atomicity/isolation. |
| Test doubles | `tests/universe/_fixtures.py` | `SpyBaseFxRateProvider`, `SpyPortfolioRiskProvider`, `fresh_snapshot`, `pos_risk` (broker-free, call-recording). |
| Docs | `docs/universe_db_schema.md`, `dynamic_universe_state_transitions_v1.md`, `dynamic_universe_shadow_operations.md`, `dynamic_universe_pre_enable_blockers.md` | v7 schema, gate ordering/reason codes, operations, blocker status. |

The version-pinned migration tests (`test_migrations.py`, `test_r1_migration.py`,
`test_r2a0_1_lifecycle.py`, `test_r2a1_migration.py`, `test_r2b_migration.py`) were updated to
the new schema head **v7** (the standard consequence of an additive migration — identical to the
R2B 5→6 bump); the migration `definitions-unedited` guards now assert the sequence
`[1..7]`, and the `test_r1_migration` rollback test that crafts a *fake future* migration was
moved from a v7 to a v8 tuple to avoid colliding with the now-real v7.

## FX normalization model
- The to-base rate is `BASE units per 1 unit of the INSTRUMENT currency` (e.g. instrument GBP,
  base USD → `fx_pair='GBPUSD'`, `rate=1.25` → 1 GBP = 1.25 USD).
- INJECTED, broker-free `BaseFxRateProvider.get_rate(instrument_currency, base_currency,
  evaluation_time)` returns an `FxRate(instrument_currency, base_currency, rate, as_of, source)`
  or `None`. No IBKR/IG/EODHD/live-FX call.
- Distinct from the USD-eligibility normalization in `bot/universe/fx.py` (that converts
  price/ADV20 to a fixed USD threshold currency; this targets the configurable account base
  currency). `fx.py` is untouched, so eligibility behaviour is unchanged.

## Currency & Decimal policy
- **base_currency is explicit/injected** and never defaulted to USD; unknown base or instrument
  currency → `sizing_currency_unresolved`.
- All money/risk arithmetic uses `decimal.Decimal`. Incoming floats are routed through
  `Decimal(str(x))` (never `Decimal(float)`); NaN/inf → fail-closed.
- Quantity is floored (`ROUND_DOWN`) — conservative, never over-sizes. Base-currency money is
  quantized **UP** (`ROUND_CEILING`) for limit comparisons, so rounding can only tighten a gate.

## Position-sizing semantics (frozen before coding)
A candidate passes sizing only when: entry price known (> 0); stop price or distance known
(> 0); instrument + base currency known; FX fresh-or-same-currency; the floored quantity is a
positive integer that fits **both** the base-currency per-trade risk limit
(`RISK_PER_TRADE × equity`) **and** the base notional cap (`MAX_NOTIONAL_PCT × equity`). Any
missing/stale/invalid input → fail-closed `sizing_*` / `fx_*` code; an instrument that cannot be
sized is ineligible for a new entry.

## Portfolio snapshot freshness policy
INJECTED `PortfolioRiskProvider.snapshot(evaluation_time)` → `PortfolioRiskSnapshot`. Freshness
is enforced against the **injected** `evaluation_time` (the layer never reads the wall clock):
daily `snapshot_date` must equal the evaluation trading date; the snapshot timestamp and each
open-order/pending-intent `observed_at` must be ≤ 15 min old (and never future); equity (when a
%-limit needs it) must be present, > 0, and ≤ 15 min old. Missing/stale/date-mismatched/
currency-incomplete/identity-incomplete snapshot or missing/stale/invalid equity → fail-closed.

## Open-book heat formula
`post_trade_heat_base = existing_open_position_risk + open_order/pending_intent_risk +
proposed_new_trade_risk`, all normalized to base currency, compared against
`MAX_PORTFOLIO_HEAT × base-currency equity` (or an explicit absolute base limit). Inherited book
already over the limit → `open_book_heat_exceeded`; a proposed entry that crosses it →
`portfolio_heat_exceeded`. Heat gates NEW entries only — never forces liquidation, never alters
an existing position.

## New-entry gate ordering & interaction with existing gates
Candidate presence remains necessary but not sufficient. R2C does NOT bypass hard-disabled
state, reconciliation, cooldown, canonical identity, listing/IBKR-mapping verification, the
candidate store, eligibility, or contention. Order: identity/listing → broker mapping →
candidate effective → existing state/cooldown/reconciliation → **FX sizing** → **open-book heat**
→ contention (slot/sector). The R2C reason codes are contention-level (a `rejected_reason`), not
`BLOCKING_REASONS` entries, so structural eligibility semantics are unchanged.

## Open-position management proof
The gate runs only on ENTRY_ELIGIBLE + entry-signal candidates inside `_apply_contention`.
Open-position states (POSITION_OPEN / EXIT_ONLY / POSITION_RECONCILIATION) are derived from the
per-instrument transition, are counted in `open_now`, and are unaffected by the gate
(`test_r2c_gate.py::test_open_position_still_managed_when_gate_blocks_new_entries`).

## Fail-closed reason-code matrix
See `docs/dynamic_universe_state_transitions_v1.md` (R2C section) for the full table. Summary:
FX → `fx_rate_missing`/`_stale`/`_invalid`/`fx_currency_mismatch`/`fx_provider_unavailable`/
`sizing_currency_unresolved`; sizing → `sizing_price_missing`/`_stop_missing`/`_input_missing`/
`_risk_exceeds_limit`/`_notional_exceeds_limit`/`_quantity_invalid`; heat → `portfolio_snapshot_
missing`/`_stale`/`_date_mismatch`/`portfolio_open_order_snapshot_stale`/`portfolio_currency_
unresolved`/`portfolio_identity_unresolved`/`portfolio_equity_missing`/`_stale`/`_invalid`/
`open_book_heat_exceeded`/`portfolio_heat_exceeded`.

## Schema v7
Additive, forward-only, atomic (single `BEGIN IMMEDIATE`), idempotent, fail-closed. Tables
`risk_evaluation` + append-only `risk_evaluation_audit` (triggers) + `ix_risk_eval_instrument_
date`. Money stored as canonical Decimal strings; only non-sensitive evidence (deterministic
`fx_rate_id` / `portfolio_snapshot_hash`) — no credentials/account ids/tokens/raw payloads. The
default contention path writes nothing; the store is opt-in evidence. No production `universe.db`
is created or migrated.

## Tests
- `tests/universe/test_r2c_sizing.py` (21): same-currency=1, cross-currency, every FX fail-closed
  mode, no-default-USD / no-default-1.0, base-currency risk/notional, floored quantity,
  conservative rounding, deterministic Decimal, missing/invalid price/stop/limit blocks.
- `tests/universe/test_r2c_heat.py` (20): positions/open-orders/pending-intents counted, proposed
  added, over-limit / open-book blocks, missing/stale/date-mismatch snapshot, open-order > 15 min,
  missing/invalid/stale equity, currency/identity completeness.
- `tests/universe/test_r2c_gate.py` (11): combined gate FX-missing/heat-exceeded/all-pass,
  evaluator wiring allow/block, candidate-absent dominance, open-position management, and
  **feature-disabled → zero FX/portfolio provider calls**.
- `tests/universe/test_r2c_persistence.py` (9): v7 atomicity (fault at each seam → full rollback,
  clean retry), append-only audit, idempotent replay, canonical-Decimal money, no production DB
  touched.

## Test results
- Focused: `pytest tests/universe` → **415 passed** (was 354 at base; +61 R2C).
- Full suite (deterministic order, `-p no:randomly`): `pytest tests` → **1748 passed, 3 failed**.
  All 3 failures are confined to `tests/test_breakout_indicators.py::test_warmup_date_matches_
  audit[...]`. Root cause (measured, not inferred): that test does
  `sqlite3.connect("backtest.db")` (a relative path) and reads an `ohlcv` table. A fresh git
  worktree does NOT contain the gitignored, locally-populated `backtest.db`, so the read raises
  `pandas.errors.DatabaseError: ... no such table: ohlcv` — the repository's pre-existing
  missing-fixture/environment issue (it surfaces in this test as a downstream
  `TypeError: Cannot index by location index with a non-integer key` on the empty result).
  Proof it is environmental and unrelated to R2C:
  (a) R2C's diff touches NOTHING under `backtest/` or `tests/test_breakout_indicators.py`;
  (b) at base `breakout-strategy @ a971cbf` (which has the populated `backtest.db`) the full
  suite is **1690 passed, 0 failed** and the breakout file alone is **8 passed**;
  (c) copying the populated `backtest.db` into the worktree makes the breakout file **8 passed**
  there too. No new failure occurs OUTSIDE `tests/test_breakout_indicators.py`.

## Known limitations
- Within-run heat accumulation across multiple candidates selected in the SAME shadow run is
  still handled by the legacy float accumulator; the R2C snapshot is authoritative for the
  INHERITED book only (documented in the transitions doc).
- The optional v7 evidence store is not invoked by the default contention path (it is opt-in);
  wiring an evidence write into a shadow run is deferred to a runtime-enablement change.
- Runtime startup wiring, scheduler, shadow mode, and paper/live trading remain out of scope and
  blocked.
