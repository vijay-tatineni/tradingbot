"""
Trading Bot v7.2 — main.py

Main entry point and orchestrator. Owns the main loop, plugin registry,
watchdog, weekend sleep, and graceful shutdown handling.

All trading logic lives in bot/layer1.py (active), bot/layer2.py (accum),
and bot/layer3_silver.py (scalper). Config loaded from instruments.json.

To add a new plugin:
  1. Create bot/plugins/my_plugin.py (extend BasePlugin)
  2. Import it below
  3. Call bot.register_plugin(MyPlugin(cfg))

Plugins:
  LearningLoop    — records every trade to SQLite, time-based weekly retrain
  TelegramAlerts  — trade alerts, daily summary (deduped), error notifications
  SentimentEngine — Claude API news sentiment gate (blocks bad-news BUYs)

Run:   python3 main.py
Stop:  Ctrl+C (positions remain open on IBKR)
"""

import sys
import os
import json
import signal
import sqlite3
import datetime
import argparse
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from bot.config        import Config
from bot.brokers       import create_broker
from bot.market_hours  import MarketHours
from bot.layer1        import ActiveTrading
from bot.layer2        import Accumulation
from bot.layer3_silver import SilverScalper
from bot.dashboard     import Dashboard
from bot.logger        import log, banner, separator

BASE_DIR = Path(__file__).parent

# ── Active plugins ────────────────────────────────────────────
from bot.plugins.learning_loop import LearningLoop
from bot.plugins.sentiment     import SentimentEngine
from bot.alerts                import TelegramAlerts
from bot.watchdog              import Watchdog
from bot.llm                   import create_llm

# ── Future plugins (uncomment to activate) ────────────────────
# from bot.plugins.macro_filter import MacroFilter
# from bot.plugins.ml_override  import MLOverride


class TradingBot:
    """
    Main orchestrator.
    Owns the main loop, plugin registry, watchdog, and shutdown handling.
    All trading logic lives in layer1.py, layer2.py and their dependencies.
    """

    VERSION = "7.2"

    def __init__(self, broker_override: str = None, config_path: str = None):
        self.cfg     = Config(config_path) if config_path else Config()
        self.broker_type = broker_override or self.cfg._raw.get('settings', {}).get('broker', 'ibkr')
        self.broker  = create_broker(self.broker_type, self.cfg)
        self.hours   = MarketHours()
        self.plugins : list = []

        # ── LLM providers ─────────────────────────────────────
        settings = self.cfg._raw.get('settings', {})
        try:
            self.llm = create_llm(settings.get('llm_provider', 'groq'))
        except Exception:
            self.llm = None
        try:
            self.llm_review = create_llm(
                settings.get('llm_provider_review',
                             settings.get('llm_provider', 'groq')))
        except Exception:
            self.llm_review = None
        try:
            self.llm_advisor = create_llm(
                settings.get('llm_provider_advisor',
                             settings.get('llm_provider', 'groq')))
        except Exception:
            self.llm_advisor = None

        # News collection state
        self._last_news_collection = 0
        self._advisor_ran_this_week = False
        self._daily_summary_sent = False

        # ── Register active plugins ───────────────────────────
        self.alerts = TelegramAlerts(self.cfg)

        self.register_plugin(LearningLoop(self.cfg, llm=self.llm_review, alerts=self.alerts))
        self.register_plugin(self.alerts)

        self.register_plugin(SentimentEngine(self.cfg, alerts=self.alerts))

        # ── Uncomment to activate future plugins ──────────────
        # self.register_plugin(MacroFilter(self.cfg))
        # self.register_plugin(MLOverride(self.cfg))

        # ── Wire alerts to broker for order failure notifications ─
        self.broker.set_alerts(self.alerts)

        # ── Pass plugins to layer1 ────────────────────────────
        self.l1   = ActiveTrading(self.cfg, self.broker, self.plugins,
                                  alerts=self.alerts, llm=self.llm)
        self.l2   = Accumulation(self.cfg, self.broker)
        self.l3   = SilverScalper(self.cfg, self.broker, alerts=self.alerts)
        self.dash = Dashboard(self.cfg)

        # ── Watchdog: alert if bot appears stuck ──────────────
        stale_mins = max(self.cfg.check_interval_mins * 3, 10)
        self.watchdog = Watchdog(alerts=self.alerts, max_stale_mins=stale_mins)

        # ── Graceful shutdown on Ctrl+C ───────────────────────
        signal.signal(signal.SIGINT,  self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def register_plugin(self, plugin) -> None:
        """Register a plugin. Plugins run in registration order."""
        self.plugins.append(plugin)
        log(f"Plugin registered: {plugin.name}")

    def run(self) -> None:
        """Main loop — runs forever until Ctrl+C."""
        banner([
            f"Trading Bot v{self.VERSION}",
            f"Account  : {self.cfg.account}",
            f"Interval : every {self.cfg.check_interval_mins} minutes",
            f"Active   : {len(self.cfg.active_instruments)} instruments",
            f"Plugins  : {', '.join(p.name for p in self.plugins) or 'none'}",
        ])

        # Qualify all contracts on startup
        log("Qualifying Layer 1 contracts...")
        self.cfg.active_instruments = self.broker.qualify_contracts(self.cfg.active_instruments)
        log("Qualifying Layer 2 contracts...")
        self.cfg.accum_instruments  = self.broker.qualify_contracts(self.cfg.accum_instruments)
        log("Qualifying Layer 3 contracts...")
        self.l3.qualify(self.broker)

        # Notify plugins bot has started
        for plugin in self.plugins:
            plugin.on_start()

        # Start watchdog
        self.watchdog.start()

        cycle = 0

        while True:
            # ── Weekend sleep — no markets open ─────────────
            now = datetime.datetime.now(datetime.timezone.utc)
            if now.weekday() >= 5:  # Saturday=5, Sunday=6
                self.watchdog.set_sleep_mode(True)
                log("Weekend — markets closed, sleeping 1 hour")
                try:
                    self.broker.sleep(3600)
                except (ConnectionError, OSError, Exception) as e:
                    log(f"Weekend sleep connection error: {e} — using fallback sleep", "WARN")
                    import time as _time
                    _time.sleep(3600)
                continue

            self.watchdog.set_sleep_mode(False)
            if not self.broker.is_connected():
                log(f"Reconnecting to {self.broker_type.upper()} after weekend sleep...")
                self.broker.reconnect()
            cycle += 1
            separator(f"CYCLE #{cycle}  ·  "
                      f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            try:
                # Notify plugins cycle starting
                for plugin in self.plugins:
                    plugin.on_cycle_start(cycle)

                # ── Layer 1: Active trading (every cycle) ─────
                self.l1.run()

                # ── Layer 2: Accumulation (every 6 cycles) ────
                if cycle % 6 == 1:
                    self.l2.run()

                # ── Layer 3: Silver Scalper (every cycle, LSE hours) ─
                self.l3.run()

                # ── Dashboard update ──────────────────────────
                self.dash.update(
                    cycle       = cycle,
                    signal_rows = self.l1.signal_rows,
                    accum_rows  = self.l2.accum_rows,
                    total_pnl   = self.l1.total_pnl,
                    lse_open    = self.hours.lse_open(),
                    us_open     = self.hours.us_open(),
                )

                # ── Notify plugins cycle ended ────────────────
                for plugin in self.plugins:
                    plugin.on_cycle_end(cycle, self.l1.signal_rows,
                                        self.l1.total_pnl)

                # ── News collection (every N hours) ──────────
                self._maybe_collect_news()

                # ── Weekly advisor (Sunday 20:00 UTC) ────────
                self._maybe_run_advisor(now)

                # ── Daily P&L summary (21:00 UTC) ────────────
                if now.hour == 21 and now.minute < self.cfg.check_interval_mins and not self._daily_summary_sent:
                    self._send_daily_summary()
                    self._daily_summary_sent = True
                if now.hour == 22:
                    self._daily_summary_sent = False

                # ── Watchdog heartbeat ────────────────────────
                self.watchdog.heartbeat(cycle)

                log(f"Cycle #{cycle} complete. "
                    f"Next in {self.cfg.check_interval_mins} minutes.")
                self.broker.sleep(self.cfg.check_interval)

            except Exception as e:
                log(f"Cycle error: {e}", "ERROR")
                import traceback
                log(traceback.format_exc(), "ERROR")
                self.alerts.send_error(f"Cycle #{cycle} error: {e}")
                self.broker.reconnect()

    def _get_today_trades(self) -> list:
        """Get today's closed trades from learning_loop.db."""
        try:
            ll_db = str(BASE_DIR / 'learning_loop.db')
            if not os.path.exists(ll_db):
                return []
            conn = sqlite3.connect(ll_db)
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT symbol, pnl_usd, outcome FROM trades
                WHERE open = 0 AND outcome IS NOT NULL
                AND date(timestamp) = date('now')
            """)
            trades = [dict(r) for r in cursor.fetchall()]
            conn.close()
            return trades
        except Exception:
            return []

    def _send_daily_summary(self) -> None:
        """Send end-of-day P&L summary via Telegram."""
        try:
            today_trades = self._get_today_trades()
            daily_pnl = sum(t["pnl_usd"] for t in today_trades)
            wins = sum(1 for t in today_trades if t["outcome"] == "WIN")
            losses = sum(1 for t in today_trades if t["outcome"] == "LOSS")
            open_count = len(self.l1.tracker.open)

            msg = (
                f"Daily Summary\n"
                f"Trades today: {len(today_trades)} "
                f"({wins}W / {losses}L)\n"
                f"Daily P&L: ${daily_pnl:+.2f}\n"
                f"Open positions: {open_count}\n"
                f"Portfolio P&L: ${self.l1.total_pnl:+.2f}"
            )

            self.alerts.send(msg)
            log(f"[DailySummary] Sent: {len(today_trades)} trades, "
                f"P&L: ${daily_pnl:+.2f}")
        except Exception as e:
            log(f"[DailySummary] Failed: {e}", "WARN")

    def _maybe_collect_news(self) -> None:
        """Collect news headlines every N hours."""
        import time as _t
        settings = self.cfg._raw.get('settings', {})
        if not settings.get('llm_news_collection_enabled', False):
            return
        if not self.llm or not self.llm.is_available():
            return

        interval = settings.get('llm_news_interval_hours', 4) * 3600
        if _t.time() - self._last_news_collection < interval:
            return

        try:
            from bot.llm.news_collector import (
                collect_news, score_headlines, save_headlines
            )
            from bot.logger import log
            log("[News] Collecting headlines...")
            for inst in self.cfg.active_instruments:
                headlines = collect_news(inst)
                scored = score_headlines(self.llm, inst['symbol'], headlines)
                save_headlines(inst['symbol'], scored)
            self._last_news_collection = _t.time()
            log(f"[News] Collection complete for "
                f"{len(self.cfg.active_instruments)} instruments")
        except Exception as e:
            from bot.logger import log
            log(f"[News] Collection failed: {e}", "WARN")

    def _maybe_run_advisor(self, now) -> None:
        """Run weekly advisor on Sunday at 20:00 UTC."""
        settings = self.cfg._raw.get('settings', {})
        if not settings.get('llm_advisor_enabled', False):
            return
        if not self.llm_advisor or not self.llm_advisor.is_available():
            return

        if now.weekday() == 6 and now.hour == 20 and not self._advisor_ran_this_week:
            try:
                from bot.llm.advisor import generate_weekly_report
                from bot.logger import log
                import sqlite3

                # Fetch trades
                ll_db = str(BASE_DIR / 'learning_loop.db')
                trades = []
                if os.path.exists(ll_db):
                    conn = sqlite3.connect(ll_db)
                    conn.execute("PRAGMA busy_timeout = 5000")
                    conn.row_factory = sqlite3.Row
                    cursor = conn.execute(
                        "SELECT * FROM trades WHERE open=0 "
                        "AND timestamp > datetime('now', '-7 days')"
                    )
                    trades = [dict(r) for r in cursor.fetchall()]
                    conn.close()

                report = generate_weekly_report(
                    self.llm_advisor, trades, [], [],
                    self.cfg.active_instruments
                )

                # Save report
                advisor_db = str(BASE_DIR / 'advisor.db')
                conn = sqlite3.connect(advisor_db)
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA busy_timeout = 5000")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS advisor_reports (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        report_json TEXT NOT NULL
                    )
                """)
                conn.execute(
                    "INSERT INTO advisor_reports (timestamp, report_json) "
                    "VALUES (?, ?)",
                    (now.isoformat(), json.dumps(report))
                )
                conn.commit()
                conn.close()

                # Telegram summary
                if self.alerts and report.get('summary'):
                    self.alerts.send(
                        f"<b>Weekly Advisor Report</b>\n{report['summary'][:500]}"
                    )

                self._advisor_ran_this_week = True
                log(f"[Advisor] Weekly report generated")

            except Exception as e:
                from bot.logger import log
                log(f"[Advisor] Failed: {e}", "WARN")

        # Reset flag on Monday
        if now.weekday() == 0:
            self._advisor_ran_this_week = False

    def _shutdown(self, sig=None, frame=None) -> None:
        """Graceful shutdown — notify plugins, watchdog, log final state."""
        separator("SHUTDOWN")
        log("Bot stopped by user.")
        self.watchdog.stop()
        for plugin in self.plugins:
            try:
                plugin.on_shutdown()
            except Exception:
                pass
        log(f"Final P&L: ${self.l1.total_pnl:+.2f}")
        log(f"Positions remain open on {self.broker_type.upper()}. Manage manually.")
        sys.exit(0)


def validate_environment(config_file: str = None) -> None:
    """
    Pre-flight checks before starting the bot.
    Exits with clear message if anything is wrong.
    """
    errors = []

    # 1. instruments.json exists and is valid JSON
    config_path = Path(config_file) if config_file else BASE_DIR / 'instruments.json'
    if not config_path.exists():
        errors.append(f"instruments.json not found at {config_path}")
    else:
        try:
            with open(config_path) as f:
                data = json.load(f)
            if 'settings' not in data or 'layer1_active' not in data:
                errors.append("instruments.json missing 'settings' or 'layer1_active' keys")
        except json.JSONDecodeError as e:
            errors.append(f"instruments.json has invalid JSON: {e}")

    # 2. Check env vars (warn, don't fail — bot works without Telegram)
    if not os.environ.get('TELEGRAM_BOT_TOKEN'):
        log("WARNING: TELEGRAM_BOT_TOKEN not set — Telegram alerts disabled", "WARN")
    if not os.environ.get('TELEGRAM_CHAT_ID'):
        log("WARNING: TELEGRAM_CHAT_ID not set — Telegram alerts disabled", "WARN")

    # 3. Web directory is writable
    web_dir = BASE_DIR / 'web'
    if web_dir.exists() and not os.access(web_dir, os.W_OK):
        errors.append(f"Web directory not writable: {web_dir}")

    # 4. Database directory is accessible
    for db_name in ['positions.db', 'learning_loop.db', 'layer3_silver.db']:
        db_path = BASE_DIR / db_name
        if db_path.exists() and not os.access(db_path, os.W_OK):
            errors.append(f"Database not writable: {db_path}")

    # 5. Broker-specific connectivity checks
    try:
        with open(config_path) as f:
            s = json.load(f).get('settings', {})
        broker = s.get('broker', 'ibkr')
        if broker == 'ibkr':
            port = s.get('port', 0)
            if port not in (4001, 4002, 7496, 7497, 4000):
                log(f"WARNING: Unusual IBKR port {port} — "
                    f"expected 4001/4002 (Gateway) or 7496/7497 (TWS)", "WARN")
        elif broker == 'ig':
            for var in ('IG_USERNAME', 'IG_PASSWORD', 'IG_API_KEY'):
                if not os.environ.get(var):
                    log(f"WARNING: {var} not set — IG broker will fail to connect", "WARN")
    except Exception:
        pass

    if errors:
        print("\n=== STARTUP VALIDATION FAILED ===")
        for e in errors:
            print(f"  ERROR: {e}")
        print("\nFix the above issues and try again.")
        sys.exit(1)

    log("Startup validation passed")


# ── Entry point ───────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="CogniflowAI Trading Bot")
    parser.add_argument("--broker", default=None,
                        help="Broker to use: ibkr or ig (overrides instruments.json)")
    parser.add_argument("--config", default=None,
                        help="Path to instruments JSON config (default: instruments.json)")
    args = parser.parse_args()

    validate_environment(config_file=args.config)
    bot = TradingBot(broker_override=args.broker, config_path=args.config)
    bot.run()
