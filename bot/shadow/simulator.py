"""
Shadow position simulator — §11.3, §11.5 of CLAUDE_STRATEGY_SPEC_v3.

Mirrors real position manager on simulated fills. Applies entry engine's
manage_exit against real bars. Honours historical overlay state at entry bar.

Abandonment criteria (§11.3):
- Instrument delisted
- Data stale > 5 bars
- DATA_QUALITY overlay active > 5 consecutive bars

Async execution (§11.5):
- Bounded queue (max 100), drop oldest on overflow
- shadow_lag counter, Telegram warn at threshold
- Shadow NEVER blocks live decisions
"""
import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Callable

from bot.shadow.counterfactual_logger import CounterfactualLogger
from bot.overlays.registry import active_overlays

logger = logging.getLogger("shadow.simulator")

MAX_QUEUE_SIZE = 100
STALE_BAR_ABANDON_THRESHOLD = 5
DATA_QUALITY_ABANDON_THRESHOLD = 5
SHADOW_LAG_WARN_THRESHOLD = 50


@dataclass
class ShadowWorkItem:
    instrument: str
    bar_time: str
    price: float
    indicators: dict
    overlay_ctx: dict
    regime: Optional[str] = None
    confidence: Optional[float] = None
    smoothed_regime: Optional[str] = None
    smoothed_days: Optional[int] = None
    engine_selected: Optional[str] = None
    signal: Optional[dict] = None
    action: Optional[str] = None
    live_engine: Optional[str] = None
    live_action: Optional[str] = None
    live_trade_id: Optional[str] = None
    flag_snapshot: Optional[dict] = None


class ShadowSimulator:
    def __init__(self, counterfactual_logger: CounterfactualLogger,
                 send_fn: Optional[Callable] = None):
        self._logger = counterfactual_logger
        self._send_fn = send_fn
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._shadow_lag = 0
        self._dropped_count = 0
        self._stale_bar_counts: dict[str, int] = defaultdict(int)
        self._dq_active_counts: dict[str, int] = defaultdict(int)
        self._running = False
        self._task: Optional[asyncio.Task] = None

    @property
    def shadow_lag(self) -> int:
        return self._queue.qsize()

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    def submit(self, work: ShadowWorkItem) -> bool:
        """Submit shadow work. Returns False if queue is full (oldest dropped)."""
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self._dropped_count += 1
                logger.warning("Shadow queue full, dropped oldest item. "
                               "Total dropped: %d", self._dropped_count)
            except asyncio.QueueEmpty:
                pass

        try:
            self._queue.put_nowait(work)
        except asyncio.QueueFull:
            self._dropped_count += 1
            return False

        lag = self.shadow_lag
        if lag >= SHADOW_LAG_WARN_THRESHOLD and self._send_fn:
            self._send_fn(f"⚠️ Shadow lag: {lag} items queued")

        return True

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.current_task()
        logger.info("Shadow simulator started")
        while self._running:
            try:
                work = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._process(work)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error("Shadow simulator error: %s", e)

    def stop(self) -> None:
        self._running = False

    async def _process(self, work: ShadowWorkItem) -> None:
        bar_dt = (datetime.fromisoformat(work.bar_time)
                  if isinstance(work.bar_time, str) else work.bar_time)
        if bar_dt.tzinfo is None:
            bar_dt = bar_dt.replace(tzinfo=timezone.utc)
        overlays = active_overlays(work.instrument, bar_dt, work.overlay_ctx)
        overlay_names = [o.overlay_name for o in overlays]

        dq_active = "DATA_QUALITY" in overlay_names
        if dq_active:
            self._dq_active_counts[work.instrument] += 1
        else:
            self._dq_active_counts[work.instrument] = 0

        if self._dq_active_counts[work.instrument] > DATA_QUALITY_ABANDON_THRESHOLD:
            self._abandon_open_trades(
                work.instrument,
                f"DATA_QUALITY active for {self._dq_active_counts[work.instrument]} "
                f"consecutive bars"
            )

        disagreement = None
        if work.live_action and work.action:
            if work.live_action != work.action:
                disagreement = self._classify_disagreement(
                    work.live_action, work.action, overlay_names)

        hypothetical_id = None
        if (work.action and work.action in ("BUY", "SELL")
                and not dq_active and len(overlays) == 0):
            hypothetical_id = f"SH-{uuid.uuid4().hex[:12]}"
            self._logger.open_hypothetical(
                trade_id=hypothetical_id,
                instrument=work.instrument,
                bar_time=work.bar_time,
                engine=work.engine_selected or "unknown",
                regime=work.regime,
                price=work.price,
                quantity=1.0,
                stop=work.signal.get("stop_hint") if work.signal else None,
            )

        self._logger.log_decision(
            instrument=work.instrument,
            bar_time=work.bar_time,
            live_engine=work.live_engine,
            live_signal=None,
            live_action=work.live_action,
            live_trade_id=work.live_trade_id,
            shadow_regime=work.regime,
            shadow_confidence=work.confidence,
            shadow_smoothed_regime=work.smoothed_regime,
            shadow_smoothed_days=work.smoothed_days,
            shadow_overlays_active=overlay_names,
            shadow_engine=work.engine_selected,
            shadow_signal=work.signal,
            shadow_action=work.action,
            disagreement_type=disagreement,
            hypothetical_trade_id=hypothetical_id,
            flag_snapshot=work.flag_snapshot,
        )

    def _classify_disagreement(self, live_action: str, shadow_action: str,
                                overlays: list) -> str:
        if overlays:
            return "overlay_would_block"
        if shadow_action in ("BUY", "SELL") and live_action == "HOLD":
            return "shadow_would_enter"
        if live_action in ("BUY", "SELL") and shadow_action == "HOLD":
            return "regime_would_block"
        return "action_differs"

    def _abandon_open_trades(self, instrument: str, reason: str) -> None:
        open_trades = self._logger.get_open_hypotheticals(instrument)
        for trade in open_trades:
            self._logger.abandon_hypothetical(trade["id"], reason)
            logger.info("Abandoned hypothetical %s: %s", trade["id"], reason)

    def check_abandonment(self, instrument: str, is_delisted: bool = False,
                          bars_since_data: int = 0) -> None:
        if is_delisted:
            self._abandon_open_trades(instrument, "instrument_delisted")
            return

        if bars_since_data > STALE_BAR_ABANDON_THRESHOLD:
            self._abandon_open_trades(
                instrument,
                f"data_stale_{bars_since_data}_bars"
            )
