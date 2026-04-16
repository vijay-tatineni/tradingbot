"""
bot/brokers/ig.py — IG Markets broker adapter.

Uses the trading-ig library (https://github.com/ig-python/trading-ig)
to implement the BaseBroker interface for IG Markets REST API.

IG uses "epics" instead of contract objects. Each instrument in
instruments.json needs an "ig_epic" field.

No Docker container needed — pure REST API calls.
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
from trading_ig import IGService

from bot.brokers.base import BaseBroker, BrokerPosition, FillResult, PositionInfo
from bot.currency import is_pence_instrument, convert_pnl_to_base

logger = logging.getLogger("ig_broker")


# ── Resolution mapping ───────────────────────────────────────────
_RESOLUTION_MAP = {
    "1 min": "1min",
    "5 mins": "5min",
    "15 mins": "15min",
    "30 mins": "30min",
    "1 hour": "1h",
    "4 hours": "4h",
    "1 day": "D",
    "1 week": "W",
}

# ── Minimum intervals between API calls (seconds) ───────────────
_RATE_LIMITS = {
    "trade": 1.5,       # ~40 trades/min
    "historical": 2.0,  # conservative — IG has weekly data allowance
    "general": 0.5,     # ~30/min for non-trading requests
}


class IGBroker(BaseBroker):
    """
    IG Markets broker adapter.

    Connects via REST API using trading-ig.  Credentials are read from
    the instruments.json ``settings`` block or from environment variables
    (IG_USERNAME, IG_PASSWORD, IG_API_KEY, IG_ACC_TYPE, IG_ACC_NUMBER).
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self._settings = cfg._settings if hasattr(cfg, "_settings") else {}

        # Credentials: settings block → env vars → empty string
        self.username = (
            self._settings.get("ig_username")
            or os.environ.get("IG_USERNAME", "")
        )
        self.password = (
            self._settings.get("ig_password")
            or os.environ.get("IG_PASSWORD", "")
        )
        self.api_key = (
            self._settings.get("ig_api_key")
            or os.environ.get("IG_API_KEY", "")
        )
        self.acc_type = (
            self._settings.get("ig_acc_type")
            or os.environ.get("IG_ACC_TYPE", "DEMO")
        )
        self.acc_number = (
            self._settings.get("ig_acc_number")
            or os.environ.get("IG_ACC_NUMBER", "")
        )

        self.ig: Optional[IGService] = None
        self._connected = False
        self._alerts = None
        self._last_request: dict[str, float] = {}
        self._consecutive_login_failures: int = 0

        # Position cache — avoids repeated API calls within same cycle
        self._position_cache: Optional[list[BrokerPosition]] = None
        self._position_cache_time: float = 0
        self._position_cache_ttl: float = 30  # seconds

    # ── Connection ────────────────────────────────────────────────

    def connect(self) -> None:
        try:
            self.ig = IGService(
                self.username,
                self.password,
                self.api_key,
                self.acc_type,
                acc_number=self.acc_number or None,
            )
            self.ig.create_session()
            self._connected = True
            self._consecutive_login_failures = 0
            logger.info(
                "Connected to IG (%s) account %s",
                self.acc_type, self.acc_number,
            )
        except Exception as e:
            self._connected = False
            self._consecutive_login_failures += 1
            if self._consecutive_login_failures >= 3:
                wait = 300
            else:
                wait = 60
            logger.warning(
                "IG login failed (%d consecutive): %s — waiting %ds before retry",
                self._consecutive_login_failures, e, wait,
            )
            time.sleep(wait)

    def disconnect(self) -> None:
        if self.ig:
            try:
                self.ig.logout()
            except Exception:
                pass
        self._connected = False
        logger.info("Disconnected from IG")

    def reconnect(self) -> None:
        logger.info("Reconnecting to IG...")
        self.disconnect()
        time.sleep(5)
        self.connect()

    def is_connected(self) -> bool:
        return self._connected and self.ig is not None

    def sleep(self, seconds: float) -> None:
        """IG REST is stateless — plain sleep is fine."""
        time.sleep(seconds)

    def qualify_contracts(self, instruments: list[dict]) -> list[dict]:
        """
        Verify each instrument has an ``ig_epic``.  Optionally validate
        the epic exists on IG by fetching market info.
        """
        qualified = []
        for inst in instruments:
            epic = inst.get("ig_epic")
            if not epic:
                logger.warning(
                    "Instrument %s has no ig_epic — skipping",
                    inst.get("symbol"),
                )
                continue

            try:
                self._rate_limit("general")
                market = self.ig.fetch_market_by_epic(epic)
                if market:
                    inst["_ig_verified"] = True
                    # Store the epic as the "contract" equivalent
                    inst["contract"] = epic
                    logger.info(
                        "Verified IG epic: %s → %s",
                        inst.get("symbol"), epic,
                    )
            except Exception as e:
                logger.warning("Could not verify epic %s: %s", epic, e)
                inst["_ig_verified"] = False
                inst["contract"] = epic  # still usable

            qualified.append(inst)
        return qualified

    # ── Market Data ───────────────────────────────────────────────

    def fetch_bars(
        self,
        contract,
        days: int = 300,
        bar_size: str = "1 day",
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV bars.  ``contract`` is an IG epic string (set by
        qualify_contracts) or an instrument dict containing ``ig_epic``.
        """
        epic = self._resolve_epic(contract)
        if not epic:
            logger.error("fetch_bars: cannot resolve epic from %s", contract)
            return None

        resolution = _RESOLUTION_MAP.get(bar_size, "D")
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)

        try:
            self._ensure_session()
            self._rate_limit("historical")
            response = self.ig.fetch_historical_prices_by_epic(
                epic=epic,
                resolution=resolution,
                start_date=start_date.strftime("%Y-%m-%dT%H:%M:%S"),
                end_date=end_date.strftime("%Y-%m-%dT%H:%M:%S"),
            )

            if response is None or "prices" not in response:
                logger.warning("No data returned for %s (%s)", epic, resolution)
                return None

            df = self._prices_to_dataframe(response["prices"])
            if df.empty:
                return None

            logger.info("Fetched %d %s bars for %s", len(df), resolution, epic)
            return df

        except Exception as e:
            logger.error("Failed to fetch bars for %s: %s", epic, e)
            return None

    def fetch_price_snapshot(self, contract) -> Optional[float]:
        """Get the latest mid-price for a contract / epic."""
        epic = self._resolve_epic(contract)
        if not epic:
            return None

        try:
            self._ensure_session()
            self._rate_limit("historical")
            response = self.ig.fetch_historical_prices_by_epic(
                epic=epic,
                resolution="1min",
                numpoints=1,
            )
            if response and "prices" in response:
                prices = response["prices"]
                if "bid" in prices.columns.get_level_values(0):
                    bid = prices["bid"]["Close"].iloc[-1]
                    ask = prices["ask"]["Close"].iloc[-1]
                    return float((bid + ask) / 2)
                return float(prices["Close"].iloc[-1])
            return None
        except Exception as e:
            logger.error("Price snapshot error for %s: %s", epic, e)
            return None

    # ── Order Execution ───────────────────────────────────────────

    def place_order(
        self,
        contract,
        action: str,
        qty: float,
        name: str,
    ) -> FillResult:
        """Place a market order on IG and wait for confirmation."""
        epic = self._resolve_epic(contract)
        if not epic:
            logger.error("place_order: no epic for %s", name)
            return FillResult()

        # Determine currency and expiry from instrument config if available
        currency = "GBP"
        expiry = "DFB"
        if isinstance(contract, dict):
            currency = contract.get("currency", "GBP")
            expiry = contract.get("ig_expiry", "DFB")

        try:
            self._ensure_session()
            self._rate_limit("trade")
            resp = self.ig.create_open_position(
                currency_code=currency,
                direction=action.upper(),
                epic=epic,
                order_type="MARKET",
                expiry=expiry,
                force_open="false",
                guaranteed_stop="false",
                size=qty,
                level=None,
                limit_distance=None,
                limit_level=None,
                quote_id=None,
                stop_level=None,
                stop_distance=None,
                trailing_stop=None,
                trailing_stop_increment=None,
            )

            deal_ref = resp.get("dealReference", "")
            confirmation = self.ig.fetch_deal_by_deal_reference(deal_ref)
            deal_status = confirmation.get("dealStatus", "")
            fill_level = float(confirmation.get("level", 0.0))
            fill_size = float(confirmation.get("size", qty))
            deal_id = confirmation.get("dealId", deal_ref)

            if deal_status == "ACCEPTED":
                logger.info(
                    "IG order filled: %s %s %s @ %s (deal: %s)",
                    action, fill_size, epic, fill_level, deal_id,
                )
                self.invalidate_position_cache()
                return FillResult(
                    success=True,
                    fill_price=fill_level,
                    filled_qty=fill_size,
                )

            reason = confirmation.get("reason", "Unknown")
            logger.error("IG order rejected: %s", reason)
            return FillResult()

        except Exception as e:
            logger.error("IG order failed for %s: %s", name, e)
            return FillResult()

    def close_position(self, inst: dict, position: float) -> FillResult:
        """
        Close an existing position.  ``position`` sign determines the
        closing direction: positive → sell to close, negative → buy to close.
        """
        epic = inst.get("ig_epic") or inst.get("contract")
        if not epic:
            logger.error("close_position: no epic for %s", inst.get("symbol"))
            return FillResult()

        try:
            self._ensure_session()
            self._rate_limit("general")
            positions_df = self.ig.fetch_open_positions()

            if positions_df is None or positions_df.empty:
                logger.warning("No open positions to close for %s", epic)
                return FillResult()

            # Find matching position by epic
            deal_id = None
            for _, row in positions_df.iterrows():
                if row.get("epic") == epic:
                    deal_id = row.get("dealId")
                    break

            if not deal_id:
                logger.warning("No matching IG position for epic %s", epic)
                return FillResult()

            close_direction = "SELL" if position > 0 else "BUY"
            abs_qty = abs(position)

            self._rate_limit("trade")
            resp = self.ig.close_open_position(
                deal_id=deal_id,
                direction=close_direction,
                epic=epic,
                expiry=inst.get("ig_expiry", "DFB"),
                level=None,
                order_type="MARKET",
                quote_id=None,
                size=abs_qty,
            )

            deal_ref = resp.get("dealReference", "")
            confirmation = self.ig.fetch_deal_by_deal_reference(deal_ref)
            deal_status = confirmation.get("dealStatus", "")
            fill_level = float(confirmation.get("level", 0.0))

            if deal_status == "ACCEPTED":
                logger.info("IG position closed: %s @ %s", epic, fill_level)
                self.invalidate_position_cache()
                return FillResult(
                    success=True,
                    fill_price=fill_level,
                    filled_qty=abs_qty,
                )

            reason = confirmation.get("reason", "Unknown")
            logger.error("IG close rejected for %s: %s", epic, reason)
            return FillResult()

        except Exception as e:
            logger.error("IG close failed for %s: %s", epic, e)
            return FillResult()

    def handle_signal(
        self,
        inst: dict,
        signal: int,
        confidence: str,
        position: float,
    ) -> tuple[str, FillResult]:
        """
        Translate a signal into an order, respecting ``long_only``.

        Returns (action_string, FillResult).
        """
        contract = inst.get("contract") or inst.get("ig_epic")
        qty = inst.get("qty", 1)
        name = inst.get("name", inst.get("symbol", "?"))
        long_only = inst.get("long_only", True)

        if signal == 1 and position == 0:
            result = self.place_order(inst, "BUY", qty, name)
            return f"BOUGHT [{confidence}]", result

        if signal == -1 and position > 0:
            result = self.close_position(inst, position)
            return "SOLD_CLOSE", result

        if signal == -1 and position == 0 and not long_only:
            result = self.place_order(inst, "SELL", qty, name)
            return f"SHORTED [{confidence}]", result

        if signal == 1 and position < 0:
            result = self.close_position(inst, position)
            return "BOUGHT_CLOSE", result

        return "HOLD", FillResult()

    def set_alerts(self, alerts) -> None:
        self._alerts = alerts

    # ── Portfolio ─────────────────────────────────────────────────

    def get_position(self, symbol: str) -> float:
        """Return current position size for a symbol (0 if none)."""
        for pos in self.get_all_positions():
            if pos.symbol == symbol:
                return pos.qty
        return 0.0

    def get_position_info(
        self, symbol: str, current_price: float = 0,
    ) -> PositionInfo:
        for pos in self.get_all_positions():
            if pos.symbol == symbol:
                price = current_price or 0.0
                unreal = 0.0
                pnl_pct = 0.0
                if pos.avg_cost and price:
                    unreal = (price - pos.avg_cost) * pos.qty
                    # GBP instruments are priced in pence; convert P&L to pounds
                    if is_pence_instrument(pos.currency):
                        unreal = round(convert_pnl_to_base(unreal, pos.currency), 2)
                    pnl_pct = ((price - pos.avg_cost) / pos.avg_cost) * 100
                return PositionInfo(
                    symbol=symbol,
                    qty=pos.qty,
                    avg_cost=pos.avg_cost,
                    currency=pos.currency,
                    price=price,
                    unreal_pnl=unreal,
                    pnl_pct=pnl_pct,
                )
        return PositionInfo(symbol=symbol, qty=0, avg_cost=0, currency="GBP")

    def get_total_pnl(self) -> float:
        """Total unrealised P&L across all IG positions."""
        try:
            self._ensure_session()
            self._rate_limit("general")
            positions_df = self.ig.fetch_open_positions()
            if positions_df is None or positions_df.empty:
                return 0.0
            pnl_col = None
            for col in ("profit", "unrealisedPnl", "pnl"):
                if col in positions_df.columns:
                    pnl_col = col
                    break
            if pnl_col:
                return float(positions_df[pnl_col].sum())
            return 0.0
        except Exception as e:
            logger.error("Failed to get IG total P&L: %s", e)
            return 0.0

    def get_all_positions(self) -> list[BrokerPosition]:
        """Return cached positions, refreshing if TTL expired."""
        now = time.time()
        if (self._position_cache is not None
                and now - self._position_cache_time < self._position_cache_ttl):
            return self._position_cache
        self._position_cache = self._fetch_positions_from_api()
        self._position_cache_time = now
        return self._position_cache

    def invalidate_position_cache(self) -> None:
        """Call after placing/closing an order to force refresh."""
        self._position_cache = None

    def _fetch_positions_from_api(self) -> list[BrokerPosition]:
        try:
            self._ensure_session()
            self._rate_limit("general")
            positions_df = self.ig.fetch_open_positions()
            if positions_df is None or positions_df.empty:
                return []

            result = []
            for _, row in positions_df.iterrows():
                direction = row.get("direction", "BUY")
                size = float(row.get("size", row.get("dealSize", 0)))
                level = float(row.get("level", row.get("openLevel", 0)))
                epic = row.get("epic", "")

                # Resolve symbol from epic or instrument name
                symbol = self._epic_to_symbol(epic)

                result.append(BrokerPosition(
                    symbol=symbol,
                    qty=size if direction == "BUY" else -size,
                    avg_cost=level,
                    currency=row.get("currency", "GBP"),
                    contract=epic,
                ))
            return result

        except Exception as e:
            logger.error("Failed to fetch IG positions: %s", e)
            return []

    def get_all_position_info(self) -> list[PositionInfo]:
        positions = self.get_all_positions()
        return [
            PositionInfo(
                symbol=p.symbol,
                qty=p.qty,
                avg_cost=p.avg_cost,
                currency=p.currency,
                price=0.0,
                unreal_pnl=0.0,
                pnl_pct=0.0,
            )
            for p in positions
        ]

    def is_emergency_stop(self, total_pnl: float) -> bool:
        limit = getattr(self.cfg, "portfolio_loss_limit", 10_000)
        return total_pnl <= -abs(limit)

    # ── Internal helpers ──────────────────────────────────────────

    def _rate_limit(self, request_type: str = "general") -> None:
        """Enforce minimum interval between API calls of the same type."""
        interval = _RATE_LIMITS.get(request_type, 0.5)
        last = self._last_request.get(request_type, 0.0)
        elapsed = time.time() - last
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_request[request_type] = time.time()

    def _ensure_session(self) -> None:
        """Connect if not yet connected, or re-create session if expired."""
        if not self._connected or self.ig is None:
            self.connect()
            return
        try:
            self.ig.fetch_accounts()
        except Exception:
            logger.info("IG session expired — reconnecting")
            self.reconnect()

    @staticmethod
    def _resolve_epic(contract) -> Optional[str]:
        """
        Accept either a plain epic string or a dict with ``ig_epic``
        and return the epic string.
        """
        if isinstance(contract, str):
            return contract
        if isinstance(contract, dict):
            return contract.get("ig_epic") or contract.get("contract")
        return None

    def _epic_to_symbol(self, epic: str) -> str:
        """
        Map an IG epic back to a trading symbol by checking the
        configured instruments.  Falls back to the epic itself.
        """
        for inst in getattr(self.cfg, "active_instruments", []):
            if inst.get("ig_epic") == epic:
                return inst["symbol"]
        for inst in getattr(self.cfg, "accum_instruments", []):
            if inst.get("ig_epic") == epic:
                return inst["symbol"]
        return epic

    @staticmethod
    def _prices_to_dataframe(prices) -> pd.DataFrame:
        """Convert IG historical prices response to standard OHLCV DataFrame."""
        if prices is None or (hasattr(prices, "empty") and prices.empty):
            return pd.DataFrame()

        df = pd.DataFrame()
        df["date"] = prices.index

        # IG returns bid/ask sub-columns — use mid prices
        if "bid" in prices.columns.get_level_values(0):
            bid = prices["bid"]
            ask = prices["ask"]
            df["open"] = ((bid["Open"] + ask["Open"]) / 2).values
            df["high"] = ((bid["High"] + ask["High"]) / 2).values
            df["low"] = ((bid["Low"] + ask["Low"]) / 2).values
            df["close"] = ((bid["Close"] + ask["Close"]) / 2).values
        else:
            df["open"] = prices["Open"].values
            df["high"] = prices["High"].values
            df["low"] = prices["Low"].values
            df["close"] = prices["Close"].values

        # Volume may or may not be present
        try:
            if "last" in prices.columns.get_level_values(0):
                df["volume"] = prices["last"]["Volume"].values
            elif "Volume" in prices.columns.get_level_values(-1):
                df["volume"] = prices["Volume"].values
            else:
                df["volume"] = 0
        except (KeyError, TypeError):
            df["volume"] = 0

        return df.reset_index(drop=True)

    @staticmethod
    def _parse_duration(duration: str, end_date: datetime) -> datetime:
        """Parse IBKR-style duration string (e.g. '2 Y') to start date."""
        parts = duration.strip().split()
        if len(parts) != 2:
            return end_date - timedelta(days=365)

        num = int(parts[0])
        unit = parts[1].upper()

        if unit in ("Y", "YEAR", "YEARS"):
            return end_date - timedelta(days=num * 365)
        if unit in ("M", "MONTH", "MONTHS"):
            return end_date - timedelta(days=num * 30)
        if unit in ("D", "DAY", "DAYS"):
            return end_date - timedelta(days=num)
        return end_date - timedelta(days=365)
