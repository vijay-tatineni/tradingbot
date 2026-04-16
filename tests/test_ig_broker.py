"""
tests/test_ig_broker.py — Tests for the IG Markets broker adapter.

All tests use mocks — no real IG API calls are made.
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd
import pytest

from bot.brokers import create_broker
from bot.brokers.base import BaseBroker, BrokerPosition, FillResult, PositionInfo
from bot.brokers.ig import IGBroker


# ── Fixtures ──────────────────────────────────────────────────────

def _make_cfg(**overrides):
    """Create a mock Config with IG settings."""
    cfg = MagicMock()
    settings = {
        "broker": "ig",
        "ig_username": "test_user",
        "ig_password": "test_pass",
        "ig_api_key": "test_key",
        "ig_acc_type": "DEMO",
        "ig_acc_number": "ABC123",
        "portfolio_loss_limit": 10000,
    }
    settings.update(overrides)
    cfg._settings = settings
    cfg.portfolio_loss_limit = settings["portfolio_loss_limit"]
    cfg.active_instruments = []
    cfg.accum_instruments = []
    return cfg


def _make_broker(**cfg_overrides) -> IGBroker:
    """Create an IGBroker with mocked config (not connected)."""
    return IGBroker(_make_cfg(**cfg_overrides))


def _make_connected_broker(**cfg_overrides) -> IGBroker:
    """Create an IGBroker with a mocked IGService (appears connected)."""
    broker = _make_broker(**cfg_overrides)
    broker.ig = MagicMock()
    broker._connected = True
    return broker


def _make_ig_prices_df():
    """Create a mock IG historical prices DataFrame with bid/ask columns."""
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    arrays = [
        ["bid", "bid", "bid", "bid", "ask", "ask", "ask", "ask", "last"],
        ["Open", "High", "Low", "Close", "Open", "High", "Low", "Close", "Volume"],
    ]
    tuples = list(zip(*arrays))
    columns = pd.MultiIndex.from_tuples(tuples)
    data = {
        ("bid", "Open"): [100, 101, 102, 103, 104],
        ("bid", "High"): [105, 106, 107, 108, 109],
        ("bid", "Low"): [95, 96, 97, 98, 99],
        ("bid", "Close"): [102, 103, 104, 105, 106],
        ("ask", "Open"): [100.2, 101.2, 102.2, 103.2, 104.2],
        ("ask", "High"): [105.2, 106.2, 107.2, 108.2, 109.2],
        ("ask", "Low"): [95.2, 96.2, 97.2, 98.2, 99.2],
        ("ask", "Close"): [102.2, 103.2, 104.2, 105.2, 106.2],
        ("last", "Volume"): [1000, 1100, 1200, 1300, 1400],
    }
    return pd.DataFrame(data, index=idx)


# ── Factory ───────────────────────────────────────────────────────

def test_create_broker_ig():
    """create_broker('ig', config) should return IGBroker instance."""
    cfg = _make_cfg()
    broker = create_broker("ig", cfg)
    assert isinstance(broker, IGBroker)
    assert isinstance(broker, BaseBroker)


def test_create_broker_ig_without_credentials():
    """IGBroker should init without error even if credentials are empty."""
    cfg = _make_cfg(ig_username="", ig_password="", ig_api_key="")
    broker = IGBroker(cfg)
    assert broker.username == ""
    assert broker.password == ""
    assert broker.api_key == ""


# ── Interface compliance ──────────────────────────────────────────

def test_ig_broker_implements_base():
    """IGBroker must implement all abstract methods from BaseBroker."""
    assert issubclass(IGBroker, BaseBroker)

    abstract_methods = set()
    for cls in BaseBroker.__mro__:
        for name, val in vars(cls).items():
            if getattr(val, "__isabstractmethod__", False):
                abstract_methods.add(name)

    for method_name in abstract_methods:
        assert hasattr(IGBroker, method_name), \
            f"IGBroker missing abstract method: {method_name}"
        method = getattr(IGBroker, method_name)
        assert not getattr(method, "__isabstractmethod__", False), \
            f"IGBroker has not implemented: {method_name}"


def test_ig_broker_has_all_ibkr_public_methods():
    """IGBroker should have the same BaseBroker methods as IBKRBroker."""
    from bot.brokers.ibkr import IBKRBroker

    # Get all public methods defined in BaseBroker
    base_methods = {
        name for name in dir(BaseBroker)
        if not name.startswith("_") and callable(getattr(BaseBroker, name))
    }

    for method_name in base_methods:
        assert hasattr(IGBroker, method_name), \
            f"IGBroker missing method: {method_name}"


# ── Connection ────────────────────────────────────────────────────

def test_ig_connect_creates_session():
    """connect() should call IGService.create_session()."""
    broker = _make_broker()
    with patch("bot.brokers.ig.IGService") as MockIG:
        mock_service = MagicMock()
        MockIG.return_value = mock_service
        broker.connect()
        mock_service.create_session.assert_called_once()
        assert broker._connected is True


def test_ig_disconnect_calls_logout():
    """disconnect() should call IGService.logout()."""
    broker = _make_connected_broker()
    mock_ig = broker.ig
    broker.disconnect()
    mock_ig.logout.assert_called_once()
    assert broker._connected is False


def test_ig_reconnect_creates_new_session():
    """reconnect() should logout then connect again."""
    broker = _make_connected_broker()
    with patch("bot.brokers.ig.IGService") as MockIG, \
         patch("bot.brokers.ig.time") as mock_time:
        mock_service = MagicMock()
        MockIG.return_value = mock_service
        broker.reconnect()
        mock_service.create_session.assert_called_once()
        mock_time.sleep.assert_called_with(5)


def test_ig_sleep_uses_time_sleep():
    """sleep() should use time.sleep, not broker-specific sleep."""
    broker = _make_broker()
    with patch("bot.brokers.ig.time") as mock_time:
        broker.sleep(30)
        mock_time.sleep.assert_called_once_with(30)


def test_ig_is_connected_false_before_connect():
    """is_connected() should be False before connect() is called."""
    broker = _make_broker()
    assert broker.is_connected() is False


def test_ig_is_connected_true_after_connect():
    """is_connected() should be True after successful connect()."""
    broker = _make_connected_broker()
    assert broker.is_connected() is True


# ── Market Data ───────────────────────────────────────────────────

def test_ig_fetch_bars_requires_epic():
    """Contract without ig_epic should return None."""
    broker = _make_connected_broker()
    result = broker.fetch_bars({"symbol": "TEST"})
    assert result is None


def test_ig_fetch_bars_returns_ohlcv_dataframe():
    """Return DataFrame should have: date, open, high, low, close, volume."""
    broker = _make_connected_broker()
    prices_df = _make_ig_prices_df()
    broker.ig.fetch_historical_prices_by_epic.return_value = {"prices": prices_df}

    df = broker.fetch_bars("KA.D.BARC.DAILY.IP", days=30, bar_size="1 day")
    assert df is not None
    assert len(df) == 5
    for col in ("date", "open", "high", "low", "close", "volume"):
        assert col in df.columns


def test_ig_fetch_bars_uses_mid_prices():
    """Bars should use (bid + ask) / 2 for OHLC values."""
    broker = _make_connected_broker()
    prices_df = _make_ig_prices_df()
    broker.ig.fetch_historical_prices_by_epic.return_value = {"prices": prices_df}

    df = broker.fetch_bars("KA.D.BARC.DAILY.IP")
    # First bar: bid open 100, ask open 100.2 → mid 100.1
    assert abs(df["open"].iloc[0] - 100.1) < 0.01
    # First bar: bid close 102, ask close 102.2 → mid 102.1
    assert abs(df["close"].iloc[0] - 102.1) < 0.01


def test_ig_fetch_bars_maps_timeframe():
    """'4 hours' should map to '4h' (pandas offset format for trading_ig)."""
    broker = _make_connected_broker()
    broker.ig.fetch_historical_prices_by_epic.return_value = None

    broker.fetch_bars("KA.D.BARC.DAILY.IP", bar_size="4 hours")
    call_args = broker.ig.fetch_historical_prices_by_epic.call_args
    assert call_args.kwargs.get("resolution") == "4h" or \
           call_args[1].get("resolution") == "4h"


def test_ig_fetch_price_snapshot_returns_float():
    """fetch_price_snapshot() should return mid-price as float."""
    broker = _make_connected_broker()
    prices_df = _make_ig_prices_df()
    broker.ig.fetch_historical_prices_by_epic.return_value = {"prices": prices_df}

    price = broker.fetch_price_snapshot("KA.D.BARC.DAILY.IP")
    assert isinstance(price, float)
    assert price > 0


def test_ig_fetch_price_snapshot_no_data_returns_none():
    """fetch_price_snapshot() should return None when no data."""
    broker = _make_connected_broker()
    broker.ig.fetch_historical_prices_by_epic.return_value = None

    price = broker.fetch_price_snapshot("KA.D.BARC.DAILY.IP")
    assert price is None


# ── Orders ────────────────────────────────────────────────────────

def _setup_order_mock(broker):
    """Set up broker.ig for order tests."""
    broker.ig.create_open_position.return_value = {"dealReference": "REF123"}
    broker.ig.fetch_deal_by_deal_reference.return_value = {
        "dealId": "DEAL456",
        "dealStatus": "ACCEPTED",
        "level": 250.5,
        "size": 400,
    }


def test_ig_place_order_buy():
    """place_order with BUY should call create_open_position."""
    broker = _make_connected_broker()
    _setup_order_mock(broker)

    result = broker.place_order(
        {"ig_epic": "KA.D.BARC.DAILY.IP", "currency": "GBP", "ig_expiry": "DFB"},
        "BUY", 400, "Barclays",
    )
    assert result.success is True
    assert result.fill_price == 250.5
    assert result.filled_qty == 400
    broker.ig.create_open_position.assert_called_once()


def test_ig_place_order_sell():
    """place_order with SELL should call create_open_position."""
    broker = _make_connected_broker()
    _setup_order_mock(broker)

    result = broker.place_order(
        {"ig_epic": "KA.D.BARC.DAILY.IP", "currency": "GBP"},
        "SELL", 400, "Barclays",
    )
    assert result.success is True
    broker.ig.create_open_position.assert_called_once()


def test_ig_place_order_no_epic_returns_error():
    """Instrument without ig_epic should return failed FillResult."""
    broker = _make_connected_broker()
    result = broker.place_order({"symbol": "TEST"}, "BUY", 10, "Test")
    assert result.success is False
    assert result.fill_price == 0.0


def test_ig_place_order_rejected():
    """Rejected order should return FillResult with success=False."""
    broker = _make_connected_broker()
    broker.ig.create_open_position.return_value = {"dealReference": "REF123"}
    broker.ig.fetch_deal_by_deal_reference.return_value = {
        "dealId": "DEAL456",
        "dealStatus": "REJECTED",
        "reason": "INSUFFICIENT_FUNDS",
        "level": 0,
        "size": 0,
    }

    result = broker.place_order(
        {"ig_epic": "KA.D.BARC.DAILY.IP", "currency": "GBP"},
        "BUY", 400, "Barclays",
    )
    assert result.success is False


def test_ig_place_order_exception():
    """Exception during order should return failed FillResult, not crash."""
    broker = _make_connected_broker()
    broker.ig.create_open_position.side_effect = Exception("API error")

    result = broker.place_order(
        {"ig_epic": "KA.D.BARC.DAILY.IP", "currency": "GBP"},
        "BUY", 400, "Barclays",
    )
    assert result.success is False


def test_ig_close_position_finds_deal_id():
    """close_position should find the deal_id from open positions."""
    broker = _make_connected_broker()

    positions_df = pd.DataFrame([{
        "epic": "KA.D.BARC.DAILY.IP",
        "dealId": "DEAL789",
        "direction": "BUY",
        "size": 400,
    }])
    broker.ig.fetch_open_positions.return_value = positions_df
    broker.ig.close_open_position.return_value = {"dealReference": "CLOSE_REF"}
    broker.ig.fetch_deal_by_deal_reference.return_value = {
        "dealId": "CLOSE_DEAL",
        "dealStatus": "ACCEPTED",
        "level": 255.0,
    }

    result = broker.close_position(
        {"ig_epic": "KA.D.BARC.DAILY.IP", "symbol": "BARC"},
        400,  # positive = long position
    )
    assert result.success is True
    assert result.fill_price == 255.0


def test_ig_close_position_no_matching_position():
    """Closing a position that doesn't exist should return error."""
    broker = _make_connected_broker()
    broker.ig.fetch_open_positions.return_value = pd.DataFrame()

    result = broker.close_position(
        {"ig_epic": "KA.D.BARC.DAILY.IP", "symbol": "BARC"}, 400,
    )
    assert result.success is False


# ── handle_signal ─────────────────────────────────────────────────

def test_ig_handle_signal_buy():
    """Signal 1 with no position → BUY."""
    broker = _make_connected_broker()
    _setup_order_mock(broker)

    inst = {"ig_epic": "KA.D.BARC.DAILY.IP", "contract": "KA.D.BARC.DAILY.IP",
            "qty": 400, "name": "Barclays", "long_only": True,
            "currency": "GBP", "ig_expiry": "DFB"}
    action, result = broker.handle_signal(inst, 1, "HIGH", 0)
    assert action == "BOUGHT [HIGH]"
    assert result.success is True


def test_ig_handle_signal_sell_close():
    """Signal -1 with long position → SELL_CLOSE."""
    broker = _make_connected_broker()

    positions_df = pd.DataFrame([{
        "epic": "KA.D.BARC.DAILY.IP", "dealId": "D1",
        "direction": "BUY", "size": 400,
    }])
    broker.ig.fetch_open_positions.return_value = positions_df
    broker.ig.close_open_position.return_value = {"dealReference": "R1"}
    broker.ig.fetch_deal_by_deal_reference.return_value = {
        "dealId": "D2", "dealStatus": "ACCEPTED", "level": 260.0,
    }

    inst = {"ig_epic": "KA.D.BARC.DAILY.IP", "contract": "KA.D.BARC.DAILY.IP",
            "qty": 400, "name": "Barclays", "long_only": True,
            "symbol": "BARC", "ig_expiry": "DFB"}
    action, result = broker.handle_signal(inst, -1, "HIGH", 400)
    assert action == "SOLD_CLOSE"


def test_ig_handle_signal_hold():
    """Signal 0 → HOLD with empty FillResult."""
    broker = _make_connected_broker()
    inst = {"ig_epic": "KA.D.BARC.DAILY.IP", "qty": 400, "name": "Barclays",
            "long_only": True}
    action, result = broker.handle_signal(inst, 0, "LOW", 0)
    assert action == "HOLD"
    assert result.success is False


def test_ig_handle_signal_long_only_blocks_short():
    """Signal -1 with no position and long_only=True → HOLD."""
    broker = _make_connected_broker()
    inst = {"ig_epic": "KA.D.BARC.DAILY.IP", "qty": 400, "name": "Barclays",
            "long_only": True}
    action, result = broker.handle_signal(inst, -1, "HIGH", 0)
    assert action == "HOLD"


# ── Portfolio ─────────────────────────────────────────────────────

def test_ig_get_positions_empty():
    """No positions returns empty list."""
    broker = _make_connected_broker()
    broker.ig.fetch_open_positions.return_value = pd.DataFrame()
    assert broker.get_all_positions() == []


def test_ig_get_positions_returns_broker_position():
    """Positions should be returned as BrokerPosition dataclass."""
    broker = _make_connected_broker()
    broker.ig.fetch_open_positions.return_value = pd.DataFrame([{
        "epic": "KA.D.BARC.DAILY.IP",
        "dealId": "D1",
        "direction": "BUY",
        "size": 400,
        "level": 250.0,
        "currency": "GBP",
    }])

    positions = broker.get_all_positions()
    assert len(positions) == 1
    assert isinstance(positions[0], BrokerPosition)
    assert positions[0].avg_cost == 250.0
    assert positions[0].currency == "GBP"


def test_ig_get_positions_direction_sign():
    """BUY positions have positive qty, SELL have negative."""
    broker = _make_connected_broker()
    broker.ig.fetch_open_positions.return_value = pd.DataFrame([
        {"epic": "E1", "dealId": "D1", "direction": "BUY",
         "size": 100, "level": 50, "currency": "GBP"},
        {"epic": "E2", "dealId": "D2", "direction": "SELL",
         "size": 200, "level": 60, "currency": "USD"},
    ])

    positions = broker.get_all_positions()
    assert positions[0].qty == 100   # BUY → positive
    assert positions[1].qty == -200  # SELL → negative


def test_ig_get_position_symbol():
    """get_position() returns qty for matching symbol."""
    broker = _make_connected_broker()
    broker.cfg.active_instruments = [
        {"symbol": "BARC", "ig_epic": "KA.D.BARC.DAILY.IP"},
    ]
    broker.ig.fetch_open_positions.return_value = pd.DataFrame([{
        "epic": "KA.D.BARC.DAILY.IP", "dealId": "D1",
        "direction": "BUY", "size": 400, "level": 250, "currency": "GBP",
    }])

    assert broker.get_position("BARC") == 400


def test_ig_get_position_not_found():
    """get_position() returns 0 for symbol not in positions."""
    broker = _make_connected_broker()
    broker.ig.fetch_open_positions.return_value = pd.DataFrame()
    assert broker.get_position("MSFT") == 0.0


def test_ig_get_position_info():
    """get_position_info() returns PositionInfo with P&L calculations."""
    broker = _make_connected_broker()
    broker.cfg.active_instruments = [
        {"symbol": "BARC", "ig_epic": "KA.D.BARC.DAILY.IP"},
    ]
    broker.ig.fetch_open_positions.return_value = pd.DataFrame([{
        "epic": "KA.D.BARC.DAILY.IP", "dealId": "D1",
        "direction": "BUY", "size": 400, "level": 250, "currency": "GBP",
    }])

    info = broker.get_position_info("BARC", current_price=260)
    assert isinstance(info, PositionInfo)
    assert info.qty == 400
    assert info.avg_cost == 250
    assert info.unreal_pnl == (260 - 250) * 400 / 100  # 4000 pence → £40
    assert abs(info.pnl_pct - 4.0) < 0.01


def test_ig_get_all_position_info():
    """get_all_position_info() returns list of PositionInfo."""
    broker = _make_connected_broker()
    broker.ig.fetch_open_positions.return_value = pd.DataFrame([{
        "epic": "E1", "dealId": "D1", "direction": "BUY",
        "size": 10, "level": 100, "currency": "USD",
    }])

    infos = broker.get_all_position_info()
    assert len(infos) == 1
    assert isinstance(infos[0], PositionInfo)


def test_ig_is_emergency_stop():
    """Emergency stop triggers when P&L exceeds loss limit."""
    broker = _make_broker()
    assert broker.is_emergency_stop(-15000) is True
    assert broker.is_emergency_stop(-5000) is False
    assert broker.is_emergency_stop(0) is False


# ── Contract qualification ────────────────────────────────────────

def test_ig_qualify_contracts_filters_no_epic():
    """Instruments without ig_epic should be filtered out."""
    broker = _make_connected_broker()
    instruments = [
        {"symbol": "TEST1"},  # no ig_epic
        {"symbol": "TEST2", "ig_epic": "KA.D.TEST.DAILY.IP"},
    ]
    broker.ig.fetch_market_by_epic.return_value = {"epic": "KA.D.TEST.DAILY.IP"}

    qualified = broker.qualify_contracts(instruments)
    assert len(qualified) == 1
    assert qualified[0]["symbol"] == "TEST2"


def test_ig_qualify_contracts_keeps_valid():
    """Instruments with ig_epic should be kept and get contract field."""
    broker = _make_connected_broker()
    instruments = [
        {"symbol": "BARC", "ig_epic": "KA.D.BARC.DAILY.IP"},
    ]
    broker.ig.fetch_market_by_epic.return_value = {"epic": "KA.D.BARC.DAILY.IP"}

    qualified = broker.qualify_contracts(instruments)
    assert len(qualified) == 1
    assert qualified[0]["contract"] == "KA.D.BARC.DAILY.IP"
    assert qualified[0]["_ig_verified"] is True


def test_ig_qualify_contracts_unverified_still_kept():
    """If epic verification fails, instrument is still kept."""
    broker = _make_connected_broker()
    instruments = [
        {"symbol": "BARC", "ig_epic": "KA.D.BARC.DAILY.IP"},
    ]
    broker.ig.fetch_market_by_epic.side_effect = Exception("API error")

    qualified = broker.qualify_contracts(instruments)
    assert len(qualified) == 1
    assert qualified[0]["_ig_verified"] is False
    assert qualified[0]["contract"] == "KA.D.BARC.DAILY.IP"


# ── Rate limiting ────────────────────────────────────────────────

def test_ig_rate_limit_trade():
    """Trade requests should enforce minimum interval."""
    broker = _make_broker()
    with patch("bot.brokers.ig.time") as mock_time:
        mock_time.time.return_value = 100.0
        broker._last_request["trade"] = 99.5  # 0.5s ago
        broker._rate_limit("trade")
        # Should sleep for ~1.0s (1.5 - 0.5)
        mock_time.sleep.assert_called_once()
        slept = mock_time.sleep.call_args[0][0]
        assert 0.9 < slept < 1.1


def test_ig_rate_limit_no_delay_when_enough_time():
    """No sleep needed when enough time has passed."""
    broker = _make_broker()
    with patch("bot.brokers.ig.time") as mock_time:
        mock_time.time.return_value = 200.0
        broker._last_request["general"] = 100.0  # 100s ago
        broker._rate_limit("general")
        mock_time.sleep.assert_not_called()


# ── Duration parsing ─────────────────────────────────────────────

def test_parse_duration_years():
    """'2 Y' should give ~730 days before end_date."""
    end = datetime(2026, 4, 1, tzinfo=timezone.utc)
    start = IGBroker._parse_duration("2 Y", end)
    diff = (end - start).days
    assert diff == 730


def test_parse_duration_months():
    """'6 M' should give ~180 days before end_date."""
    end = datetime(2026, 4, 1, tzinfo=timezone.utc)
    start = IGBroker._parse_duration("6 M", end)
    diff = (end - start).days
    assert diff == 180


def test_parse_duration_days():
    """'30 D' should give 30 days before end_date."""
    end = datetime(2026, 4, 1, tzinfo=timezone.utc)
    start = IGBroker._parse_duration("30 D", end)
    diff = (end - start).days
    assert diff == 30


def test_parse_duration_invalid_defaults_to_1_year():
    """Invalid duration string should default to 365 days."""
    end = datetime(2026, 4, 1, tzinfo=timezone.utc)
    start = IGBroker._parse_duration("invalid", end)
    diff = (end - start).days
    assert diff == 365


# ── Epic resolution ──────────────────────────────────────────────

def test_resolve_epic_string():
    """String epic should be returned as-is."""
    assert IGBroker._resolve_epic("KA.D.BARC.DAILY.IP") == "KA.D.BARC.DAILY.IP"


def test_resolve_epic_dict_with_ig_epic():
    """Dict with ig_epic should return the epic."""
    assert IGBroker._resolve_epic({"ig_epic": "KA.D.BARC.DAILY.IP"}) == "KA.D.BARC.DAILY.IP"


def test_resolve_epic_dict_with_contract():
    """Dict with contract (no ig_epic) should return contract."""
    assert IGBroker._resolve_epic({"contract": "KA.D.BARC.DAILY.IP"}) == "KA.D.BARC.DAILY.IP"


def test_resolve_epic_none():
    """None input should return None."""
    assert IGBroker._resolve_epic(None) is None


def test_resolve_epic_int():
    """Non-string non-dict should return None."""
    assert IGBroker._resolve_epic(42) is None


# ── Epic-to-symbol mapping ───────────────────────────────────────

def test_epic_to_symbol_found():
    """Known epic should map to its symbol."""
    broker = _make_broker()
    broker.cfg.active_instruments = [
        {"symbol": "BARC", "ig_epic": "KA.D.BARC.DAILY.IP"},
    ]
    assert broker._epic_to_symbol("KA.D.BARC.DAILY.IP") == "BARC"


def test_epic_to_symbol_not_found():
    """Unknown epic should return the epic itself."""
    broker = _make_broker()
    broker.cfg.active_instruments = []
    assert broker._epic_to_symbol("UNKNOWN.EPIC") == "UNKNOWN.EPIC"


# ── C1: handle_signal returns correct action strings ─────────────

def test_handle_signal_buy_returns_bought():
    """handle_signal for BUY must return 'BOUGHT [...]' so layer1.py records it."""
    broker = _make_connected_broker()
    broker.ig.create_open_position.return_value = {
        "dealReference": "REF1",
        "dealStatus": "ACCEPTED",
    }
    broker.ig.fetch_deal_by_deal_reference.return_value = {
        "dealStatus": "ACCEPTED",
        "level": 250.0,
        "size": 400,
    }
    inst = {"symbol": "BARC", "ig_epic": "KA.D.BARC.DAILY.IP",
            "qty": 400, "currency": "GBP", "long_only": True}
    action, fill = broker.handle_signal(inst, signal=1, confidence="HIGH", position=0)
    assert "BOUGHT" in action, f"Expected 'BOUGHT' in action, got '{action}'"
    assert "HIGH" in action


def test_handle_signal_short_returns_shorted():
    """handle_signal for SELL_SHORT must return 'SHORTED [...]' so layer1.py records it."""
    broker = _make_connected_broker()
    broker.ig.create_open_position.return_value = {
        "dealReference": "REF2",
        "dealStatus": "ACCEPTED",
    }
    broker.ig.fetch_deal_by_deal_reference.return_value = {
        "dealStatus": "ACCEPTED",
        "level": 150.0,
        "size": 100,
    }
    inst = {"symbol": "TSLA", "ig_epic": "KA.D.TSLA.DAILY.IP",
            "qty": 100, "currency": "USD", "long_only": False}
    action, fill = broker.handle_signal(inst, signal=-1, confidence="MEDIUM", position=0)
    assert "SHORTED" in action, f"Expected 'SHORTED' in action, got '{action}'"
    assert "MEDIUM" in action


def test_handle_signal_hold_unchanged():
    """No signal match returns HOLD."""
    broker = _make_connected_broker()
    inst = {"symbol": "BARC", "ig_epic": "KA.D.BARC.DAILY.IP",
            "qty": 400, "currency": "GBP", "long_only": True}
    action, fill = broker.handle_signal(inst, signal=-1, confidence="HIGH", position=0)
    assert action == "HOLD"


# ── C2: GBP pence-to-pounds conversion in get_position_info ──────

def test_get_position_info_gbp_pence_conversion():
    """GBP P&L must be divided by 100 (pence → pounds)."""
    broker = _make_connected_broker()
    positions_df = pd.DataFrame({
        "epic": ["KA.D.BARC.DAILY.IP"],
        "direction": ["BUY"],
        "size": [400.0],
        "level": [250.0],
        "currency": ["GBP"],
        "dealId": ["DEAL1"],
    })
    broker.ig.fetch_open_positions.return_value = positions_df
    broker.cfg.active_instruments = [
        {"symbol": "BARC", "ig_epic": "KA.D.BARC.DAILY.IP"}
    ]
    info = broker.get_position_info("BARC", current_price=260.0)
    # Raw unreal = (260 - 250) * 400 = 4000 pence = £40.00
    assert info.unreal_pnl == 40.0, f"Expected 40.0, got {info.unreal_pnl}"


def test_get_position_info_usd_no_conversion():
    """USD P&L should NOT be divided by 100."""
    broker = _make_connected_broker()
    positions_df = pd.DataFrame({
        "epic": ["KA.D.AAPL.DAILY.IP"],
        "direction": ["BUY"],
        "size": [10.0],
        "level": [180.0],
        "currency": ["USD"],
        "dealId": ["DEAL2"],
    })
    broker.ig.fetch_open_positions.return_value = positions_df
    broker.cfg.active_instruments = [
        {"symbol": "AAPL", "ig_epic": "KA.D.AAPL.DAILY.IP"}
    ]
    info = broker.get_position_info("AAPL", current_price=190.0)
    # Raw unreal = (190 - 180) * 10 = 100 — no conversion
    assert info.unreal_pnl == 100.0, f"Expected 100.0, got {info.unreal_pnl}"


# ── C3: _ensure_session is called ────────────────────────────────

def test_ensure_session_called_on_fetch_bars():
    """_ensure_session must be called before API requests."""
    broker = _make_connected_broker()
    broker.ig.fetch_historical_prices_by_epic.return_value = None
    with patch.object(broker, '_ensure_session') as mock_ensure:
        broker.fetch_bars("KA.D.BARC.DAILY.IP", days=10)
        mock_ensure.assert_called()


def test_ensure_session_called_on_place_order():
    """_ensure_session must be called before placing orders."""
    broker = _make_connected_broker()
    broker.ig.create_open_position.return_value = {
        "dealReference": "REF", "dealStatus": "ACCEPTED",
    }
    broker.ig.fetch_deal_by_deal_reference.return_value = {
        "dealStatus": "ACCEPTED", "level": 100.0, "size": 10,
    }
    inst = {"ig_epic": "KA.D.TEST.DAILY.IP", "currency": "USD"}
    with patch.object(broker, '_ensure_session') as mock_ensure:
        broker.place_order(inst, "BUY", 10, "TEST")
        mock_ensure.assert_called()


def test_ensure_session_called_on_get_all_positions():
    """_ensure_session must be called before fetching positions."""
    broker = _make_connected_broker()
    broker.ig.fetch_open_positions.return_value = pd.DataFrame()
    with patch.object(broker, '_ensure_session') as mock_ensure:
        broker.get_all_positions()
        mock_ensure.assert_called()


def test_ensure_session_reconnects_on_expired():
    """When fetch_accounts raises, _ensure_session should reconnect."""
    broker = _make_connected_broker()
    broker.ig.fetch_accounts.side_effect = Exception("Session expired")
    with patch.object(broker, 'reconnect') as mock_reconnect:
        broker._ensure_session()
        mock_reconnect.assert_called_once()


# ── Position cache tests ─────────────────────────────────────

def test_ig_position_cache_reuses_within_ttl():
    """Two calls to get_all_positions() within TTL should only make one API call."""
    broker = _make_connected_broker()
    broker.ig.fetch_open_positions.return_value = pd.DataFrame([{
        "epic": "KA.D.BARC.DAILY.IP", "dealId": "D1",
        "direction": "BUY", "size": 400, "level": 250, "currency": "GBP",
    }])
    broker.cfg.active_instruments = [
        {"symbol": "BARC", "ig_epic": "KA.D.BARC.DAILY.IP"}
    ]

    result1 = broker.get_all_positions()
    result2 = broker.get_all_positions()

    assert len(result1) == 1
    assert len(result2) == 1
    # fetch_open_positions should only be called once (cached on second call)
    assert broker.ig.fetch_open_positions.call_count == 1


def test_ig_position_cache_refreshes_after_ttl():
    """Call after TTL expires should make a new API call."""
    broker = _make_connected_broker()
    broker._position_cache_ttl = 0  # expire immediately
    broker.ig.fetch_open_positions.return_value = pd.DataFrame([{
        "epic": "E1", "dealId": "D1",
        "direction": "BUY", "size": 10, "level": 100, "currency": "USD",
    }])
    broker.cfg.active_instruments = []

    broker.get_all_positions()
    broker.get_all_positions()

    # With TTL=0, both calls should hit the API
    assert broker.ig.fetch_open_positions.call_count == 2


def test_ig_position_cache_invalidated_after_order():
    """After place_order(), the cache should be cleared."""
    broker = _make_connected_broker()
    broker.ig.fetch_open_positions.return_value = pd.DataFrame()
    broker.cfg.active_instruments = []

    # Populate cache
    broker.get_all_positions()
    assert broker._position_cache is not None

    # Place order — should invalidate
    broker.ig.create_open_position.return_value = {
        "dealReference": "REF", "dealStatus": "ACCEPTED",
    }
    broker.ig.fetch_deal_by_deal_reference.return_value = {
        "dealStatus": "ACCEPTED", "level": 100.0, "size": 10,
    }
    inst = {"ig_epic": "KA.D.TEST.DAILY.IP", "currency": "USD"}
    broker.place_order(inst, "BUY", 10, "TEST")

    assert broker._position_cache is None  # cache invalidated
