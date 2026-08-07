"""Ratchet-monotonicity (§2 trailing stop). The active stop may only rise or
hold — never fall, even when ATR spikes."""
import pandas as pd

from backtest.breakout_sim import BreakoutPosition, TRAIL_ATR_MULT, INITIAL_STOP_ATR_MULT
from backtest.simulator import CostConfig
from tests.breakout_helpers import make_inst, bar


def _pos(entry_fill=100.0, signal_bar_atr=5.0):
    inst = make_inst("X", 0, [bar("2025-01-02", 100, 100, 100, 100)])
    return BreakoutPosition.open(inst, entry_idx=0, entry_fill=entry_fill, qty=10,
                                 signal_bar_atr=signal_bar_atr, stop_distance_usd=10.0)


def test_initial_stop_is_entry_minus_2atr():
    pos = _pos(entry_fill=100.0, signal_bar_atr=5.0)
    assert pos.initial_stop == 100.0 - INITIAL_STOP_ATR_MULT * 5.0
    assert pos.active_stop == pos.initial_stop


def test_ratchet_rises_with_price():
    pos = _pos(100.0, 5.0)          # initial_stop = 90
    s1 = pos.ratchet_at_close(bar("2025-01-03", 0, 0, 0, 110, atr14=5))
    s2 = pos.ratchet_at_close(bar("2025-01-04", 0, 0, 0, 120, atr14=5))
    assert s1 == 110 - TRAIL_ATR_MULT * 5      # 95
    assert s2 == 120 - TRAIL_ATR_MULT * 5      # 105
    assert s2 > s1 > pos.initial_stop


def test_ratchet_never_falls_on_atr_spike():
    pos = _pos(100.0, 5.0)
    pos.ratchet_at_close(bar("2025-01-03", 0, 0, 0, 120, atr14=5))   # -> 105
    before = pos.active_stop
    # ATR spikes hugely; candidate (120 - 3*40 = 0) is far below — stop must HOLD.
    after = pos.ratchet_at_close(bar("2025-01-04", 0, 0, 0, 120, atr14=40))
    assert after == before == 105
    # And it must still be monotonic on a subsequent normal bar.
    after2 = pos.ratchet_at_close(bar("2025-01-05", 0, 0, 0, 130, atr14=5))
    assert after2 == 130 - TRAIL_ATR_MULT * 5      # 115
    assert after2 > after


def test_ratchet_history_is_nondecreasing_random_walk():
    pos = _pos(100.0, 5.0)
    closes = [101, 99, 105, 90, 120, 60, 121, 80, 122]
    atrs = [5, 8, 4, 30, 5, 50, 5, 25, 5]
    for k, (c, a) in enumerate(zip(closes, atrs)):
        pos.ratchet_at_close(bar(f"2025-02-{k+1:02d}", 0, 0, 0, c, atr14=a))
    hist = pos.ratchet_history
    assert all(hist[i] <= hist[i + 1] for i in range(len(hist) - 1)), hist
    assert min(hist) >= pos.initial_stop
