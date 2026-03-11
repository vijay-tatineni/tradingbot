"""
bot/plugins/learning_loop.py
Learning Loop Plugin — Phase 1.

Records every trade with full market conditions at entry.
Records exit with P&L, hold_days, and exit_reason when positions close.
Weekly retraining trigger (placeholder for Phase 2 ML).

FIXED in v7.0:
  - Exits recorded via post_trade (not just signal_rows scan)
  - hold_days calculated from entry timestamp
  - exit_reason passed through from layer1 (not hardcoded)
  - _check_exits kept as backup safety net
"""

import sqlite3
import datetime
import os
from bot.plugins.base_plugin import BasePlugin
from bot.logger import log

DB_FILE = os.path.expanduser('~/trading/learning_loop.db')


class LearningLoop(BasePlugin):

    name = "LearningLoop"

    def __init__(self, cfg):
        self.cfg = cfg
        self.db  = DB_FILE
        self._init_db()

    # ── Lifecycle hooks ───────────────────────────────────────

    def on_start(self) -> None:
        log(f"[LearningLoop] Started — database: {self.db}")
        stats = self._get_stats()
        log(f"[LearningLoop] Trades recorded: {stats['total']}  |  "
            f"Wins: {stats['wins']}  |  Losses: {stats['losses']}")

    def post_trade(self, inst: dict, signal: int,
                   action: str, entry_price: float) -> None:
        """
        Record trade entries AND exits.
        Entries: action contains 'BOUGHT', 'SHORT', or 'RE-ENTRY'
        Exits:   action contains 'CLOSED'
        """
        if any(kw in action for kw in ('BOUGHT', 'SHORT', 'RE-ENTRY')):
            self._record_entry(inst, signal, action, entry_price)
        elif 'CLOSED' in action:
            self._record_exit(inst['symbol'], entry_price, action)

    def on_cycle_end(self, cycle: int, signal_rows: list,
                     total_pnl: float) -> None:
        """Backup exit check + weekly retrain trigger."""
        self._check_exits(signal_rows)

        # Weekly retrain trigger (every 672 cycles at 15min = 7 days)
        if cycle % 672 == 0:
            self._retrain()

    # ── Entry recording ───────────────────────────────────────

    def _record_entry(self, inst: dict, signal: int,
                      action: str, entry_price: float) -> None:
        bundle = inst.get('_last_bundle')
        if bundle is None:
            return

        conn = sqlite3.connect(self.db)
        conn.execute("""
            INSERT INTO trades
            (timestamp, symbol, name, action, entry_price, qty,
             alligator_state, alligator_dir, ma200_trend,
             wr_value, wr_signal, rsi_value, confidence, open)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            datetime.datetime.utcnow().isoformat(),
            inst['symbol'],
            inst['name'],
            'BUY' if signal >= 0 else 'SELL',
            entry_price,
            inst['qty'],
            bundle.alligator.state,
            bundle.alligator.direction,
            bundle.ma200.trend,
            bundle.wr.value,
            bundle.wr.signal,
            bundle.rsi,
            inst.get('_last_confidence', 'UNKNOWN'),
        ))
        conn.commit()
        conn.close()
        log(f"[LearningLoop] Recorded entry: "
            f"{'BUY' if signal >= 0 else 'SELL'} {inst['symbol']} "
            f"@ {entry_price}")

    # ── Exit recording ────────────────────────────────────────

    def _record_exit(self, symbol: str, exit_price: float,
                     action: str) -> None:
        """Close the most recent open trade for this symbol."""
        conn   = sqlite3.connect(self.db)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, entry_price, qty, action, timestamp "
            "FROM trades WHERE symbol=? AND open=1 "
            "ORDER BY id DESC LIMIT 1",
            (symbol,)
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return

        trade_id, entry_price_db, qty, trade_action, entry_ts = row

        # Calculate P&L
        if trade_action == 'BUY':
            pnl = (exit_price - entry_price_db) * qty
        else:
            pnl = (entry_price_db - exit_price) * qty

        outcome = 'WIN' if pnl > 0 else 'LOSS' if pnl < 0 else 'SCRATCH'

        # Calculate hold_days
        hold_days = 0
        try:
            entry_dt  = datetime.datetime.fromisoformat(entry_ts)
            hold_days = (datetime.datetime.utcnow() - entry_dt).days
        except Exception:
            pass

        # Extract exit reason from action string e.g. "CLOSED (TRAIL_STOP +2.1%)"
        exit_reason = action
        if '(' in action and ')' in action:
            exit_reason = action[action.index('(') + 1 : action.index(')')]

        cursor.execute("""
            UPDATE trades
            SET exit_price=?, pnl_usd=?, outcome=?, open=0,
                hold_days=?, exit_reason=?
            WHERE id=?
        """, (round(exit_price, 4), round(pnl, 2), outcome,
              hold_days, exit_reason, trade_id))
        conn.commit()
        conn.close()
        log(f"[LearningLoop] Closed: {symbol}  P&L: ${pnl:.2f}  "
            f"Outcome: {outcome}  Hold: {hold_days}d  Reason: {exit_reason}")

    # ── Backup exit detection ─────────────────────────────────

    def _check_exits(self, signal_rows: list) -> None:
        """Safety net: mark open trades as closed if IBKR position is now 0."""
        conn   = sqlite3.connect(self.db)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, symbol, entry_price, qty, action, timestamp "
            "FROM trades WHERE open=1"
        )
        open_trades = cursor.fetchall()

        # Build map: symbol → current position qty
        positions_now = {r['symbol']: r.get('pos', 0) for r in signal_rows}

        for trade_id, symbol, entry_price, qty, action, entry_ts in open_trades:
            current_pos = positions_now.get(symbol)
            if current_pos is None:
                continue  # symbol not in signal_rows (e.g. market closed), skip
            if current_pos != 0:
                continue  # still open

            # Position closed but exit wasn't recorded via post_trade
            exit_price = next(
                (r['price'] for r in signal_rows if r['symbol'] == symbol),
                entry_price
            )
            if action == 'BUY':
                pnl = (exit_price - entry_price) * qty
            else:
                pnl = (entry_price - exit_price) * qty

            outcome = 'WIN' if pnl > 0 else 'LOSS' if pnl < 0 else 'SCRATCH'

            hold_days = 0
            try:
                entry_dt  = datetime.datetime.fromisoformat(entry_ts)
                hold_days = (datetime.datetime.utcnow() - entry_dt).days
            except Exception:
                pass

            cursor.execute("""
                UPDATE trades
                SET exit_price=?, pnl_usd=?, outcome=?, open=0,
                    hold_days=?, exit_reason='DETECTED_FLAT'
                WHERE id=?
            """, (round(exit_price, 4), round(pnl, 2), outcome,
                  hold_days, trade_id))
            log(f"[LearningLoop] Backup exit: {symbol}  P&L: ${pnl:.2f}  "
                f"Outcome: {outcome}")

        conn.commit()
        conn.close()

    # ── Stats & retrain ───────────────────────────────────────

    def _get_stats(self) -> dict:
        try:
            conn   = sqlite3.connect(self.db)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM trades WHERE open=0")
            total = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM trades WHERE outcome='WIN'")
            wins = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM trades WHERE outcome='LOSS'")
            losses = cursor.fetchone()[0]
            conn.close()
            return {'total': total, 'wins': wins, 'losses': losses}
        except Exception:
            return {'total': 0, 'wins': 0, 'losses': 0}

    def _retrain(self) -> None:
        """Phase 2: Retrain ML model on trade history."""
        stats = self._get_stats()
        log(f"[LearningLoop] Retrain check — {stats['total']} closed trades")
        if stats['total'] < 50:
            log(f"[LearningLoop] Need 50+ trades. "
                f"Currently {stats['total']}. Skipping.")
            return
        log("[LearningLoop] TODO: Train Random Forest model — Phase 2")

    # ── Database init ─────────────────────────────────────────

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT,
                symbol          TEXT,
                name            TEXT,
                action          TEXT,
                entry_price     REAL,
                exit_price      REAL,
                qty             REAL,
                pnl_usd         REAL,
                hold_days       INTEGER,
                outcome         TEXT,
                alligator_state TEXT,
                alligator_dir   TEXT,
                ma200_trend     TEXT,
                wr_value        REAL,
                wr_signal       TEXT,
                rsi_value       REAL,
                confidence      TEXT,
                exit_reason     TEXT,
                open            INTEGER DEFAULT 1
            )
        """)
        conn.commit()
        conn.close()
