"""
bot/watchdog.py — Health monitor
Runs in a background thread. Alerts if bot appears stuck or disconnected.
"""

import threading
import time
import datetime
from bot.logger import log


class Watchdog:

    SLEEP_STALE_MINS = 90  # longer threshold during weekend/sleep

    def __init__(self, alerts=None, max_stale_mins: int = 10):
        """
        Args:
            alerts         : TelegramAlerts instance (or None to just log)
            max_stale_mins : alert if no heartbeat for this many minutes
        """
        self.alerts         = alerts
        self.max_stale_mins = max_stale_mins
        self.last_heartbeat = datetime.datetime.utcnow()
        self.last_cycle     = 0
        self._running       = False
        self._thread        = None
        self._sleep_mode    = False

    def start(self) -> None:
        """Start the watchdog background thread."""
        self._running = True
        self._thread  = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()
        log(f"[Watchdog] Started — alert if no heartbeat "
            f"for {self.max_stale_mins} min")

    def heartbeat(self, cycle: int) -> None:
        """Called every cycle to signal the bot is alive."""
        self.last_heartbeat = datetime.datetime.utcnow()
        self.last_cycle     = cycle

    def set_sleep_mode(self, sleeping: bool) -> None:
        """Enable/disable sleep mode (weekend, holidays). Resets heartbeat."""
        self._sleep_mode = sleeping
        self.last_heartbeat = datetime.datetime.utcnow()
        if sleeping:
            log("[Watchdog] Sleep mode ON — threshold raised to "
                f"{self.SLEEP_STALE_MINS} min")

    def stop(self) -> None:
        self._running = False

    def _monitor(self) -> None:
        """Background loop — checks heartbeat freshness every 60s."""
        while self._running:
            time.sleep(60)
            elapsed = (datetime.datetime.utcnow()
                       - self.last_heartbeat).total_seconds() / 60

            threshold = self.SLEEP_STALE_MINS if self._sleep_mode else self.max_stale_mins

            if elapsed > threshold:
                mode = "sleep" if self._sleep_mode else "trading"
                msg = (f"🚨 Watchdog: Bot appears stuck!\n"
                       f"Last heartbeat: {elapsed:.0f} min ago\n"
                       f"Last cycle: #{self.last_cycle}\n"
                       f"Mode: {mode}")
                log(msg, "ERROR")
                if self.alerts:
                    self.alerts.send(msg)
                # Reset to avoid alert spam — re-alert after another full interval
                self.last_heartbeat = datetime.datetime.utcnow()
