"""
bot/plugins/base_plugin.py
Base class for all bot plugins.

Plugins hook into the bot's lifecycle via event methods.
Override only the hooks you need — all default to no-op.

AVAILABLE HOOKS:
  on_start()              → called once after bot starts
  on_cycle_start(cycle)   → called at the start of each cycle
  pre_trade(inst, signal) → called before a trade is placed
                            return False to BLOCK the trade
  post_trade(inst, result)→ called after a trade is placed
  on_cycle_end(cycle)     → called at the end of each cycle
  on_shutdown()           → called when bot stops

EXAMPLE — Adding a new plugin:
  1. Create bot/plugins/my_plugin.py
  2. Inherit from BasePlugin
  3. Override the hooks you need
  4. In main.py: bot.register_plugin(MyPlugin(cfg))
  That's it. No other files need to change.
"""

from typing import Optional


class BasePlugin:
    """
    Abstract base for all trading bot plugins.
    All methods are no-ops by default — safe to inherit.
    """

    name: str = "BasePlugin"

    def on_start(self) -> None:
        """Called once after the bot connects and qualifies contracts."""
        pass

    def on_cycle_start(self, cycle: int) -> None:
        """Called at the very start of each trading cycle."""
        pass

    def pre_trade(self, inst: dict, signal: int, confidence: str) -> bool:
        """
        Called before every trade is placed.

        Return True  → allow the trade
        Return False → BLOCK the trade (with reason logged)

        Use this for:
          - Sentiment gate (block if news is negative)
          - Macro risk filter (block on Fed meeting days)
          - ML override (block if model disagrees strongly)
        """
        return True   # default: allow all trades

    def post_trade(self, inst: dict, signal: int,
                   action: str, entry_price: float) -> None:
        """
        Called after a trade is placed.
        Use this for:
          - Recording trade to Learning Loop database
          - Sending Telegram/email notification
          - Logging to external analytics
        """
        pass

    def on_cycle_end(self, cycle: int, signal_rows: list,
                     total_pnl: float) -> None:
        """
        Called at the end of each cycle after dashboard updates.
        Use this for:
          - Weekly retraining trigger check
          - Performance analytics update
          - External reporting
        """
        pass

    def on_shutdown(self) -> None:
        """Called when bot is stopped gracefully (Ctrl+C)."""
        pass
