"""Frozen Dynamic Universe v1 parameters (shadow foundation).

These values are FROZEN for the shadow implementation (operator-approved). They are
NOT tuned from outcomes. The risk/sizing constants intentionally mirror the frozen
breakout simulator (backtest/breakout_sim.py); a test asserts they have not drifted
(tests/universe/test_params_consistency.py).

Nothing here computes PF / Sharpe / returns / rankings — these are structural
universe-construction and (hypothetical) sizing parameters only.
"""

# ── Candidate sources ────────────────────────────────────────────────
CANDIDATE_SOURCES = ("AUTO", "TTI", "MANUAL")

# TTI / MANUAL candidate time-to-live, in completed sessions.
CANDIDATE_TTL_SESSIONS = 5

# ── Structural eligibility thresholds ────────────────────────────────
MIN_HISTORY_BARS = 250          # minimum valid completed daily bars
# USD-denominated thresholds. The evaluator converts each instrument's local price /
# ADV20 to USD-normalised values (bot.universe.fx) BEFORE comparing them here — a local
# value is never compared directly against a USD threshold (P2-2).
MIN_PRICE_USD = 10.0            # minimum USD-normalised last close ($10)
MIN_ADV20_USD = 20_000_000.0   # minimum USD-normalised 20-day average dollar volume

# ── Eligibility hysteresis / cooldown (completed sessions) ───────────
ENTRY_HYSTERESIS_PASSES = 2     # consecutive passing sessions to become ENTRY_ELIGIBLE
REMOVAL_HYSTERESIS_FAILS = 2    # consecutive failing sessions for ordinary removal
COOLDOWN_SESSIONS = 3           # post-exit cooldown

# ── Portfolio caps ───────────────────────────────────────────────────
MAX_OPEN_POSITIONS = 5
MAX_POSITIONS_PER_SECTOR = 2

# ── Risk / sizing (FROZEN — mirror backtest/breakout_sim.py) ─────────
RISK_PER_TRADE = 0.005          # 0.50% of equity
MAX_NOTIONAL_PCT = 0.20         # 20% of equity per instrument
MAX_PORTFOLIO_HEAT = 0.025      # 2.50% aggregate initial-stop risk
INITIAL_STOP_ATR_MULT = 2.0
TRAIL_ATR_MULT = 3.0

# ── Candidate priority when slots are scarce (deterministic) ─────────
# 1. ADV20 descending  2. estimated spread ascending  3. stable canonical id asc
def candidate_sort_key(row: dict):
    """Deterministic priority key (lower sorts first → use with reverse=False).

    row: {'adv20': float, 'spread': float, 'canonical_instrument_id': str}
    ADV20 descending (negate), spread ascending, canonical id ascending.
    """
    return (
        -float(row.get("adv20") or 0.0),
        float(row.get("spread") if row.get("spread") is not None else float("inf")),
        str(row.get("canonical_instrument_id") or ""),
    )


# ── Frozen breakout/exit reference (logic lives in backtest/breakout_strategy.py) ──
# Entry (all true on a completed bar):
#   close > preceding-20-day-high(excl current) AND close > SMA200
#   AND SMA50 > SMA200 AND ADX14 > 25
# Exit:
#   initial stop = entry_fill - 2 * signal-bar Wilder ATR14
#   trailing stop = highest completed close - 3 * current Wilder ATR14 (monotonic)
#   trend break   = completed close < SMA50
BREAKOUT_ADX_THRESHOLD = 25.0
