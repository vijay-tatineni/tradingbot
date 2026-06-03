"""
Shadow trade simulator (Commit 2 of the regime-filter experiment).

When the regime filter blocks an entry, this simulator opens a
hypothetical position with the same entry price and quantity the live
path would have used, then ticks the same tier-1 (emergency stop) /
tier-2 (trail stop + take profit) exit logic that
PositionTracker.check_emergency_stop / check_exit applies to live
positions. On exit, computes P&L and writes back to
shadow_hypothetical_trades via CounterfactualLogger.

Peak price and current trail stop persist across restarts via two
extra columns on shadow_hypothetical_trades (added in-place on init).
"""
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("shadow.trade_simulator")


@dataclass
class ShadowPosState:
    trade_id: str
    instrument: str
    side: str               # "LONG" or "SHORT"
    entry_price: float
    qty: float
    peak_price: float
    trail_stop: float


_ADD_COLUMN_SQL = [
    "ALTER TABLE shadow_hypothetical_trades ADD COLUMN peak_price REAL",
    "ALTER TABLE shadow_hypothetical_trades ADD COLUMN trail_stop REAL",
]


class ShadowTradeSimulator:
    def __init__(self, counterfactual_logger):
        self._cf = counterfactual_logger
        # symbol → ShadowPosState for fast per-tick lookup. Rebuilt
        # from DB on startup so restarts don't lose in-flight shadows.
        self._open: dict[str, ShadowPosState] = {}
        self._ensure_schema()
        self._reload_from_db()

    # ── schema + persistence ────────────────────────────────

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self._cf._db_path) as conn:
            for stmt in _ADD_COLUMN_SQL:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e).lower():
                        raise

    def _reload_from_db(self) -> None:
        with sqlite3.connect(self._cf._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, instrument, entry_price, entry_quantity, "
                "entry_stop, peak_price, trail_stop "
                "FROM shadow_hypothetical_trades WHERE status = 'OPEN'"
            ).fetchall()
        for row in rows:
            sym = row["instrument"]
            entry = float(row["entry_price"])
            peak = (float(row["peak_price"])
                    if row["peak_price"] is not None else entry)
            stop = (float(row["trail_stop"])
                    if row["trail_stop"] is not None
                    else (float(row["entry_stop"])
                          if row["entry_stop"] is not None else entry))
            side = "LONG" if float(row["entry_quantity"]) >= 0 else "SHORT"
            self._open[sym] = ShadowPosState(
                trade_id=row["id"], instrument=sym, side=side,
                entry_price=entry, qty=abs(float(row["entry_quantity"])),
                peak_price=peak, trail_stop=stop,
            )

    def _persist_peak(self, state: ShadowPosState) -> None:
        with sqlite3.connect(self._cf._db_path) as conn:
            conn.execute(
                "UPDATE shadow_hypothetical_trades "
                "SET peak_price = ?, trail_stop = ? WHERE id = ?",
                (state.peak_price, state.trail_stop, state.trade_id),
            )

    # ── public API ──────────────────────────────────────────

    def open(self, instrument: str, side: str, price: float, qty: float,
             bar_time: str, regime: Optional[str],
             engine: str = "would_have_taken",
             trail_stop_pct: float = 2.0) -> Optional[str]:
        """Open a hypothetical position. Returns the trade_id, or None
        if a shadow position for this instrument is already open (we
        don't stack — the blocked signal we'd be opening on is
        essentially the same one we already opened on)."""
        if instrument in self._open:
            return None
        trade_id = str(uuid.uuid4())
        # Initial trail stop matches PositionTracker.on_open: distance
        # below entry by trail_stop_pct (above for SHORT).
        if side == "SHORT":
            init_stop = price * (1 + trail_stop_pct / 100)
        else:
            init_stop = price * (1 - trail_stop_pct / 100)
        try:
            self._cf.open_hypothetical(
                trade_id=trade_id, instrument=instrument,
                bar_time=bar_time, engine=engine, regime=regime,
                price=price, quantity=qty if side == "LONG" else -qty,
                stop=init_stop,
            )
        except Exception as e:
            logger.warning("shadow open failed for %s: %s",
                           instrument, e)
            return None
        state = ShadowPosState(
            trade_id=trade_id, instrument=instrument, side=side,
            entry_price=price, qty=qty,
            peak_price=price, trail_stop=init_stop,
        )
        self._open[instrument] = state
        # Persist the initial peak/trail so a restart can recover.
        try:
            self._persist_peak(state)
        except Exception as e:
            logger.warning("shadow persist after open failed for %s: %s",
                           instrument, e)
        return trade_id

    def tick(self, instrument: str, price: float, bar_closed: bool,
             bar_time: str, trail_stop_pct: float,
             take_profit_pct: float, emergency_stop_pct: float) -> Optional[str]:
        """Advance the shadow position (if any) for this instrument.
        Returns the exit reason string if the position closed this
        tick, else None.

        Tier 1 (every tick): emergency stop from entry.
        Tier 2 (bar close only): peak/trail-stop update + take profit.
        """
        state = self._open.get(instrument)
        if state is None:
            return None

        # Tier 1
        exit_reason = self._check_emergency_stop(state, price,
                                                 emergency_stop_pct)
        if exit_reason:
            self._close(state, price, exit_reason, bar_time)
            return exit_reason

        if not bar_closed:
            return None

        # Tier 2 — update peak/trail
        self._update_peak_and_trail(state, price, trail_stop_pct)
        # Then check take profit + trail stop
        exit_reason = self._check_take_profit(state, price,
                                              take_profit_pct)
        if not exit_reason:
            exit_reason = self._check_trail_stop(state, price)
        if exit_reason:
            self._close(state, price, exit_reason, bar_time)
            return exit_reason

        try:
            self._persist_peak(state)
        except Exception as e:
            logger.warning("shadow peak persist failed for %s: %s",
                           instrument, e)
        return None

    def open_positions(self) -> dict[str, ShadowPosState]:
        return dict(self._open)

    # ── exit-logic primitives (mirror PositionTracker) ──────

    @staticmethod
    def _check_emergency_stop(state: ShadowPosState, price: float,
                              emergency_stop_pct: float) -> Optional[str]:
        entry = state.entry_price
        if state.side == "SHORT":
            limit = entry * (1 + emergency_stop_pct / 100)
            if price >= limit:
                pnl_pct = (entry - price) / entry * 100
                return f"EMERGENCY_STOP {pnl_pct:+.1f}%"
        else:
            limit = entry * (1 - emergency_stop_pct / 100)
            if price <= limit:
                pnl_pct = (price - entry) / entry * 100
                return f"EMERGENCY_STOP {pnl_pct:+.1f}%"
        return None

    @staticmethod
    def _update_peak_and_trail(state: ShadowPosState, price: float,
                               trail_stop_pct: float) -> None:
        if state.side == "SHORT":
            if price < state.peak_price:
                state.peak_price = price
                new_stop = price * (1 + trail_stop_pct / 100)
                if new_stop < state.trail_stop:
                    state.trail_stop = new_stop
        else:
            if price > state.peak_price:
                state.peak_price = price
                new_stop = price * (1 - trail_stop_pct / 100)
                if new_stop > state.trail_stop:
                    state.trail_stop = new_stop

    @staticmethod
    def _check_take_profit(state: ShadowPosState, price: float,
                           take_profit_pct: float) -> Optional[str]:
        if take_profit_pct <= 0:
            return None
        entry = state.entry_price
        if state.side == "SHORT":
            target = entry * (1 - take_profit_pct / 100)
            if price <= target:
                gain_pct = (entry - price) / entry * 100
                return f"TAKE_PROFIT +{gain_pct:.1f}%"
        else:
            target = entry * (1 + take_profit_pct / 100)
            if price >= target:
                gain_pct = (price - entry) / entry * 100
                return f"TAKE_PROFIT +{gain_pct:.1f}%"
        return None

    @staticmethod
    def _check_trail_stop(state: ShadowPosState,
                          price: float) -> Optional[str]:
        if state.side == "SHORT":
            if price >= state.trail_stop:
                pnl_pct = (state.entry_price - price) / state.entry_price * 100
                return f"TRAIL_STOP {pnl_pct:+.1f}%"
        else:
            if price <= state.trail_stop:
                pnl_pct = (price - state.entry_price) / state.entry_price * 100
                return f"TRAIL_STOP {pnl_pct:+.1f}%"
        return None

    # ── close + bookkeeping ─────────────────────────────────

    def _close(self, state: ShadowPosState, exit_price: float,
               exit_reason: str, bar_time: str) -> None:
        entry = state.entry_price
        if state.side == "SHORT":
            pnl = (entry - exit_price) * state.qty
            pnl_pct = (entry - exit_price) / entry * 100
        else:
            pnl = (exit_price - entry) * state.qty
            pnl_pct = (exit_price - entry) / entry * 100
        try:
            self._cf.close_hypothetical(
                trade_id=state.trade_id, bar_time=bar_time,
                exit_price=exit_price, exit_reason=exit_reason,
                pnl=pnl, pnl_pct=pnl_pct,
            )
        except Exception as e:
            logger.warning("shadow close failed for %s: %s",
                           state.instrument, e)
        finally:
            self._open.pop(state.instrument, None)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
