"""
Regime-specific Telegram alerts — §15.2 of CLAUDE_STRATEGY_SPEC_v3.

Extends the existing TelegramAlerts with regime-aware notifications:
  - Hard degradation events
  - Daily regime summary at UK market close (16:30 UTC)
"""
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("regime.alerts")


def send_hard_degradation_alert(telegram_alerts, component: str,
                                trigger_reason: str,
                                action_taken: str) -> bool:
    if telegram_alerts is None:
        return False
    msg = (
        f"🔴 <b>Hard Degradation</b>\n"
        f"Component: <code>{component}</code>\n"
        f"Trigger: {trigger_reason}\n"
        f"Action: {action_taken}"
    )
    return telegram_alerts.send(msg)


def send_db_logging_paused_alert(telegram_alerts, reason: str) -> bool:
    if telegram_alerts is None:
        return False
    msg = (
        f"⚠️ <b>DB Logging Paused</b>\n"
        f"Reason: {reason}"
    )
    return telegram_alerts.send(msg)


def send_daily_regime_summary(telegram_alerts, instruments: list,
                              regime_states: dict,
                              shadow_stats: Optional[dict] = None) -> bool:
    if telegram_alerts is None:
        return False

    lines = ["📊 <b>Daily Regime Summary</b>"]

    for inst in instruments:
        symbol = inst if isinstance(inst, str) else inst.get("symbol", "?")
        state = regime_states.get(symbol)
        if state:
            regime = state.get("regime", "N/A")
            days = state.get("days_in_regime", "?")
            lines.append(f"  {symbol}: {regime} (day {days})")
        else:
            lines.append(f"  {symbol}: no data")

    if shadow_stats:
        agreements = shadow_stats.get("agreements", 0)
        disagreements = shadow_stats.get("disagreements", 0)
        total = agreements + disagreements
        if total > 0:
            pct = agreements / total * 100
            lines.append(f"\nShadow agreement: {pct:.0f}% ({agreements}/{total})")

    return telegram_alerts.send("\n".join(lines))
