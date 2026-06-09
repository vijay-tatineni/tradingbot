"""Dynamic Universe v1 — structural eligibility (pure predicate).

Evaluates the operator-frozen structural checks (task §7) on a resolved snapshot of
SCALAR facts. The evaluator derives those scalars from bars (via the frozen
backtest.breakout_strategy indicators); keeping this a pure scalar predicate makes
it trivially testable and free of any data/broker dependency.

If corporate-action data is unavailable, a reason code is recorded — eligibility is
NEVER silently assumed correct. Unknown sector is recorded (non-blocking) so the
sector cap can reason about it downstream rather than passing silently.
"""
from bot.universe import params
from bot.universe.models import BLOCKING_REASONS, EligibilityResult, Reason


def structural_eligibility(snapshot: dict) -> EligibilityResult:
    """snapshot scalar keys (all optional; missing → conservative fail):
        bar_count (int)              valid completed daily bars
        fresh_bar (bool)             a fresh completed bar exists
        ohlc_valid (bool)            last bar OHLC is internally valid
        indicators_available (bool)  SMA50/200, ATR14, ADX14, high20 all defined
        price (float)                last completed close (local ccy)
        adv20_usd (float)            20-day average dollar volume (USD-equiv)
        research_mapping_ok (bool)
        ibkr_mapping_ok (bool)
        cooldown_remaining (int)
        corp_action_status (str)     'ok' | 'unavailable' | 'anomaly'
        sector (str | None)
    """
    reasons = []

    if int(snapshot.get("bar_count", 0)) < params.MIN_HISTORY_BARS:
        reasons.append(Reason.INSUFFICIENT_HISTORY)

    if not snapshot.get("fresh_bar", False):
        reasons.append(Reason.STALE_BAR)

    if not snapshot.get("ohlc_valid", False):
        reasons.append(Reason.INVALID_OHLC)

    if not snapshot.get("indicators_available", False):
        reasons.append(Reason.INDICATORS_UNAVAILABLE)

    price = snapshot.get("price")
    if price is None or float(price) < params.MIN_PRICE:
        reasons.append(Reason.PRICE_BELOW_MIN)

    adv20 = snapshot.get("adv20_usd")
    if adv20 is None or float(adv20) < params.MIN_ADV20_USD:
        reasons.append(Reason.ADV20_BELOW_MIN)

    if not snapshot.get("research_mapping_ok", False):
        reasons.append(Reason.RESEARCH_MAPPING_MISSING)

    if not snapshot.get("ibkr_mapping_ok", False):
        reasons.append(Reason.IBKR_MAPPING_MISSING)

    if int(snapshot.get("cooldown_remaining", 0)) > 0:
        reasons.append(Reason.IN_COOLDOWN)

    corp = snapshot.get("corp_action_status", "unavailable")
    if corp == "unavailable":
        reasons.append(Reason.CORP_ACTION_DATA_UNAVAILABLE)
    elif corp == "anomaly":
        reasons.append(Reason.CORP_ACTION_ANOMALY)

    if not snapshot.get("sector"):
        reasons.append(Reason.SECTOR_UNKNOWN)  # informational, non-blocking

    passes = not any(r in BLOCKING_REASONS for r in reasons)
    if passes:
        reasons.append(Reason.ELIGIBLE)
    return EligibilityResult(passes=passes, reason_codes=reasons)
