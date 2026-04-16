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
import json
import os
from pathlib import Path
from bot.plugins.base_plugin import BasePlugin
from bot.logger import log
from bot.currency import convert_pnl_to_base

BASE_DIR = Path(__file__).parent.parent.parent
DB_FILE = str(BASE_DIR / 'learning_loop.db')


class LearningLoop(BasePlugin):

    name = "LearningLoop"

    def __init__(self, cfg, llm=None, alerts=None):
        self.cfg = cfg
        self.db  = DB_FILE
        self.llm = llm
        self.alerts = alerts
        self._review_enabled = getattr(cfg, '_raw', {}).get('settings', {}).get('llm_review_enabled', False)
        self._last_retrain_time = datetime.datetime.utcnow()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

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
        """Backup exit check + weekly retrain trigger (time-based)."""
        self._check_exits(signal_rows)

        # Weekly retrain trigger — time-based (every 7 days regardless of cycle interval)
        elapsed = datetime.datetime.utcnow() - self._last_retrain_time
        if elapsed > datetime.timedelta(days=7):
            self._retrain()
            self._last_retrain_time = datetime.datetime.utcnow()

    # ── Entry recording ───────────────────────────────────────

    def _record_entry(self, inst: dict, signal: int,
                      action: str, entry_price: float) -> None:
        bundle = inst.get('_last_bundle')
        if bundle is None:
            return

        conn = self._connect()
        conn.execute("""
            INSERT INTO trades
            (timestamp, symbol, name, action, entry_price, qty,
             alligator_state, alligator_dir, ma200_trend,
             wr_value, wr_signal, rsi_value, confidence, open, currency)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
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
            inst.get('currency', 'USD'),
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
        # Guard: never record garbage exit prices
        if exit_price is None or exit_price <= 0:
            log(f"[LearningLoop] REFUSING to record trade with "
                f"exit_price={exit_price} for {symbol}", "ERROR")
            return

        conn   = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, entry_price, qty, action, timestamp, currency "
            "FROM trades WHERE symbol=? AND open=1 "
            "ORDER BY id DESC LIMIT 1",
            (symbol,)
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return

        trade_id, entry_price_db, qty, trade_action, entry_ts, currency = row
        currency = currency or 'USD'

        # Calculate P&L
        if trade_action == 'BUY':
            pnl = (exit_price - entry_price_db) * qty
        else:
            pnl = (entry_price_db - exit_price) * qty

        # GBP: prices are in pence, convert P&L to pounds
        pnl = convert_pnl_to_base(pnl, currency)

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

        # Check consecutive losses — auto-disable if threshold reached
        if outcome == 'LOSS':
            self._check_auto_disable(symbol)

        # LLM trade review
        if self._review_enabled and self.llm and self.llm.is_available():
            self._run_trade_review(trade_id, symbol, entry_price_db,
                                   exit_price, pnl, outcome, hold_days,
                                   exit_reason, trade_action, entry_ts)

    # ── Consecutive loss auto-disable ────────────────────────

    def _check_consecutive_losses(self, symbol: str) -> int:
        """Count consecutive losses for a symbol (most recent first)."""
        conn = self._connect()
        rows = conn.execute("""
            SELECT outcome FROM trades
            WHERE symbol = ? AND outcome IS NOT NULL
            ORDER BY id DESC LIMIT 5
        """, (symbol,)).fetchall()
        conn.close()

        consecutive = 0
        for row in rows:
            if row[0] == "LOSS":
                consecutive += 1
            else:
                break
        return consecutive

    def _check_auto_disable(self, symbol: str) -> None:
        """After a loss, check if instrument should be auto-disabled."""
        consecutive = self._check_consecutive_losses(symbol)
        settings = getattr(self.cfg, '_raw', {}).get('settings', {})
        max_consecutive = settings.get("max_consecutive_losses", 3)
        if consecutive >= max_consecutive:
            log(f"[LearningLoop] [{symbol}] {consecutive} consecutive losses "
                f"— auto-disabling. Manual re-enable required.", "WARN")
            self._disable_instrument(symbol)
            self._send_alert(
                f"[{symbol}] auto-disabled after {consecutive} consecutive losses"
            )

    def _disable_instrument(self, symbol: str) -> None:
        """Set enabled=false in instruments.json for the given symbol."""
        config_path = str(BASE_DIR / 'instruments.json')
        try:
            with open(config_path) as f:
                data = json.load(f)

            for inst in data.get('layer1_active', []):
                if inst.get('symbol') == symbol:
                    inst['enabled'] = False
                    inst['disabled_reason'] = 'auto_disabled_consecutive_losses'
                    break

            # Atomic write
            tmp_path = config_path + '.tmp'
            with open(tmp_path, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, config_path)

            log(f"[LearningLoop] Disabled {symbol} in instruments.json")
        except Exception as e:
            log(f"[LearningLoop] Failed to disable {symbol}: {e}", "ERROR")

    def _send_alert(self, message: str) -> None:
        """Send alert via Telegram if available."""
        if self.alerts and hasattr(self.alerts, 'send'):
            self.alerts.send(message)

    # ── Backup exit detection ─────────────────────────────────

    def _check_exits(self, signal_rows: list) -> None:
        """Safety net: mark open trades as closed if IBKR position is now 0."""
        conn   = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, symbol, entry_price, qty, action, timestamp, currency "
            "FROM trades WHERE open=1"
        )
        open_trades = cursor.fetchall()

        # Build map: symbol → current position qty
        positions_now = {r['symbol']: r.get('pos', 0) for r in signal_rows}

        for trade_id, symbol, entry_price, qty, action, entry_ts, currency in open_trades:
            currency = currency or 'USD'
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
            # Guard: never record garbage exit prices
            if exit_price is None or exit_price <= 0:
                log(f"[LearningLoop] Backup exit skipped: {symbol} "
                    f"exit_price={exit_price}", "ERROR")
                continue

            if action == 'BUY':
                pnl = (exit_price - entry_price) * qty
            else:
                pnl = (entry_price - exit_price) * qty

            # GBP: prices are in pence, convert P&L to pounds
            pnl = convert_pnl_to_base(pnl, currency)

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
            conn   = self._connect()
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

    # ── LLM Trade Review ────────────────────────────────────────

    def _run_trade_review(self, trade_id: int, symbol: str,
                          entry_price: float, exit_price: float,
                          pnl: float, outcome: str, hold_days: int,
                          exit_reason: str, action: str, entry_ts: str) -> None:
        """Run LLM review on a completed trade."""
        try:
            from bot.llm.reviewer import review_trade

            bars_at_entry = self._get_bars_from_db(symbol, entry_ts, 10)
            bars_at_exit = self._get_bars_from_db(
                symbol, datetime.datetime.utcnow().isoformat(), 10
            )

            if bars_at_entry is None or bars_at_exit is None:
                log(f"[LearningLoop] Skipping trade review — no OHLCV data in backtest.db")
                return

            trade_data = {
                "symbol": symbol,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl": pnl,
                "outcome": outcome,
                "hold_days": hold_days,
                "exit_reason": exit_reason,
                "action": action,
                "indicators_at_entry": {},
            }

            review = review_trade(self.llm, trade_data, bars_at_entry, bars_at_exit)
            self._save_review(trade_id, symbol, review)
            log(f"[LearningLoop] Trade review: {review.get('analysis', 'N/A')[:100]}")

        except Exception as e:
            log(f"[LearningLoop] Trade review failed: {e}", "WARN")

    def _get_bars_from_db(self, symbol: str, around_time: str,
                          lookback: int = 10) -> list | None:
        """Get OHLCV bars from backtest.db around a given time."""
        try:
            backtest_db = str(BASE_DIR / 'backtest.db')
            if not os.path.exists(backtest_db):
                return None

            conn = sqlite3.connect(backtest_db)
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT datetime, open, high, low, close, volume "
                "FROM bars WHERE symbol = ? AND datetime <= ? "
                "ORDER BY datetime DESC LIMIT ?",
                (symbol, around_time[:10], lookback)
            )
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                return None

            return [dict(row) for row in reversed(rows)]

        except Exception:
            return None

    def _save_review(self, trade_id: int, symbol: str, review: dict) -> None:
        """Save trade review to learning_loop.db."""
        try:
            conn = self._connect()
            conn.execute("""
                INSERT INTO trade_reviews
                (trade_id, symbol, timestamp, analysis, entry_quality,
                 exit_quality, pattern_at_entry, lesson, raw_response)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_id, symbol,
                datetime.datetime.utcnow().isoformat(),
                review.get("analysis", ""),
                review.get("entry_quality", ""),
                review.get("exit_quality", ""),
                review.get("pattern_at_entry", ""),
                review.get("suggestion", ""),
                review.get("raw_response", ""),
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            log(f"[LearningLoop] Failed to save review: {e}", "WARN")

    # ── Database init ─────────────────────────────────────────

    def _init_db(self) -> None:
        conn = self._connect()
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
                open            INTEGER DEFAULT 1,
                currency        TEXT DEFAULT 'USD'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                analysis TEXT,
                entry_quality TEXT,
                exit_quality TEXT,
                pattern_at_entry TEXT,
                lesson TEXT,
                raw_response TEXT,
                FOREIGN KEY (trade_id) REFERENCES trades(id)
            )
        """)
        # Add currency column to existing databases
        try:
            conn.execute("ALTER TABLE trades ADD COLUMN currency TEXT DEFAULT 'USD'")
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.commit()
        conn.close()
