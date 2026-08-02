"""Mixed-market UTC event ordering (§14). A European exit at the LSE session
open (08:00 UTC) frees a slot before a US entry at 14:30 UTC the same date.
The frozen universe order arbitrates ONLY among simultaneous candidates."""
import backtest.breakout_sim as bsim
from backtest.breakout_sim import run_breakout_portfolio, session_open_offset
from tests.breakout_helpers import make_inst, bar


def test_european_session_orders_before_us_same_date():
    assert session_open_offset("GBP") < session_open_offset("USD")
    assert session_open_offset("EUR") < session_open_offset("USD")


def test_lse_exit_frees_slot_before_us_entry_same_date(monkeypatch):
    monkeypatch.setattr(bsim, "MAX_OPEN_POSITIONS", 1)   # single slot

    gbp = make_inst("LSEX", 0, [
        bar("2025-01-06", 99, 100, 98, 99, atr14=1, entry_signal=True),
        bar("2025-01-07", 100, 110, 99, 100, atr14=1, trend_break=True),  # holds; ratchet->98; schedule exit
        bar("2025-01-08", 101, 102, 100, 101, atr14=1),  # opens 101 > stop 98 -> trend-break, not gap
    ], currency="GBP")
    us = make_inst("USX", 1, [
        bar("2025-01-07", 49, 50, 48, 49, atr14=1, entry_signal=True),    # pending for 01-08
        bar("2025-01-08", 50, 55, 50, 54, atr14=1),      # US entry at 14:30
    ], currency="USD")

    res = run_breakout_portfolio([gbp, us], initial_capital_usd=100_000.0,
                                 liquidate_at_end=False)

    # The single slot was occupied by LSEX through 01-07; it exits 01-08 08:00,
    # so USX can enter 01-08 14:30 instead of being blocked by the cap.
    entered = {e.symbol: e.date for e in res.entries}
    assert "USX" in entered and entered["USX"].startswith("2025-01-08")
    lse_exit = [t for t in res.trades if t.symbol == "LSEX"][0]
    assert lse_exit.exit_reason == "trend_break"
    assert lse_exit.exit_date.startswith("2025-01-08")
    # USX must NOT have been blocked by the cap on 01-08.
    assert not any(b.symbol == "USX" and b.reason == "cap" for b in res.blocked)


def test_universe_order_breaks_simultaneous_slot_contention(monkeypatch):
    monkeypatch.setattr(bsim, "MAX_OPEN_POSITIONS", 1)   # single slot

    # Two US names, same session timestamp, both want the one free slot on 01-08.
    a = make_inst("AAA", 0, [
        bar("2025-01-07", 99, 100, 98, 99, atr14=1, entry_signal=True),
        bar("2025-01-08", 100, 105, 99, 104, atr14=1),
    ], currency="USD")
    b = make_inst("BBB", 1, [
        bar("2025-01-07", 99, 100, 98, 99, atr14=1, entry_signal=True),
        bar("2025-01-08", 100, 105, 99, 104, atr14=1),
    ], currency="USD")

    res = run_breakout_portfolio([a, b], initial_capital_usd=100_000.0,
                                 liquidate_at_end=False)

    entered = [e.symbol for e in res.entries]
    assert entered == ["AAA"]                              # lower order_idx wins
    assert any(x.symbol == "BBB" and x.reason == "cap" for x in res.blocked)
    # The contention was recorded as a simultaneous multi-candidate event.
    assert any(set(c[1]) == {"AAA", "BBB"} for c in res.contention_events)
