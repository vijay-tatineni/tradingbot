# Regime Filter Experiment

Start date:            2026-06-01
Planned end:           2026-06-15 (min) to 2026-06-29 (full)
Branch:                claude-strategy
Commit at filter-live: 6bd51b8621fd2de499353209eb87f289f604a845
Config hash:           190fe49565119d50ddd274edb8a92d887188d8e84ab3931e240cd11f90e90326

Enabled instruments (14): SGLN, SSLN, TSM, AVGO, ANET, SU, SCCO, ANTO, PLTR, NBIS, AAPL, MSFT, BARC, NVTS

Instrument modifications:
  - ANET: allow_new_entries=false (no edge, exits only) — 2026-06-02
  - NBIS: experiment=true (marginal, kept in experiment) — 2026-06-02
  - LUNR/MNTS/RDW: drift, discarded, never activated

Feature flags:         enable_regime_filter_live=true (others SAFE_DEFAULTS)
Layers included:       Layer 1 only (Layer 2/3 reported separately if at all)

## FROZEN deterministic baseline (pre-registered before comparison — do not tune after results)

Rationale: Claude's classifier is direction-agnostic, runs daily per instrument
before any BUY/SELL signal exists. Direction is applied later by triple-confirmation,
identically for both Claude and the rule baseline. The baseline must therefore also
be direction-agnostic and classify at the same pipeline point, so the comparison
isolates judgment-vs-mechanical, not direction-aware-vs-agnostic.

ma_200_slope_pct_per_day unit: PERCENTAGE POINTS
  Computation: _ma_slope returns slope / mean_ma * 100 (bot/regime/features.py), i.e.
  percent-per-day. Confirmed empirically from regime_classification_cache stored
  values: SGLN=0.1476, SSLN=0.3137, ANTO=0.3064, TSM=0.2673, AVGO=0.1981 — all in
  the 0.15–0.31 range (percentage points), not 0.0005–0.003 (decimal return). A 0.31
  value means MA200 is rising ~0.31% per day. (A decimal-return unit would store ~0.003.)
Threshold chosen accordingly: 0.05

PRIMARY baseline v1 (direction-agnostic, daily, same compute_regime_features inputs as Claude):
  TRENDING if:
    adx_14 > 25
    AND range_efficiency > 0.5
    AND abs(ma_200_slope_pct_per_day) > 0.05
  RANGING if:
    adx_14 < 20 AND range_efficiency < 0.3
  UNCLEAR otherwise

SECONDARY sensitivity baseline (non-primary, robustness check only):
  Same as primary, but range_efficiency > 0.4 for TRENDING.

Decision rule:
  - Compare Claude against the PRIMARY 0.5 baseline.
  - Report the 0.4 baseline only as a sensitivity check.
  - Do NOT choose whichever baseline looks better after results.
  - Do NOT make the rule direction-aware — that would give the rule signal-direction
    information Claude does not have at classification time.

Primary metric:        After-cost expectancy delta (filtered vs unfiltered Layer 1)
Secondary metrics:     Win rate, median trade, max drawdown, per-symbol breakdown,
                       Claude-vs-rules 2x2 matrix (allow/block × allow/block)

Sample-size verdict levels:
  <30 closed shadow trades:  no conclusion, dashboard only
  30-60:                     weak directional evidence
  60-150:                    usable evidence
  150+:                      portfolio-level evidence

Stop conditions (abort early): operational failure (phantom positions, broker desync,
  config guardrail halt) — NOT short-term P&L noise

Promotion criteria (ALL must hold):
  1. Layer 1 has positive after-cost expectancy (proven in Phase 2)
  2. Regime filter improves that expectancy
  3. Claude beats the frozen PRIMARY deterministic baseline

Abandonment criteria (ANY triggers):
  1. Layer 1 negative after-cost expectancy → kills the whole strategy
  2. Filter does not improve outcomes → drop regime layer, keep Layer 1
  3. Claude ≈ frozen rules → drop Claude, keep cheaper deterministic filter

Known contamination / mid-experiment changes:
  - 2026-06-02: ANET exits-only, NBIS experimental marker, no-edge guardrail wired
  - Shadow P&L includes "trending but against-trend" entries (e.g. a long in a
    TRENDING-down market), since the filter is direction-agnostic. Account for this
    when reading the comparison.
