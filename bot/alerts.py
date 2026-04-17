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
        self.trade_count       = 0
        self.daily_pnl         = 0.0
        self._last_summary_date = None
        broker = cfg._raw.get('settings', {}).get('broker', 'ibkr')
        self.broker_label = broker.upper()

    # ── Plugin lifecycle hooks ──────────────────────

    def on_start(self) -> None:
        if not self.enabled:
            log("[Telegram] Not configured — set TELEGRAM_BOT_TOKEN "
                "and TELEGRAM_CHAT_ID env vars")
            return
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
        # Daily summary at ~17:00 UTC — send only once per day
        now = datetime.datetime.now(datetime.timezone.utc)
        today = now.strftime('%Y-%m-%d')
        if now.hour == 17 and self._last_summary_date != today:
            self._send_daily_summary(signal_rows, total_pnl)
            self._last_summary_date = today

    def on_shutdown(self) -> None:
        if self.enabled:
            if self.daily_pnl != 0.0:
                self.send(f"🔴 <b>Trading Bot stopped</b>\n"
                          f"P&L: ${self.daily_pnl:+.2f}")
            else:
                self.send("🔴 <b>Trading Bot stopped</b>")

    # ── Public send methods ─────────────────────────

    def send(self, message: str) -> bool:
        if not self.enabled:
            return False
        prefixed = f"<b>[{self.broker_label}]</b> {message}"
        url     = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = json.dumps({
            'chat_id':    self.chat_id,
            'text':       prefixed,
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
