"""
backtest/config.py — Default configuration for walk-forward testing.
"""

# Default walk-forward settings
DEFAULT_TRAIN_MONTHS = 6
DEFAULT_TEST_MONTHS = 3
LOOKBACK_YEARS = 2
MIN_TRADES_PER_WINDOW = 5

# Parameter grid (stop% and TP% combinations to test)
PARAM_GRID = {
    "trail_stop_pct": [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0],
    "take_profit_pct": [3.0, 4.0, 5.0, 8.0, 10.0, 12.0, 15.0, 20.0],
}

# IBKR connection for data download
BACKTEST_CLIENT_ID = 99  # Avoids conflict with running bot

# Walk-forward efficiency thresholds
WF_ROBUST_THRESHOLD = 0.5
WF_MARGINAL_THRESHOLD = 0.3

# Data freshness threshold for --skip-download
DATA_FRESHNESS_HOURS = 24

# Indicator grid for deep optimisation (Phase 1)
INDICATOR_GRID = {
    "rsi_period": [7, 14, 21],             # 3 values
    "rsi_oversold": [30, 35],              # 2 values
    "rsi_overbought": [65, 70],            # 2 values
    "williams_r_period": [10, 14],         # 2 values
    "adx_threshold": [15, 20, 25],         # 3 values
    "ma200_period": [150, 200],            # 2 values
}
# Total: 3 × 2 × 2 × 2 × 3 × 2 = 144 combos

# Fixed indicator settings (not grid-searched)
INDICATOR_FIXED = {
    "adx_period": 14,
    "williams_r_mid": -50,
    "williams_r_oversold": -80,
    "williams_r_overbought": -20,
    "alligator_min_gap_pct": 0.003,
}

# Number of top indicator combos to keep from Phase 1
TOP_N_INDICATOR_COMBOS = 5
