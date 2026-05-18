"""Unit tests for core dataclasses — §7."""
from datetime import datetime, timezone

from bot.regime.models import (
    RegimeClassification,
    SmoothedRegimeState,
    RoutingDecision,
    PositionMetadata,
)


class TestRegimeClassification:
    def test_create_with_defaults(self):
        c = RegimeClassification(
            instrument="BARC.L",
            classified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            trading_date="2026-01-01",
            raw_regime="TRENDING",
            confidence=0.9,
            rationale="Strong ADX",
            features={"adx_14": 30.0},
            model_version="claude-sonnet-4-6",
            prompt_version="V1",
            input_hash="abc123",
        )
        assert c.cache_hit is False
        assert c.raw_regime == "TRENDING"
        assert c.confidence == 0.9

    def test_cache_hit_field_accessible(self):
        c = RegimeClassification(
            instrument="BARC.L",
            classified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            trading_date="2026-01-01",
            raw_regime="RANGING",
            confidence=0.8,
            rationale="Low ADX",
            features={},
            model_version="test",
            prompt_version="V1",
            input_hash="def456",
            cache_hit=True,
        )
        assert c.cache_hit is True

    def test_frozen(self):
        c = RegimeClassification(
            instrument="BARC.L",
            classified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            trading_date="2026-01-01",
            raw_regime="UNCLEAR",
            confidence=0.5,
            rationale="Mixed",
            features={},
            model_version="test",
            prompt_version="V1",
            input_hash="ghi789",
        )
        try:
            c.confidence = 0.99
            assert False, "Should not allow mutation"
        except AttributeError:
            pass


class TestSmoothedRegimeState:
    def test_create(self):
        s = SmoothedRegimeState(
            instrument="SGLN.L",
            effective_regime="TRENDING",
            source_regime="TRENDING",
            days_in_regime=5,
            last_changed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            confidence=0.9,
            pending_regime=None,
            pending_days=0,
        )
        assert s.effective_regime == "TRENDING"
        assert s.regime_history == []

    def test_with_history(self):
        s = SmoothedRegimeState(
            instrument="SGLN.L",
            effective_regime="RANGING",
            source_regime="RANGING",
            days_in_regime=2,
            last_changed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            confidence=0.85,
            pending_regime=None,
            pending_days=0,
            regime_history=["TRENDING", "UNCLEAR"],
        )
        assert len(s.regime_history) == 2


class TestRoutingDecision:
    def test_create(self):
        r = RoutingDecision(
            instrument="BARC.L",
            decided_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            effective_regime="TRENDING",
            selected_engine="TripleConfirmationEngine",
            allow_new_entries=True,
        )
        assert r.active_overlays == []
        assert r.block_reason is None
        assert r.flag_snapshot == {}

    def test_with_overlays(self):
        r = RoutingDecision(
            instrument="BARC.L",
            decided_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            effective_regime="TRENDING",
            selected_engine="TripleConfirmationEngine",
            allow_new_entries=False,
            active_overlays=["EARNINGS_LOCKOUT"],
            block_reason="Overlay active",
        )
        assert r.allow_new_entries is False


class TestPositionMetadata:
    def test_create(self):
        m = PositionMetadata(
            position_id="pos-001",
            fill_id="fill-001",
            instrument="BARC.L",
            entry_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            entry_price=150.0,
            entry_quantity=100.0,
            entry_strategy="TripleConfirmationEngine",
            entry_regime="TRENDING",
        )
        assert m.exit_policy == "use_entry_strategy_rules"
        assert m.entry_overlays_active == []

    def test_composite_key(self):
        m1 = PositionMetadata(
            position_id="pos-001",
            fill_id="fill-001",
            instrument="BARC.L",
            entry_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            entry_price=150.0,
            entry_quantity=50.0,
            entry_strategy="TripleConfirmationEngine",
            entry_regime="TRENDING",
        )
        m2 = PositionMetadata(
            position_id="pos-001",
            fill_id="fill-002",
            instrument="BARC.L",
            entry_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            entry_price=151.0,
            entry_quantity=50.0,
            entry_strategy="TripleConfirmationEngine",
            entry_regime="TRENDING",
        )
        assert m1.position_id == m2.position_id
        assert m1.fill_id != m2.fill_id
        total_qty = m1.entry_quantity + m2.entry_quantity
        assert total_qty == 100.0
