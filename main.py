"""
╔══════════════════════════════════════════════════════════════╗
║  Trading Bot v7.0 — main.py                                ║
║  The ONLY file you need to edit to change bot behaviour.    ║
║                                                             ║
║  v7.0 changes:                                              ║
║    - SQLite DB for position state (trail stops survive)     ║
║    - GBP pence/pounds fix                                   ║
║    - Telegram alerts (bot/alerts.py)                        ║
║    - Watchdog health monitor (bot/watchdog.py)              ║
║    - Learning loop exit recording fixed                     ║
║                                                             ║
║  To add a new plugin (e.g. AI sentiment, macro filter):     ║
║    1. Create bot/plugins/my_plugin.py                       ║
║    2. Import it below                                       ║
║    3. Call bot.register_plugin(MyPlugin(cfg))               ║
║    That's it. No other files change.                        ║
╚══════════════════════════════════════════════════════════════╝

CURRENT PLUGINS:
  ✅ LearningLoop    — records every trade to SQLite, weekly retrain
  ✅ TelegramAlerts  — trade alerts, daily summary, error notifications
  ✅ SentimentEngine — Claude API news sentiment gate (blocks bad-news BUYs)

RUN:
  python3 main.py

STOP:
  Ctrl+C  (bot closes gracefully, positions remain open on IBKR)
"""

import sys
import os
import json
import signal
import datetime
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from bot.config        import Config
from bot.connection    import IBConnection
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

# ── Future plugins (uncomment to activate) ────────────────────
# from bot.plugins.macro_filter import MacroFilter
# from bot.plugins.ml_override  import MLOverride


class TradingBot:
    """
    Main orchestrator.
    Owns the main loop, plugin registry, watchdog, and shutdown handling.
    All trading logic lives in layer1.py, layer2.py and their dependencies.
    """

    VERSION = "7.0"

    def __init__(self):
        self.cfg     = Config()
        self.ib      = IBConnection(self.cfg)
        self.hours   = MarketHours()
        self.plugins : list = []

        # ── Register active plugins ───────────────────────────
        self.register_plugin(LearningLoop(self.cfg))

        self.alerts = TelegramAlerts(self.cfg)
        self.register_plugin(self.alerts)

        self.register_plugin(SentimentEngine(self.cfg, alerts=self.alerts))

        # ── Uncomment to activate future plugins ──────────────
        # self.register_plugin(MacroFilter(self.cfg))
        # self.register_plugin(MLOverride(self.cfg))

        # ── Pass plugins to layer1 ────────────────────────────
        self.l1   = ActiveTrading(self.cfg, self.ib, self.plugins, alerts=self.alerts)
        self.l2   = Accumulation(self.cfg, self.ib)
        self.l3   = SilverScalper(self.cfg, self.ib, alerts=self.alerts)
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
        self.cfg.active_instruments = self.ib.qualify_contracts(self.cfg.active_instruments)
        log("Qualifying Layer 2 contracts...")
        self.cfg.accum_instruments  = self.ib.qualify_contracts(self.cfg.accum_instruments)
        log("Qualifying Layer 3 contracts...")
        self.l3.qualify(self.ib)

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
                log("Weekend — markets closed, sleeping 1 hour")
                self.ib.sleep(3600)
                continue

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

                # ── Watchdog heartbeat ────────────────────────
                self.watchdog.heartbeat(cycle)

                log(f"Cycle #{cycle} complete. "
                    f"Next in {self.cfg.check_interval_mins} minutes.")
                self.ib.sleep(self.cfg.check_interval)

            except Exception as e:
                log(f"Cycle error: {e}", "ERROR")
                import traceback
                log(traceback.format_exc(), "ERROR")
                self.alerts.send_error(f"Cycle #{cycle} error: {e}")
                self.ib.reconnect()

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
        log("Positions remain open on IBKR. Use IBKR app to manage manually.")
        sys.exit(0)


def validate_environment() -> None:
    """
    Pre-flight checks before starting the bot.
    Exits with clear message if anything is wrong.
    """
    errors = []

    # 1. instruments.json exists and is valid JSON
    config_path = BASE_DIR / 'instruments.json'
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

    # 5. IBKR connectivity (tested later during Config/IBConnection init)
    #    Just check the port config is sensible
    try:
        with open(config_path) as f:
            s = json.load(f).get('settings', {})
        port = s.get('port', 0)
        if port not in (4001, 4002, 7496, 7497, 4000):
            log(f"WARNING: Unusual IBKR port {port} — "
                f"expected 4001/4002 (Gateway) or 7496/7497 (TWS)", "WARN")
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
    validate_environment()
    bot = TradingBot()
    bot.run()
