"""
bot/layer1.py  — v8.0
Layer 1 — Active Trading with smart exits and re-entry.

v8.0 changes:
  - Two-tier stop evaluation matching walk-forward testing:
    Tier 1 (every cycle): Emergency hard stop only
    Tier 2 (bar close only): Trailing stop + take profit + peak update
  - Missing position handling: waits 3 cycles, uses market price not 0
  - Peak price only updates on bar close

v7.2 changes:
  - Uses actual broker fill prices for tracker and learning loop
  - Handles FillResult tuples from orders.handle_signal()
  - Tracker reconciliation with IBKR every cycle
  - ADX shown in log output
"""

import datetime
import sqlite3
from pathlib import Path
from bot.config           import Config
from bot.brokers.base     import BaseBroker, FillResult
from bot.market_hours     import MarketHours
from bot.indicators       import Indicators
from bot.signals          import SignalEngine
from bot.position_tracker import PositionTracker
from bot.bar_schedule     import is_bar_close, next_bar_close_str
from bot.logger           import log, separator
from bot.sizing            import calculate_qty
from bot.order_validator  import validate_order, OrderValidationError

_BASE_DIR = Path(__file__).parent.parent


class ActiveTrading:

    def __init__(self, cfg: Config, broker: BaseBroker,
                 plugins: list = None, alerts=None,
                 llm=None):
        self.cfg       = cfg
        self.broker    = broker
        self.plugins   = plugins or []
        self.alerts    = alerts
        self.llm       = llm
        self.hours     = MarketHours()
        self.indics    = Indicators(cfg)
        self.engine    = SignalEngine()
        self.tracker   = PositionTracker(cfg)

        # LLM sentiment settings
        settings = cfg._raw.get('settings', {})
        self._sentiment_enabled = settings.get('llm_sentiment_enabled', False)
        self._reject_threshold = settings.get('llm_sentiment_reject_threshold', 0.7)

        self.signal_rows : list  = []
        self.total_pnl   : float = 0.0
        self._synced     : bool  = False

    def run(self) -> None:
        separator("LAYER 1: ACTIVE TRADING")

        # Log disabled instruments on first run
        if not self._synced:
            for inst in self.cfg._raw.get('layer1_active', []):
                if not inst.get('enabled', True):
                    reason = inst.get('disabled_reason', 'no edge per walk-forward')
                    log(f"  Skipping {inst['symbol']} — disabled ({reason})")

        self.total_pnl = self.broker.get_total_pnl()
        log(f"Portfolio P&L: ${self.total_pnl:+.2f}  |  Limit: -${self.cfg.portfolio_loss_limit}")

        # Sync existing IBKR positions with tracker on first run
        if not self._synced:
            self._sync_existing_positions()
            self._synced = True

        # Reconcile tracker with broker every cycle
        self._reconcile_with_broker()

        if self.broker.is_emergency_stop(self.total_pnl):
            log(f"EMERGENCY STOP: P&L ${self.total_pnl:.2f} hit loss limit!", "ERROR")
            self._close_all()
            return

        self.signal_rows = []
        self._entries_this_cycle = 0
        self._open_count = len(self.tracker.open)
        for inst in self.cfg.active_instruments:
            row = self._process_instrument(inst)
            self.signal_rows.append(row)
            self.broker.sleep(1)

    def _reconcile_with_broker(self) -> None:
        """
        Compare local tracker state with live broker positions.
        Add anything broker has that we don't track.
        For missing positions: use 3-cycle counter before recording close.
        """
        active_symbols = {inst['symbol'] for inst in self.cfg.active_instruments}
        unmanaged = set(getattr(self.cfg, 'unmanaged_positions', []))

        broker_positions = {}
        for p in self.broker.get_all_positions():
            if p.symbol in active_symbols and p.symbol not in unmanaged and p.qty != 0:
                broker_positions[p.symbol] = p

        for sym, p in broker_positions.items():
            # Position reappeared — clear any missing counter
            self.tracker.clear_missing_count(sym)
            if sym not in self.tracker.open:
                inst = next((i for i in self.cfg.active_instruments
                             if i['symbol'] == sym), None)
                trail_pct = inst.get('trail_stop_pct', 2.0) if inst else 2.0
                self.tracker.init_existing(
                    sym, p.avg_cost, p.qty, trail_pct, p.currency
                )
                log(f"[Reconcile] Added missing position: {sym} "
                    f"qty={p.qty}", "WARN")

        stale = [sym for sym in list(self.tracker.open)
                 if sym in active_symbols and sym not in broker_positions]
        for sym in stale:
            # Get current market price for this symbol
            inst = next((i for i in self.cfg.active_instruments
                         if i['symbol'] == sym), None)
            current_price = 0.0
            if inst and inst.get('_last_bundle'):
                current_price = inst['_last_bundle'].price
            elif inst:
                pos_info = self.broker.get_position_info(sym)
                current_price = pos_info.price

            result = self.tracker.handle_missing_position(sym, current_price)
            if result is not None:
                # Position confirmed gone — record the close
                exit_price = result['exit_price']
                reason = result['reason']
                self.tracker.on_close(sym, exit_price, reason, 30)
                self.tracker.open.pop(sym, None)
                self.tracker._delete_open(sym)

                # Record in learning loop
                if inst:
                    for p in self.plugins:
                        p.post_trade(inst, 0, f"CLOSED ({reason})", exit_price)

                # Send alert
                if self.alerts:
                    self.alerts.send(
                        f"⚠️ {sym} position disappeared from broker. "
                        f"Recorded exit at {exit_price:.4f}. Check broker app."
                    )

    def _can_enter(self, symbol: str) -> bool:
        """Check if portfolio risk limits allow a new entry."""
        if self._open_count >= self.cfg.max_open_positions:
            log(f"  [{symbol}] Skipping entry — max positions reached "
                f"({self._open_count}/{self.cfg.max_open_positions})")
            return False
        if self._entries_this_cycle >= self.cfg.max_entries_per_cycle:
            log(f"  [{symbol}] Skipping entry — max entries this cycle "
                f"({self._entries_this_cycle}/{self.cfg.max_entries_per_cycle})")
            return False
        return True

    def _record_entry(self):
        """Update counters after a successful entry."""
        self._entries_this_cycle += 1
        self._open_count += 1

    def _process_instrument(self, inst: dict) -> dict:
        symbol   = inst['symbol']
        mkt_open = self.hours.is_open(inst)
        mkt_str  = self.hours.status(inst)

        if not mkt_open:
            return self._closed_row(inst, mkt_str)

        # Determine bar size from instrument timeframe setting
        timeframe = inst.get('timeframe', 'daily')
        bar_size = '4 hours' if timeframe == '4hr' else '1 day'
        df = self.broker.fetch_bars(inst['contract'], bar_size=bar_size)
        if df is None:
            return self._closed_row(inst, mkt_str)

        # Resolve per-instrument indicator settings
        ind_settings = self.cfg.get_indicator_settings(inst)
        bundle = self.indics.calculate(df, indicator_settings=ind_settings)
        if bundle is None:
            return self._closed_row(inst, mkt_str)

        inst['_last_bundle']     = bundle
        price                    = bundle.price
        result                   = self.engine.evaluate(bundle)
        inst['_last_confidence'] = result.confidence
        pos_info                 = self.broker.get_position_info(symbol, price)
        pos                      = pos_info.qty

        # Per-instrument smart exit config (with sensible defaults)
        trail_stop_pct       = inst.get('trail_stop_pct',        2.0)
        take_profit_pct      = inst.get('take_profit_pct',       8.0)
        reentry_recovery_pct = inst.get('reentry_recovery_pct',  1.5)
        reentry_cooldown     = inst.get('reentry_cooldown_mins',  30)
        loss_limit           = inst.get('loss_limit',            200)

        # Risk-based position sizing: calculate qty from target_notional
        entry_qty = calculate_qty(inst, price, self.cfg.default_target_notional)
        inst['qty'] = entry_qty  # Override fixed qty for this cycle

        action = "--"
        timeframe = inst.get('timeframe', 'daily')
        emergency_stop_pct = inst.get(
            'emergency_stop_pct',
            trail_stop_pct * 2  # default: 2x trail stop
        )

        if pos != 0:
            # ── Tier 1: Emergency hard stop (every cycle) ──────────
            emergency_exit = self.tracker.check_emergency_stop(
                symbol, price, emergency_stop_pct
            )

            # Also check per-instrument dollar loss limit (every cycle)
            if not emergency_exit and pos_info.unreal_pnl < -loss_limit:
                emergency_exit = f"LOSS_LIMIT -${abs(pos_info.unreal_pnl):.0f}"
                log(f"  LOSS LIMIT hit {symbol}: ${pos_info.unreal_pnl:.2f}", "WARN")

            if emergency_exit:
                fill_result = self.broker.close_position(inst, pos)
                if fill_result:
                    exit_price = fill_result.fill_price or price
                    self.tracker.on_close(symbol, exit_price, emergency_exit, reentry_cooldown)
                    action = f"CLOSED ({emergency_exit})"
                    for p in self.plugins:
                        p.post_trade(inst, 0, action, exit_price)
                else:
                    action = "CLOSE FAILED (emergency)"
            else:
                # Log tier 1 status
                state = self.tracker.open.get(symbol)
                if state:
                    entry = state.entry_price
                    if state.side == 'SHORT':
                        e_price = entry * (1 + emergency_stop_pct / 100)
                        log(f"  [{symbol}] Tier 1: price {price:.2f}, "
                            f"emergency at {e_price:.2f} → HOLD")
                    else:
                        e_price = entry * (1 - emergency_stop_pct / 100)
                        log(f"  [{symbol}] Tier 1: price {price:.2f}, "
                            f"emergency at {e_price:.2f} → HOLD")

                # ── Tier 2: Trail stop + TP (bar close only) ───────
                bar_closed = is_bar_close(timeframe, inst)

                if bar_closed:
                    # Update peak price ONLY on bar close
                    self.tracker.update(symbol, price, trail_stop_pct,
                                        inst.get('currency', 'USD'))

                    # Check take profit / trailing stop
                    smart_exit = self.tracker.check_exit(
                        symbol, price, take_profit_pct, trail_stop_pct,
                        inst.get('currency', 'USD')
                    )

                    # Log bar close evaluation
                    peak = self.tracker.get_peak(symbol)
                    stop = self.tracker.get_stop_level(symbol)
                    log(f"  [{symbol}] Bar closed — price {price:.2f}, "
                        f"peak {peak:.2f}, trail at {stop:.2f}")

                    if smart_exit:
                        fill_result = self.broker.close_position(inst, pos)
                        if fill_result:
                            exit_price = fill_result.fill_price or price
                            self.tracker.on_close(symbol, exit_price, smart_exit,
                                                  reentry_cooldown)
                            action = f"CLOSED ({smart_exit})"
                            for p in self.plugins:
                                p.post_trade(inst, 0, action, exit_price)
                        else:
                            action = "CLOSE FAILED (smart exit)"

                    # Signal reversal (short-able CFDs only)
                    elif result.signal == -1 and not inst.get('long_only', True):
                        allowed = all(p.pre_trade(inst, -1, result.confidence)
                                      for p in self.plugins)
                        if allowed:
                            action, fill_result = self.broker.handle_signal(
                                inst, result.signal, result.confidence, pos)
                            if 'FAILED' not in action:
                                exit_price = fill_result.fill_price or price
                                self.tracker.on_close(symbol, exit_price,
                                                      'SIGNAL_REVERSED',
                                                      reentry_cooldown)
                                for p in self.plugins:
                                    p.post_trade(inst, -1, action, exit_price)
                else:
                    next_close = next_bar_close_str(timeframe, inst)
                    log(f"  [{symbol}] Waiting for bar close (next: {next_close})")

        else:
            # No position — check for re-entry or fresh entry

            if self.tracker.is_watching(symbol):
                signal_valid = (result.signal == 1 and result.confidence in ('HIGH', 'MEDIUM'))
                should_reenter, re_reason = self.tracker.check_reentry(
                    symbol, price, signal_valid, reentry_recovery_pct
                )
                if should_reenter:
                    if not self._can_enter(symbol):
                        action = "RE-ENTRY BLOCKED (position limit)"
                    elif not self._validate_entry(inst, inst['qty'], price, "BUY"):
                        action = "RE-ENTRY BLOCKED (validation)"
                    else:
                        allowed = all(p.pre_trade(inst, 1, result.confidence) for p in self.plugins)
                        if allowed:
                            fill_result = self.broker.place_order(
                                inst['contract'], 'BUY', inst['qty'], inst['name'])
                            if fill_result:
                                fill_price = fill_result.fill_price or price
                                fill_qty = fill_result.filled_qty or inst['qty']
                                self.tracker.on_open(symbol, fill_price, fill_qty,
                                                     trail_stop_pct, inst.get('currency','USD'))
                                self.tracker.clear_watch(symbol)
                                action = f"RE-ENTRY [{result.confidence}]"
                                self._record_entry()
                                for p in self.plugins:
                                    p.post_trade(inst, 1, action, fill_price)
                                pos_info = self.broker.get_position_info(symbol, price)
                            else:
                                action = "RE-ENTRY FAILED"
                else:
                    action = f"WATCHING: {re_reason}"

            elif result.signal == 1:
                # Fresh entry
                if not self._can_enter(symbol):
                    action = "ENTRY BLOCKED (position limit)"
                elif not self._validate_entry(inst, entry_qty, price, "BUY"):
                    action = "ENTRY BLOCKED (validation)"
                else:
                    allowed = all(p.pre_trade(inst, result.signal, result.confidence) for p in self.plugins)
                    if allowed and not self._llm_sentiment_check(inst, "BUY", df):
                        allowed = False
                        action = "BLOCKED by LLM sentiment"
                    if allowed:
                        action, fill_result = self.broker.handle_signal(
                            inst, result.signal, result.confidence, pos)
                        if 'BOUGHT' in action and fill_result:
                            fill_price = fill_result.fill_price or price
                            fill_qty = fill_result.filled_qty or inst['qty']
                            self.tracker.on_open(symbol, fill_price, fill_qty,
                                                 trail_stop_pct, inst.get('currency','USD'))
                            self._record_entry()
                            for p in self.plugins:
                                p.post_trade(inst, 1, action, fill_price)
                            pos_info = self.broker.get_position_info(symbol, price)
                    else:
                        action = "BLOCKED by plugin"

            elif result.signal == -1 and not inst.get('long_only', True):
                # Fresh short from flat
                if not self._can_enter(symbol):
                    action = "ENTRY BLOCKED (position limit)"
                elif not self._validate_entry(inst, entry_qty, price, "SELL"):
                    action = "ENTRY BLOCKED (validation)"
                else:
                    allowed = all(p.pre_trade(inst, result.signal, result.confidence) for p in self.plugins)
                    if allowed:
                        action, fill_result = self.broker.handle_signal(
                            inst, result.signal, result.confidence, pos)
                        if 'SHORTED' in action and fill_result:
                            fill_price = fill_result.fill_price or price
                            fill_qty = fill_result.filled_qty or inst['qty']
                            self.tracker.on_open(symbol, fill_price, -fill_qty,
                                                 trail_stop_pct, inst.get('currency','USD'),
                                                 side='SHORT')
                            self._record_entry()
                            for p in self.plugins:
                                p.post_trade(inst, -1, action, fill_price)
                            pos_info = self.broker.get_position_info(symbol, price)
                    else:
                        action = "BLOCKED by plugin"

        # Refresh position info after any trades
        pos_info   = self.broker.get_position_info(symbol, price)
        stop_level = self.tracker.get_stop_level(symbol)
        peak_price = self.tracker.get_peak(symbol)
        watch_info = self.tracker.watch_info(symbol)

        log(f"  {symbol:<8} {mkt_str:<6} {price:>10.4f}  "
            f"{bundle.alligator.state:<6} {bundle.alligator.direction:<4}  "
            f"WR:{bundle.wr.value:>6.1f}  RSI:{bundle.rsi:>5.1f}  "
            f"ADX:{bundle.adx.value:>5.1f}  "
            f"{result.confidence:<6} {self._signal_str(result.signal):<4}  "
            f"pos:{pos_info.qty:.0f}  stop:{stop_level:.2f}  {action}")

        if result.reason:
            log(f"    → {result.reason}")

        return self._build_row(inst, mkt_str, bundle, result,
                               pos_info, action, stop_level, peak_price, watch_info)

    def _get_daily_pnl(self) -> float:
        """Get today's realized P&L from closed trades."""
        try:
            db_path = str(_BASE_DIR / 'learning_loop.db')
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA busy_timeout = 5000")
            result = conn.execute("""
                SELECT COALESCE(SUM(pnl_usd), 0) FROM trades
                WHERE outcome IS NOT NULL
                AND date(timestamp) = date('now')
            """).fetchone()
            conn.close()
            return result[0] if result else 0.0
        except Exception:
            return 0.0

    def _get_weekly_pnl(self) -> float:
        """Get this week's realized P&L from closed trades."""
        try:
            db_path = str(_BASE_DIR / 'learning_loop.db')
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA busy_timeout = 5000")
            result = conn.execute("""
                SELECT COALESCE(SUM(pnl_usd), 0) FROM trades
                WHERE outcome IS NOT NULL
                AND date(timestamp) >= date('now', 'weekday 0', '-7 days')
            """).fetchone()
            conn.close()
            return result[0] if result else 0.0
        except Exception:
            return 0.0

    def _validate_entry(self, inst: dict, qty: int, price: float,
                        direction: str) -> bool:
        """Run order validation. Returns True if order is allowed."""
        try:
            validate_order(
                symbol=inst["symbol"],
                qty=qty,
                price=price,
                direction=direction,
                currency=inst.get("currency", "USD"),
                settings=self.cfg._raw.get("settings", {}),
                open_positions=len(self.tracker.open),
                daily_pnl=self._get_daily_pnl(),
                weekly_pnl=self._get_weekly_pnl(),
            )
            return True
        except OrderValidationError:
            return False

    def _build_row(self, inst, mkt_str, bundle, result,
                   pos_info, action, stop_level, peak_price, watch_info) -> dict:
        return {
            'symbol':     inst['symbol'],
            'name':       inst['name'],
            'flag':       inst.get('flag', ''),
            'market':     mkt_str,
            'price':      bundle.price,
            'alligator':  bundle.alligator.state,
            'direction':  bundle.alligator.direction,
            'ma200':      result.ma200_str,
            'wr':         bundle.wr.value,
            'rsi':        bundle.rsi,
            'confidence': result.confidence,
            'signal':     self._signal_str(result.signal),
            'pos':        pos_info.qty,
            'avg_cost':   pos_info.avg_cost,
            'unreal_pnl': pos_info.unreal_pnl,
            'pnl_pct':    pos_info.pnl_pct,
            'currency':   inst.get('currency', pos_info.currency),
            'stop_level': round(stop_level, 4),
            'peak_price': round(peak_price, 4),
            'watching':   round(watch_info.recovery_pct, 2) if watch_info else 0,
            'action':     action,
            'reason':     result.reason,
        }

    def _closed_row(self, inst: dict, mkt_str: str) -> dict:
        # Use last known price from previous cycle so P&L stays populated
        last_price = 0.0
        bundle = inst.get('_last_bundle')
        if bundle:
            last_price = bundle.price
        pos_info   = self.broker.get_position_info(inst['symbol'], last_price)
        stop_level = self.tracker.get_stop_level(inst['symbol'])
        peak_price = self.tracker.get_peak(inst['symbol'])
        watch_info = self.tracker.watch_info(inst['symbol'])
        return {
            'symbol':     inst['symbol'],
            'name':       inst['name'],
            'flag':       inst.get('flag', ''),
            'market':     mkt_str,
            'price':      last_price,
            'alligator':  '--',
            'direction':  '--',
            'ma200':      '--',
            'wr':         0.0,
            'rsi':        50.0,
            'confidence': '--',
            'signal':     'CLOSED',
            'pos':        pos_info.qty,
            'avg_cost':   pos_info.avg_cost,
            'unreal_pnl': pos_info.unreal_pnl,
            'pnl_pct':    pos_info.pnl_pct,
            'currency':   inst.get('currency', 'USD'),
            'stop_level': stop_level,
            'peak_price': peak_price,
            'watching':   round(watch_info.recovery_pct, 2) if watch_info else 0,
            'action':     '--',
            'reason':     '',
        }

    def _close_all(self) -> None:
        """
        Emergency close ALL positions across all layers (L1, L2, L3).
        Uses broker live positions directly so nothing is missed.
        """
        closed_symbols = []

        # Close every position broker reports — not just Layer 1 instruments
        all_positions = self.broker.get_all_positions()
        for p in all_positions:
            if p.qty == 0:
                continue
            symbol = p.symbol

            # Skip unmanaged positions (e.g. ghost XAUUSD)
            unmanaged = set(getattr(self.cfg, 'unmanaged_positions', []))
            if symbol in unmanaged:
                log(f"  [Emergency] Skipping unmanaged: {symbol}")
                continue

            # Try to find the instrument config for this symbol (any layer)
            inst = self._find_instrument(symbol)
            if inst and 'contract' in inst:
                fill_result = self.broker.close_position(inst, p.qty)
            else:
                # No config found — use the broker position's contract handle
                try:
                    side = 'SELL' if p.qty > 0 else 'BUY'
                    qty = abs(p.qty)
                    fill_result = self.broker.place_order(
                        p.contract, side, qty, symbol
                    )
                except Exception as e:
                    log(f"  [Emergency] Failed to close {symbol}: {e}", "ERROR")
                    fill_result = None

            if fill_result:
                exit_price = getattr(fill_result, 'fill_price', 0) or 0
                if symbol in self.tracker.open:
                    exit_price = exit_price or self.tracker.open[symbol].current_price
                    self.tracker.on_close(symbol, exit_price, 'EMERGENCY_STOP', 0)
                closed_symbols.append(symbol)

        # Clear all watching states
        for sym in list(self.tracker.watching):
            self.tracker.clear_watch(sym)

        # Send emergency Telegram alert
        if self.alerts and closed_symbols:
            self.alerts.send(
                f"🚨 <b>EMERGENCY STOP</b>\n"
                f"P&L hit -${self.cfg.portfolio_loss_limit}\n"
                f"Closed: {', '.join(closed_symbols)}\n"
                f"All watching states cleared"
            )

        log(f"Emergency stop complete — closed {len(closed_symbols)} positions")

    def _find_instrument(self, symbol: str) -> dict | None:
        """Find instrument config across all layers by symbol."""
        for inst in self.cfg.active_instruments:
            if inst.get('symbol') == symbol:
                return inst
        for inst in self.cfg.accum_instruments:
            if inst.get('symbol') == symbol:
                return inst
        # Check Layer 3 silver instruments
        for inst in self.cfg._raw.get('layer3_silver', []):
            if inst.get('symbol') == symbol:
                return inst
        return None

    def _sync_existing_positions(self) -> None:
        """Register any broker positions not already tracked (e.g. first run)."""
        for inst in self.cfg.active_instruments:
            symbol   = inst['symbol']
            pos_info = self.broker.get_position_info(symbol)
            if pos_info.qty != 0 and symbol not in self.tracker.open:
                trail_pct = inst.get('trail_stop_pct', 2.0)
                currency  = inst.get('currency', 'USD')
                self.tracker.init_existing(
                    symbol, pos_info.avg_cost, pos_info.qty,
                    trail_pct, currency
                )

    def _llm_sentiment_check(self, inst: dict, signal_direction: str,
                               df) -> bool:
        """
        Run LLM sentiment check. Returns True if trade should proceed.
        Always returns True on error or if disabled.
        """
        if not self._sentiment_enabled or not self.llm or not self.llm.is_available():
            return True

        try:
            from bot.llm.sentiment import analyze_sentiment
            from bot.llm.news_collector import get_aggregate_sentiment

            symbol = inst['symbol']
            recent_bars = df.tail(20) if df is not None and len(df) >= 20 else df

            # Get news sentiment if available
            news_sentiment = get_aggregate_sentiment(symbol)

            sentiment = analyze_sentiment(
                self.llm, symbol, recent_bars, signal_direction,
                news_sentiment=news_sentiment
            )

            if sentiment["verdict"] == "REJECT" and sentiment["confidence"] >= self._reject_threshold:
                log(f"  [{symbol}] LLM rejected entry: {sentiment['reason']}", "WARN")
                return False

            if sentiment["verdict"] == "CAUTION":
                log(f"  [{symbol}] LLM caution: {sentiment['reason']}")

            log(f"  [{symbol}] LLM sentiment: {sentiment['verdict']} "
                f"({sentiment['confidence']:.1f})")
            return True

        except Exception as e:
            log(f"  [{inst['symbol']}] LLM sentiment error: {e} — proceeding", "WARN")
            return True

    @staticmethod
    def _signal_str(signal: int) -> str:
        return {1: 'BUY', -1: 'SELL', 0: 'HOLD'}.get(signal, 'HOLD')
