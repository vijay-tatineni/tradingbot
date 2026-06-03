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
  log_signal(inst, signal, confidence, live_blocked_by)
                          → called for every BUY/SELL engine signal
                            regardless of whether layer1 enforced
                            gates. Observation-only.
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

    def on_instrument_tick(self, inst: dict, price: float,
                           bar_closed: bool, trail_stop_pct: float,
                           take_profit_pct: float,
                           emergency_stop_pct: float) -> None:
        """
        Called once per instrument per cycle, after `price` and the
        instrument's per-instrument exit pct values are known but
        before the live position-handling branch. Default is a no-op.
        The orchestrator overrides this to tick any open shadow
        positions through tier-1 / tier-2 exit logic so blocked-entry
        P&L data can be measured.
        """
        pass

    def apply_regime_filter(self, inst: dict, signal: int,
                            confidence: str, price: float,
                            bar_time: str) -> bool:
        """
        Called BEFORE the layer1 entry gates (position limit, validation,
        sentiment, pre_trade). Return False to block this signal because
        of the regime filter; layer1 will mark live_blocked_by =
        "regime_filter" and skip the rest of the branch. Default is True
        (no opinion) — only the orchestrator overrides this.
        """
        return True

    def log_signal(self, inst: dict, signal: int, confidence: str,
                   live_blocked_by: Optional[str]) -> None:
        """
        Called for every BUY/SELL engine signal, BEFORE layer1's
        position-limit and validation gates. Observation-only — the
        return value is ignored. Use this to record what the live
        trading path will do (live_blocked_by tells you which gate,
        if any, will stop the trade) alongside what your subsystem
        would have done.
        """
        pass

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
