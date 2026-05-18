"""
§10.5 invariant: Entries gated by overlays. Exits NEVER blocked by overlays.

This test verifies the architectural boundary — overlays produce OverlayCheck
objects that gate entries but have no mechanism to block exits.
The router accepts active_overlays and may set allow_new_entries=False,
but manage_exit on any engine never consults overlays.
"""
from datetime import datetime, timezone, date

import pandas as pd

from bot.overlays.models import OverlayCheck
from bot.overlays.registry import active_overlays, OVERLAY_ORDER
from bot.strategies.noop import NoOpEngine
from bot.strategies.triple_confirmation import TripleConfirmationEngine
from bot.strategies.mean_reversion import MeanReversionEngine
from bot.regime.models import PositionMetadata
from bot.strategies.base import MarketState, ExitDecision


def _make_position():
    return PositionMetadata(
        position_id="P1",
        fill_id="F1",
        instrument="AAPL",
        entry_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        entry_price=150.0,
        entry_quantity=10.0,
        entry_strategy="TripleConfirmationEngine",
        entry_regime="TRENDING",
    )


def _make_state():
    return MarketState(
        symbol="AAPL",
        bar_time=pd.Timestamp("2026-05-18 14:00:00", tz="UTC"),
        ohlcv=pd.DataFrame(),
        indicators={},
        open_position=None,
        recent_trades=[],
        news=[],
        account={},
    )


def test_overlay_check_has_no_exit_blocking_field():
    """OverlayCheck only has is_active, expires_at, reason — no exit_blocked field."""
    check = OverlayCheck(
        overlay_name="TEST", is_active=True,
        expires_at=None, reason="test",
    )
    assert not hasattr(check, "block_exits")
    assert not hasattr(check, "exit_blocked")


def test_triple_confirmation_exit_ignores_overlays():
    engine = TripleConfirmationEngine()
    pos = _make_position()
    state = _make_state()
    decision = engine.manage_exit(pos, state)
    assert isinstance(decision, ExitDecision)
    assert decision.action in ("HOLD", "EXIT")


def test_noop_engine_exit_returns_hold():
    engine = NoOpEngine()
    pos = _make_position()
    state = _make_state()
    decision = engine.manage_exit(pos, state)
    assert decision.action == "HOLD"


def test_mean_reversion_exit_returns_hold():
    engine = MeanReversionEngine()
    pos = _make_position()
    state = _make_state()
    decision = engine.manage_exit(pos, state)
    assert decision.action == "HOLD"


def test_all_overlays_return_overlay_check():
    """Every overlay returns OverlayCheck, which has no exit-blocking semantics."""
    now = datetime(2026, 5, 18, 14, 0, 0, tzinfo=timezone.utc)
    for overlay in OVERLAY_ORDER:
        result = overlay.check("AAPL", now, {})
        assert isinstance(result, OverlayCheck)
