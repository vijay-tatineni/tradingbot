"""Frozen-param drift guard: the universe risk/exit constants must equal the
frozen breakout simulator's, so the shadow never silently diverges from the
deployed/frozen breakout mechanics."""
from backtest import breakout_sim
from bot.universe import params


def test_risk_params_match_breakout_sim():
    assert params.RISK_PER_TRADE == breakout_sim.RISK_PER_TRADE
    assert params.MAX_NOTIONAL_PCT == breakout_sim.MAX_NOTIONAL_PCT
    assert params.MAX_OPEN_POSITIONS == breakout_sim.MAX_OPEN_POSITIONS
    assert params.MAX_PORTFOLIO_HEAT == breakout_sim.MAX_PORTFOLIO_HEAT
    assert params.INITIAL_STOP_ATR_MULT == breakout_sim.INITIAL_STOP_ATR_MULT
    assert params.TRAIL_ATR_MULT == breakout_sim.TRAIL_ATR_MULT


def test_frozen_universe_values():
    # The operator-frozen v1 values (must not be tuned from outcomes).
    assert params.CANDIDATE_TTL_SESSIONS == 5
    assert params.MIN_HISTORY_BARS == 250
    assert params.MIN_PRICE_USD == 10.0
    assert params.MIN_ADV20_USD == 20_000_000.0
    assert params.ENTRY_HYSTERESIS_PASSES == 2
    assert params.REMOVAL_HYSTERESIS_FAILS == 2
    assert params.COOLDOWN_SESSIONS == 3
    assert params.MAX_POSITIONS_PER_SECTOR == 2
    assert params.CANDIDATE_SOURCES == ("AUTO", "TTI", "MANUAL")


def test_candidate_sort_key_priority():
    # ADV20 desc, spread asc, canonical id asc.
    rows = [
        {"canonical_instrument_id": "US_B", "adv20": 100, "spread": 0.1},
        {"canonical_instrument_id": "US_A", "adv20": 200, "spread": 0.2},
        {"canonical_instrument_id": "US_C", "adv20": 200, "spread": 0.1},
    ]
    ordered = [r["canonical_instrument_id"] for r in sorted(rows, key=params.candidate_sort_key)]
    # highest ADV20 first; tie broken by lower spread; then id
    assert ordered == ["US_C", "US_A", "US_B"]
