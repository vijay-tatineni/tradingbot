"""
bot/position_tracker.py  — v7.0
Tracks peak prices, trailing stops, and re-entry signals.
All state persisted to SQLite so trail stops survive restarts.

FIXED in v7.0:
  - SQLite persistence for all position state
  - GBP pence/pounds: removed broken < 500 heuristic.
    IBKR avgCost is ALWAYS in pounds for GBP.
    Market prices are ALWAYS in pence for LSE.
    Conversion only applied to IBKR avgCost, never to market prices.
  - Cooldown timer: fixed .seconds → .total_seconds()
"""

import sqlite3
import datetime
import os
from dataclasses import dataclass
from typing import Optional
from bot.logger import log

DB_FILE = os.path.expanduser('~/trading/positions.db')


@dataclass
class PositionState:
    symbol:        str
    entry_price:   float   # in market quote units (pence for GBP/LSE)
    entry_time:    str      # ISO format for SQLite
    peak_price:    float
    current_price: float = 0.0
    trail_stop:    float = 0.0
    qty:           float = 0.0
    currency:      str   = 'USD'


@dataclass
class WatchState:
    symbol:            str
    exit_price:        float
    exit_time:         str   # ISO format
    exit_reason:       str
    low_since_exit:    float
    recovery_pct:      float = 0.0
    cooldown_mins:     int   = 30
    reentry_triggered: bool  = False


def ibkr_avg_cost_to_market(avg_cost: float, currency: str) -> float:
    """
    Convert IBKR avgCost to market price units.
    IBKR returns avgCost in GBP pounds; LSE quotes in pence.
    Always multiply by 100 for GBP — no heuristic threshold.
    """
    if currency == 'GBP':
        return avg_cost * 100
    return avg_cost


class PositionTracker:

    def __init__(self, cfg):
        self.cfg      = cfg
        self.db_path  = DB_FILE
        self.open     : dict[str, PositionState] = {}
        self.watching : dict[str, WatchState]    = {}
        self._init_db()
        self._load_state()

    # ── SQLite persistence ──────────────────────────

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS open_positions (
                symbol        TEXT PRIMARY KEY,
                entry_price   REAL,
                entry_time    TEXT,
                peak_price    REAL,
                current_price REAL,
                trail_stop    REAL,
                qty           REAL,
                currency      TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watch_positions (
                symbol            TEXT PRIMARY KEY,
                exit_price        REAL,
                exit_time         TEXT,
                exit_reason       TEXT,
                low_since_exit    REAL,
                recovery_pct      REAL,
                cooldown_mins     INTEGER,
                reentry_triggered INTEGER
            )
        """)
        conn.commit()
        conn.close()

    def _load_state(self) -> None:
        """Load persisted state from SQLite on startup."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        for row in conn.execute("SELECT * FROM open_positions"):
            state = PositionState(
                symbol=row['symbol'], entry_price=row['entry_price'],
                entry_time=row['entry_time'], peak_price=row['peak_price'],
                current_price=row['current_price'], trail_stop=row['trail_stop'],
                qty=row['qty'], currency=row['currency'],
            )
            self.open[state.symbol] = state
            log(f"[Tracker] Restored: {state.symbol} entry={state.entry_price:.4f} "
                f"stop={state.trail_stop:.4f} peak={state.peak_price:.4f}")

        for row in conn.execute("SELECT * FROM watch_positions"):
            watch = WatchState(
                symbol=row['symbol'], exit_price=row['exit_price'],
                exit_time=row['exit_time'], exit_reason=row['exit_reason'],
                low_since_exit=row['low_since_exit'],
                recovery_pct=row['recovery_pct'],
                cooldown_mins=row['cooldown_mins'],
                reentry_triggered=bool(row['reentry_triggered']),
            )
            self.watching[watch.symbol] = watch

        conn.close()
        if self.open:
            log(f"[Tracker] Loaded {len(self.open)} open positions from DB")
        if self.watching:
            log(f"[Tracker] Loaded {len(self.watching)} watched positions from DB")

    def _save_open(self, symbol: str) -> None:
        state = self.open[symbol]
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO open_positions
            (symbol, entry_price, entry_time, peak_price,
             current_price, trail_stop, qty, currency)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (state.symbol, state.entry_price, state.entry_time,
              state.peak_price, state.current_price, state.trail_stop,
              state.qty, state.currency))
        conn.commit()
        conn.close()

    def _delete_open(self, symbol: str) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM open_positions WHERE symbol=?", (symbol,))
        conn.commit()
        conn.close()

    def _save_watch(self, symbol: str) -> None:
        watch = self.watching[symbol]
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO watch_positions
            (symbol, exit_price, exit_time, exit_reason, low_since_exit,
             recovery_pct, cooldown_mins, reentry_triggered)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (watch.symbol, watch.exit_price, watch.exit_time,
              watch.exit_reason, watch.low_since_exit, watch.recovery_pct,
              watch.cooldown_mins, int(watch.reentry_triggered)))
        conn.commit()
        conn.close()

    def _delete_watch(self, symbol: str) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM watch_positions WHERE symbol=?", (symbol,))
        conn.commit()
        conn.close()

    # ── Open position management ──────────────────

    def on_open(self, symbol: str, entry_price: float, qty: float,
                trail_stop_pct: float, currency: str = 'USD') -> None:
        """
        Call when a new position is opened.
        entry_price must be in market quote units (pence for LSE).
        """
        stop = entry_price * (1 - trail_stop_pct / 100)
        now  = datetime.datetime.utcnow().isoformat()

        state = PositionState(
            symbol=symbol, entry_price=entry_price, entry_time=now,
            peak_price=entry_price, current_price=entry_price,
            qty=qty, trail_stop=stop, currency=currency,
        )
        self.open[symbol] = state
        self._save_open(symbol)

        if symbol in self.watching:
            self.watching.pop(symbol)
            self._delete_watch(symbol)

        log(f"[Tracker] OPEN {symbol} @ {entry_price:.4f} {currency}  "
            f"trail stop: {stop:.4f}")

    def init_existing(self, symbol: str, ibkr_avg_cost: float,
                      qty: float, trail_stop_pct: float,
                      currency: str = 'USD') -> None:
        """
        Register a position that existed before the bot started.
        ibkr_avg_cost comes from IBKR (pounds for GBP) — converted here.
        """
        if symbol not in self.open:
            entry = ibkr_avg_cost_to_market(ibkr_avg_cost, currency)
            self.on_open(symbol, entry, qty, trail_stop_pct, currency)
            log(f"[Tracker] Registered existing: {symbol} @ "
                f"{entry:.4f} {currency}")

    def update(self, symbol: str, price: float,
               trail_stop_pct: float, currency: str = 'USD') -> None:
        """
        Update peak price and trail stop.
        Price is market price (already in pence for LSE).
        Trail stop moves UP only, never down.
        """
        if symbol not in self.open:
            return
        state = self.open[symbol]
        state.current_price = price

        if price > state.peak_price:
            state.peak_price = price
            new_stop = price * (1 - trail_stop_pct / 100)
            if new_stop > state.trail_stop:
                state.trail_stop = new_stop
                log(f"[Tracker] {symbol} new peak {price:.4f}  "
                    f"trail stop raised to {state.trail_stop:.4f}")

        self._save_open(symbol)

    def check_exit(self, symbol: str, price: float,
                   take_profit_pct: float, trail_stop_pct: float,
                   currency: str = 'USD') -> Optional[str]:
        """
        Returns exit reason if position should be closed, else None.
        Price is in market quote units — matches entry_price.
        """
        if symbol not in self.open:
            return None

        state = self.open[symbol]
        entry = state.entry_price

        # Take profit
        if take_profit_pct > 0:
            target = entry * (1 + take_profit_pct / 100)
            if price >= target:
                gain_pct = ((price - entry) / entry) * 100
                log(f"[Tracker] TAKE PROFIT {symbol}  "
                    f"price {price:.4f} >= target {target:.4f}  "
                    f"gain: +{gain_pct:.2f}%")
                return f"TAKE_PROFIT +{gain_pct:.1f}%"

        # Trailing stop
        if trail_stop_pct > 0 and price <= state.trail_stop:
            pnl_pct = ((price - entry) / entry) * 100
            log(f"[Tracker] TRAIL STOP {symbol}  "
                f"price {price:.4f} <= stop {state.trail_stop:.4f}  "
                f"P&L: {pnl_pct:+.2f}%")
            return f"TRAIL_STOP {pnl_pct:+.1f}%"

        return None

    def on_close(self, symbol: str, exit_price: float,
                 reason: str, cooldown_mins: int = 30) -> None:
        """Called when a position is closed. Persists watch state to DB."""
        self.open.pop(symbol, None)
        self._delete_open(symbol)

        now = datetime.datetime.utcnow().isoformat()
        self.watching[symbol] = WatchState(
            symbol=symbol, exit_price=exit_price, exit_time=now,
            exit_reason=reason, low_since_exit=exit_price,
            cooldown_mins=cooldown_mins,
        )
        self._save_watch(symbol)
        log(f"[Tracker] WATCHING {symbol} for re-entry  "
            f"exit: {exit_price:.4f}  reason: {reason}")

    # ── Re-entry detection ────────────────────────

    def check_reentry(self, symbol: str, price: float,
                      signal_valid: bool,
                      reentry_recovery_pct: float = 1.5) -> tuple[bool, str]:
        """Returns (should_reenter, reason)."""
        if symbol not in self.watching:
            return False, ""

        watch = self.watching[symbol]

        # Cooldown — use total_seconds() not .seconds
        exit_time  = datetime.datetime.fromisoformat(watch.exit_time)
        mins_since = (datetime.datetime.utcnow() - exit_time).total_seconds() / 60
        if mins_since < watch.cooldown_mins:
            remaining = watch.cooldown_mins - mins_since
            return False, f"Cooldown {remaining:.0f}m remaining"

        # Track new lows after exit
        if price < watch.low_since_exit:
            watch.low_since_exit = price

        # Check recovery from low
        if watch.low_since_exit > 0:
            recovery = ((price - watch.low_since_exit) / watch.low_since_exit) * 100
            watch.recovery_pct = recovery
            self._save_watch(symbol)

            if recovery >= reentry_recovery_pct and signal_valid:
                reason = (f"RE-ENTRY: recovered +{recovery:.1f}% "
                         f"from low {watch.low_since_exit:.4f}")
                log(f"[Tracker] {reason}")
                return True, reason

        return False, f"Waiting +{reentry_recovery_pct}% recovery (now +{watch.recovery_pct:.1f}%)"

    def clear_watch(self, symbol: str) -> None:
        self.watching.pop(symbol, None)
        self._delete_watch(symbol)

    def get_stop_level(self, symbol: str) -> float:
        if symbol in self.open:
            return self.open[symbol].trail_stop
        return 0.0

    def get_peak(self, symbol: str) -> float:
        if symbol in self.open:
            return self.open[symbol].peak_price
        return 0.0

    def is_watching(self, symbol: str) -> bool:
        return symbol in self.watching

    def watch_info(self, symbol: str) -> Optional[WatchState]:
        return self.watching.get(symbol)
