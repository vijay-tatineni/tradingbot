# Classifier forward-returns evaluation

_Generated: 2026-06-01T12:40:33.076868+00:00_
_Mode: **backfill (90d, step 5)**, forward window: 20 trading days_

## Per-regime forward metrics

| Regime | n | Mean dir. move (ATR) | SE | Mean |move| (ATR) | Mean fwd range eff |
|---|---|---|---|---|---|
| TRENDING | 13 | +0.233 | +0.693 | +2.000 | +0.091 |
| RANGING | 55 | +1.541 | +0.518 | +3.117 | +0.160 |
| UNCLEAR | 128 | +0.342 | +0.281 | +2.421 | +0.210 |

## Effect size: TRENDING vs (RANGING + UNCLEAR)

- n(TRENDING) = 13, n(other) = 183
- Mean directional move difference (TRENDING − other): **-0.470** ATR
- Cohen's d (directional): **-0.139**
- Mean |move| difference: **-0.630** ATR
- Cohen's d (|move|): **-0.280**

_Cohen's d interpretation (rough rule of thumb): 0.2 = small, 0.5 = medium, 0.8 = large._

## Sample summary

- Total samples with forward metrics: **196**

## Cost

- Spend before run (consistency only): $0.5970
- Spend after  run (wall-clock today, all rows): $2.2682
- Cost of this run: **$1.6712** (196 backfill calls)

> **Note (manually corrected after the run):** the script's automated cost
> calculation read $0.0000 because `CostTracker.get_daily_spend(today)` filters
> by the `trading_date` column, not the wall-clock day of the API call. Backfill
> classifications use historical `trading_date` values (the bar being
> classified), so they don't accumulate against today's bucket. The same
> condition means the mid-backfill `is_budget_exceeded(today)` check never
> would have fired against backfill cost. **Real spend numbers above are taken
> from a direct query of `regime_classification_log`.** Budget cap enforcement
> for backfill mode is therefore effectively absent and should be fixed before
> the next backfill run — flagged as a follow-up bug.
