"""
bot/layer3_silver.py
Layer 3 — Silver Scalper (SSLN, iShares Physical Silver, LSE, GBP).

Intraday momentum scalper. Runs every cycle (1 minute) during LSE hours only.
ALL state is persisted in SQLite — survives crashes and restarts.

ENTRY:
  - Track rolling day low (resets at 08:00 UTC)
  - Buy 164 shares when price rises 0.3% from day low
  - Only if no position currently open

EXIT (Trail Stop):
  - Initial trail stop at 0.2% below entry price
  - Ratchet trail stop up as price rises (never down)
  - Sell immediately when price drops through trail stop

RE-ENTRY:
  - 5 minute cooldown after any sell
  - Track new low after sell
  - Buy again when price rises 0.3% from new low
  - Maximum 5 trades per day
  - Stop trading for the day if down £50

RISK:
  - Force sell at 16:15 UTC (never hold overnight)
  - Telegram alert on every buy and sell
  - Log every trade to SQLite

STATE PERSISTENCE:
  - silver_scalper_state table holds full scalper state
  - Written on every price update
  - Read on startup to restore exact position (trail stop, day low, etc.)
  - If bot crashes at 10am and restarts at 10:05am, picks up exactly
"""

import sqlite3
import datetime
import os
from pathlib import Path
import pytz
from bot.config       import Config
from bot.connection   import IBConnection
from bot.market_hours import MarketHours
from bot.portfolio    import Portfolio
from bot.orders       import OrderManager
from bot.logger       import log, separator

BASE_DIR = Path(__file__).parent.parent
DB_FILE = str(BASE_DIR / 'layer3_silver.db')

# ── Scalper parameters ───────────────────────────────────────
BOUNCE_PCT         = 0.3    # % rise from day low to trigger buy
TRAIL_STOP_PCT     = 0.2    # % below entry/peak for trail stop
COOLDOWN_MINS      = 5      # minutes to wait after a sell
MAX_TRADES_PER_DAY = 5      # max round-trip trades per day
DAILY_LOSS_LIMIT   = 50.0   # £50 — stop trading for the day
FORCE_SELL_HOUR    = 16     # London local time (handles BST automatically)
FORCE_SELL_MIN     = 15     # force sell at 16:15 London time
LONDON_TZ          = pytz.timezone('Europe/London')
SYMBOL             = 'SSLN'


class SilverScalper:
    """
    Layer 3 — intraday silver scalper on SSLN.
    All state persisted in SQLite. Daily reset at 08:00 UTC.
    """

    def __init__(self, cfg: Config, ib_conn: IBConnection, alerts=None):
        self.cfg       = cfg
        self.ib_conn   = ib_conn
        self.hours     = MarketHours()
        self.portfolio = Portfolio(ib_conn, cfg)
        self.orders    = OrderManager(ib_conn, cfg)
        self.alerts    = alerts

        # ── Instrument config (loaded from instruments.json) ─
        self.inst : dict | None = None

        # ── SQLite ───────────────────────────────────────────
        self._init_db()

        # ── Restore state from DB ────────────────────────────
        self._state = self._load_state()
        if self._state['status'] != 'NEW':
            log(f"[L3 Silver] Restored state from DB: "
                f"status={self._state['status']}  "
                f"date={self._state['date']}  "
                f"day_low={self._state['day_low']:.2f}  "
                f"trail_stop={self._state['trail_stop']:.2f}  "
                f"trades={self._state['trades_today']}  "
                f"pnl=£{self._state['pnl_today']:+.2f}")

    # ── Public interface ─────────────────────────────────────

    def qualify(self, ib_conn: IBConnection) -> bool:
        """Find SSLN in layer3_silver config and qualify its contract."""
        instruments = self.cfg._raw.get('layer3_silver', [])
        enabled = [i for i in instruments if i.get('enabled', True)]
        if not enabled:
            log("[L3 Silver] No enabled instruments in layer3_silver config")
            return False

        qualified = ib_conn.qualify_contracts(enabled)
        if not qualified:
            log("[L3 Silver] Failed to qualify SSLN contract", "WARN")
            return False

        self.inst = qualified[0]
        log(f"[L3 Silver] Qualified: {self.inst['symbol']} "
            f"qty={self.inst['qty']}")
        return True

    def run(self) -> None:
        """Run one Layer 3 scalp cycle. Called every minute from main loop."""
        if self.inst is None:
            return

        now = datetime.datetime.now(datetime.timezone.utc)
        now_london = now.astimezone(LONDON_TZ)

        # Only trade during LSE hours
        if not self.hours.lse_open():
            return

        # ── Session reset at 08:00 London time each day ────
        today = now_london.strftime('%Y-%m-%d')
        if today != self._state['date']:
            self._reset_session(today)

        # ── Already stopped for the day? ─────────────────────
        if self._state['pnl_today'] <= -DAILY_LOSS_LIMIT:
            return

        # ── Fetch current price ──────────────────────────────
        price = self._get_price()
        if price is None or price <= 0:
            return

        # ── Track day low and day high ───────────────────────
        changed = False
        if self._state['day_low'] <= 0 or price < self._state['day_low']:
            self._state['day_low'] = price
            changed = True
        if self._state['day_high'] <= 0 or price > self._state['day_high']:
            self._state['day_high'] = price
            changed = True

        # ── Force sell at 16:15 London time (handles BST) ────
        if now_london.hour == FORCE_SELL_HOUR and now_london.minute >= FORCE_SELL_MIN:
            if self._state['status'] == 'IN_POSITION':
                self._sell(price, "FORCE_CLOSE_EOD")
            elif changed:
                self._save_state()
            return   # no new trades after 16:15

        # ── Sync position with IBKR ─────────────────────────
        if self._state['status'] == 'IN_POSITION':
            actual_pos = self.portfolio.get_position(self.inst['symbol'])
            if actual_pos <= 0:
                # Position was closed externally
                self._state['status']      = 'WATCHING'
                self._state['entry_price'] = 0.0
                self._state['peak_price']  = 0.0
                self._state['trail_stop']  = 0.0
                self._save_state()
                log("[L3 Silver] Position closed externally — synced")

        # ── EXIT: check trail stop ───────────────────────────
        if self._state['status'] == 'IN_POSITION':
            self._update_trail(price)
            if price <= self._state['trail_stop']:
                self._sell(price, "TRAIL_STOP")
            else:
                self._save_state()
            return

        # ── ENTRY checks ────────────────────────────────────
        # Max trades per day
        if self._state['trades_today'] >= MAX_TRADES_PER_DAY:
            if changed:
                self._save_state()
            return

        # Cooldown after last sell
        if self._state['status'] == 'COOLDOWN':
            last_sell = self._state['last_sell_time']
            if last_sell:
                try:
                    sell_dt = datetime.datetime.fromisoformat(last_sell)
                    if sell_dt.tzinfo is None:
                        sell_dt = sell_dt.replace(tzinfo=datetime.timezone.utc)
                    elapsed = (now - sell_dt).total_seconds()
                    if elapsed < COOLDOWN_MINS * 60:
                        if changed:
                            self._save_state()
                        return
                except (ValueError, TypeError):
                    pass
            # Cooldown expired — transition to WATCHING
            self._state['status'] = 'WATCHING'

        # Daily loss limit
        if self._state['pnl_today'] <= -DAILY_LOSS_LIMIT:
            msg = (f"🪙 <b>L3 Silver STOPPED</b> — "
                   f"daily loss £{abs(self._state['pnl_today']):.2f} "
                   f"hit £{DAILY_LOSS_LIMIT:.0f} limit")
            log(f"[L3 Silver] {msg}")
            self._alert(msg)
            self._save_state()
            return

        # ── BUY: price bounced 0.3% from day low ────────────
        if self._state['day_low'] > 0:
            bounce = (price - self._state['day_low']) / self._state['day_low'] * 100
            if bounce >= BOUNCE_PCT:
                self._buy(price)
                return

        # Persist any day_low/day_high changes even if no trade
        if changed:
            self._save_state()

    # ── Private: trade execution ─────────────────────────────

    def _buy(self, price: float) -> None:
        """Execute buy and set initial trail stop."""
        qty = self.inst['qty']
        ok  = self.orders.place(
            self.inst['contract'], 'BUY', qty, self.inst['name']
        )
        if not ok:
            return

        self._state['status']      = 'IN_POSITION'
        self._state['entry_price'] = price
        self._state['peak_price']  = price
        self._state['trail_stop']  = price * (1 - TRAIL_STOP_PCT / 100)
        self._state['trades_today'] += 1
        self._save_state()

        self._log_trade('BUY', price, 0.0)

        msg = (f"🟢 <b>🪙 SSLN BUY</b> — {qty} shares @ {price:.2f}p\n"
               f"Trail stop: {self._state['trail_stop']:.2f}p\n"
               f"Day low: {self._state['day_low']:.2f}p  |  "
               f"Trade #{self._state['trades_today']}")
        log(f"[L3 Silver] BUY {qty} @ {price:.2f}p  "
            f"trail={self._state['trail_stop']:.2f}  "
            f"trade #{self._state['trades_today']}")
        self._alert(msg)

    def _sell(self, price: float, reason: str) -> None:
        """Execute sell and record P&L."""
        qty = self.inst['qty']
        actual_pos = self.portfolio.get_position(self.inst['symbol'])
        sell_qty = actual_pos if actual_pos > 0 else qty

        ok = self.orders.place(
            self.inst['contract'], 'SELL', sell_qty, self.inst['name']
        )
        if not ok:
            return

        # P&L in pence (SSLN is GBP pence)
        pnl_pence  = (price - self._state['entry_price']) * sell_qty
        pnl_pounds = pnl_pence / 100.0

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        self._state['status']         = 'COOLDOWN'
        self._state['pnl_today']     += pnl_pounds
        self._state['entry_price']    = 0.0
        self._state['peak_price']     = 0.0
        self._state['trail_stop']     = 0.0
        self._state['last_sell_time'] = now_iso
        # Reset day_low to current price so new bounce tracks from here
        self._state['day_low']        = price
        self._save_state()

        self._log_trade('SELL', price, pnl_pounds, reason)

        emoji = '🟢' if pnl_pounds >= 0 else '🔴'
        msg = (f"{emoji} <b>🪙 SSLN SELL</b> — {sell_qty:.0f} shares "
               f"@ {price:.2f}p\n"
               f"P&L: £{pnl_pounds:+.2f}  |  Reason: {reason}\n"
               f"Daily P&L: £{self._state['pnl_today']:+.2f}  |  "
               f"Trades: {self._state['trades_today']}/{MAX_TRADES_PER_DAY}")
        log(f"[L3 Silver] SELL @ {price:.2f}p  P&L: £{pnl_pounds:+.2f}  "
            f"reason={reason}  daily_pnl=£{self._state['pnl_today']:+.2f}")
        self._alert(msg)

    # ── Trail stop logic ─────────────────────────────────────

    def _update_trail(self, price: float) -> None:
        """Ratchet trail stop up as price rises (never down).
        Also tracks peak_price for state persistence."""
        if price > self._state['peak_price']:
            self._state['peak_price'] = price
        new_stop = price * (1 - TRAIL_STOP_PCT / 100)
        if new_stop > self._state['trail_stop']:
            self._state['trail_stop'] = new_stop

    # ── Price fetching ───────────────────────────────────────

    def _get_price(self) -> float | None:
        """Get current SSLN price from IBKR (1-minute snapshot)."""
        try:
            bars = self.ib_conn.ib.reqHistoricalData(
                self.inst['contract'],
                endDateTime='',
                durationStr='120 S',
                barSizeSetting='1 min',
                whatToShow='TRADES',
                useRTH=True,
            )
            if bars:
                return bars[-1].close
            return None
        except Exception as e:
            log(f"[L3 Silver] Price fetch error: {e}", "WARN")
            return None

    # ── Session reset ────────────────────────────────────────

    def _reset_session(self, today: str) -> None:
        """Reset daily state for a new trading day. Persists to DB."""
        prev = self._state['date']
        if prev:
            log(f"[L3 Silver] New session: {today}  "
                f"(prev: {prev}, "
                f"trades: {self._state['trades_today']}, "
                f"pnl: £{self._state['pnl_today']:+.2f})")

        self._state['date']           = today
        self._state['status']         = 'WATCHING'
        self._state['day_low']        = 0.0
        self._state['day_high']       = 0.0
        self._state['entry_price']    = 0.0
        self._state['peak_price']     = 0.0
        self._state['trail_stop']     = 0.0
        self._state['trades_today']   = 0
        self._state['pnl_today']      = 0.0
        self._state['last_sell_time'] = None
        self._save_state()

    # ── Telegram alerts ──────────────────────────────────────

    def _alert(self, message: str) -> None:
        """Send Telegram alert if alerts plugin is available."""
        if self.alerts and hasattr(self.alerts, 'send'):
            self.alerts.send(message)

    # ── SQLite: state persistence ────────────────────────────

    def _init_db(self) -> None:
        conn = sqlite3.connect(DB_FILE)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS silver_scalper_state (
                symbol         TEXT PRIMARY KEY,
                date           TEXT,
                status         TEXT,
                day_low        REAL,
                day_high       REAL,
                entry_price    REAL,
                peak_price     REAL,
                trail_stop     REAL,
                trades_today   INTEGER,
                pnl_today      REAL,
                last_sell_time TEXT,
                last_updated   TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS layer3_trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT,
                symbol      TEXT,
                action      TEXT,
                price       REAL,
                qty         INTEGER,
                pnl_gbp     REAL,
                reason      TEXT,
                day_low     REAL,
                trail_stop  REAL,
                trade_num   INTEGER,
                daily_pnl   REAL,
                session     TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _load_state(self) -> dict:
        """Load scalper state from DB. Returns defaults if no row exists."""
        defaults = {
            'symbol':         SYMBOL,
            'date':           '',
            'status':         'NEW',
            'day_low':        0.0,
            'day_high':       0.0,
            'entry_price':    0.0,
            'peak_price':     0.0,
            'trail_stop':     0.0,
            'trades_today':   0,
            'pnl_today':      0.0,
            'last_sell_time': None,
            'last_updated':   '',
        }
        try:
            conn = sqlite3.connect(DB_FILE)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM silver_scalper_state WHERE symbol = ?",
                (SYMBOL,)
            ).fetchone()
            conn.close()

            if row is None:
                return defaults

            return {
                'symbol':         row['symbol'],
                'date':           row['date'] or '',
                'status':         row['status'] or 'WATCHING',
                'day_low':        row['day_low'] or 0.0,
                'day_high':       row['day_high'] or 0.0,
                'entry_price':    row['entry_price'] or 0.0,
                'peak_price':     row['peak_price'] or 0.0,
                'trail_stop':     row['trail_stop'] or 0.0,
                'trades_today':   row['trades_today'] or 0,
                'pnl_today':      row['pnl_today'] or 0.0,
                'last_sell_time': row['last_sell_time'],
                'last_updated':   row['last_updated'] or '',
            }
        except Exception as e:
            log(f"[L3 Silver] DB load error: {e}", "WARN")
            return defaults

    def _save_state(self) -> None:
        """Write full scalper state to DB (upsert on symbol)."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._state['last_updated'] = now_iso
        try:
            conn = sqlite3.connect(DB_FILE)
            conn.execute("""
                INSERT INTO silver_scalper_state
                    (symbol, date, status, day_low, day_high,
                     entry_price, peak_price, trail_stop,
                     trades_today, pnl_today, last_sell_time, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    date           = excluded.date,
                    status         = excluded.status,
                    day_low        = excluded.day_low,
                    day_high       = excluded.day_high,
                    entry_price    = excluded.entry_price,
                    peak_price     = excluded.peak_price,
                    trail_stop     = excluded.trail_stop,
                    trades_today   = excluded.trades_today,
                    pnl_today      = excluded.pnl_today,
                    last_sell_time = excluded.last_sell_time,
                    last_updated   = excluded.last_updated
            """, (
                self._state['symbol'],
                self._state['date'],
                self._state['status'],
                round(self._state['day_low'], 4),
                round(self._state['day_high'], 4),
                round(self._state['entry_price'], 4),
                round(self._state['peak_price'], 4),
                round(self._state['trail_stop'], 4),
                self._state['trades_today'],
                round(self._state['pnl_today'], 4),
                self._state['last_sell_time'],
                now_iso,
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            log(f"[L3 Silver] DB save error: {e}", "WARN")

    # ── SQLite: trade logging ────────────────────────────────

    def _log_trade(self, action: str, price: float,
                   pnl: float, reason: str = "") -> None:
        """Log a trade to the layer3_trades table."""
        try:
            conn = sqlite3.connect(DB_FILE)
            conn.execute("""
                INSERT INTO layer3_trades
                (timestamp, symbol, action, price, qty, pnl_gbp,
                 reason, day_low, trail_stop, trade_num, daily_pnl, session)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
                self._state['symbol'],
                action,
                round(price, 4),
                self.inst['qty'],
                round(pnl, 4),
                reason,
                round(self._state['day_low'], 4),
                round(self._state['trail_stop'], 4),
                self._state['trades_today'],
                round(self._state['pnl_today'], 4),
                self._state['date'],
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            log(f"[L3 Silver] DB trade log error: {e}", "WARN")
