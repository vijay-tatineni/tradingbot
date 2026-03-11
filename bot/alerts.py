"""
bot/alerts.py — Telegram alert system
Sends trade alerts, errors, and daily summaries via Telegram Bot API.

Config via environment variables:
  TELEGRAM_BOT_TOKEN  — from @BotFather
  TELEGRAM_CHAT_ID    — your chat/group ID

No external dependencies — uses urllib only.
"""

import os
import json
import urllib.request
import urllib.error
import datetime
from bot.plugins.base_plugin import BasePlugin
from bot.logger import log


class TelegramAlerts(BasePlugin):

    name = "TelegramAlerts"

    def __init__(self, cfg):
        self.cfg      = cfg
        self.token    = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        self.chat_id  = os.environ.get('TELEGRAM_CHAT_ID', '')
        self.enabled  = bool(self.token and self.chat_id)
        self.trade_count = 0
        self.daily_pnl   = 0.0

    # ── Plugin lifecycle hooks ──────────────────────

    def on_start(self) -> None:
        if not self.enabled:
            log("[Telegram] Not configured — set TELEGRAM_BOT_TOKEN "
                "and TELEGRAM_CHAT_ID env vars")
            return
        self.send("🟢 <b>Trading Bot started</b>")
        log("[Telegram] Alerts enabled")

    def post_trade(self, inst: dict, signal: int,
                   action: str, price: float) -> None:
        if not self.enabled:
            return

        flag   = inst.get('flag', '')
        symbol = inst['symbol']
        name   = inst['name']

        if 'BOUGHT' in action or 'RE-ENTRY' in action:
            emoji = '🟢'
        elif 'CLOSED' in action or 'SOLD' in action:
            emoji = '🔴'
        elif 'SHORT' in action:
            emoji = '🔻'
        else:
            return

        msg = f"{emoji} <b>{flag} {symbol}</b> — {action}\nPrice: {price:.4f}\n{name}"
        self.send(msg)
        self.trade_count += 1

    def on_cycle_end(self, cycle: int, signal_rows: list,
                     total_pnl: float) -> None:
        self.daily_pnl = total_pnl
        # Daily summary at ~17:00 UTC
        now = datetime.datetime.now(datetime.timezone.utc)
        if now.hour == 17 and now.minute < (self.cfg.check_interval_mins + 1):
            self._send_daily_summary(signal_rows, total_pnl)

    def on_shutdown(self) -> None:
        if self.enabled:
            self.send(f"🔴 <b>Trading Bot stopped</b>\n"
                      f"P&L: ${self.daily_pnl:+.2f}")

    # ── Public send methods ─────────────────────────

    def send(self, message: str) -> bool:
        """Send a message via Telegram Bot API. Returns True on success."""
        if not self.enabled:
            return False
        url     = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = json.dumps({
            'chat_id':    self.chat_id,
            'text':       message,
            'parse_mode': 'HTML',
        }).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={'Content-Type': 'application/json'}
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            return True
        except (urllib.error.URLError, Exception) as e:
            log(f"[Telegram] Send failed: {e}", "WARN")
            return False

    def send_error(self, message: str) -> None:
        """Send an error alert with warning emoji."""
        self.send(f"⚠️ {message}")

    # ── Private ─────────────────────────────────────

    def _send_daily_summary(self, signal_rows: list,
                            total_pnl: float) -> None:
        open_pos = [r for r in signal_rows if r.get('pos', 0) != 0]
        lines = [
            "📊 <b>Daily Summary</b>",
            f"P&L: <b>${total_pnl:+.2f}</b>",
            f"Open positions: {len(open_pos)}",
            f"Trades today: {self.trade_count}",
        ]
        for p in open_pos:
            pnl   = p.get('unreal_pnl', 0)
            emoji = '🟢' if pnl >= 0 else '🔴'
            lines.append(f"  {emoji} {p['symbol']}: ${pnl:+.2f}")
        self.send('\n'.join(lines))
        self.trade_count = 0
