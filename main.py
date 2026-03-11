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
import signal
import datetime
from dotenv import load_dotenv
load_dotenv()

from bot.config        import Config
from bot.connection    import IBConnection
from bot.market_hours  import MarketHours
from bot.layer1        import ActiveTrading
from bot.layer2        import Accumulation
from bot.dashboard     import Dashboard
from bot.logger        import log, banner, separator

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
        self.l1   = ActiveTrading(self.cfg, self.ib, self.plugins)
        self.l2   = Accumulation(self.cfg, self.ib)
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

        # Notify plugins bot has started
        for plugin in self.plugins:
            plugin.on_start()

        # Start watchdog
        self.watchdog.start()

        cycle = 0

        while True:
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


# ── Entry point ───────────────────────────────────────────────
if __name__ == '__main__':
    bot = TradingBot()
    bot.run()
