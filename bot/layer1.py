"""
bot/layer1.py  — v7.2
Layer 1 — Active Trading with smart exits and re-entry.

v7.2 changes:
  - Uses actual broker fill prices for tracker and learning loop
  - Handles FillResult tuples from orders.handle_signal()
  - Tracker reconciliation with IBKR every cycle
  - ADX shown in log output
"""

import datetime
from bot.config           import Config
from bot.connection       import IBConnection
from bot.market_hours     import MarketHours
from bot.data             import DataFeed
from bot.indicators       import Indicators
from bot.signals          import SignalEngine
from bot.portfolio        import Portfolio
from bot.orders           import OrderManager, FillResult
from bot.position_tracker import PositionTracker
from bot.logger           import log, separator


class ActiveTrading:

    def __init__(self, cfg: Config, ib_conn: IBConnection,
                 plugins: list = None, alerts=None):
        self.cfg       = cfg
        self.ib_conn   = ib_conn
        self.plugins   = plugins or []
        self.hours     = MarketHours()
        self.feed      = DataFeed(ib_conn)
        self.indics    = Indicators(cfg)
        self.engine    = SignalEngine()
        self.portfolio = Portfolio(ib_conn, cfg)
        self.orders    = OrderManager(ib_conn, cfg)
        self.tracker   = PositionTracker(cfg)

        # Wire up Telegram alerts to OrderManager for fill failure alerts
        if alerts:
            self.orders.alerts = alerts

        self.signal_rows : list  = []
        self.total_pnl   : float = 0.0
        self._synced     : bool  = False

    def run(self) -> None:
        separator("LAYER 1: ACTIVE TRADING")
        self.total_pnl = self.portfolio.get_total_pnl()
        log(f"Portfolio P&L: ${self.total_pnl:+.2f}  |  Limit: -${self.cfg.portfolio_loss_limit}")

        # Sync existing IBKR positions with tracker on first run
        if not self._synced:
            self._sync_existing_positions()
            self._synced = True

        # Reconcile tracker with IBKR every cycle
        self._reconcile_with_ibkr()

        if self.portfolio.is_emergency_stop(self.total_pnl):
            log(f"EMERGENCY STOP: P&L ${self.total_pnl:.2f} hit loss limit!", "ERROR")
            self._close_all()
            return

        self.signal_rows = []
        for inst in self.cfg.active_instruments:
            row = self._process_instrument(inst)
            self.signal_rows.append(row)
            self.ib_conn.sleep(1)

    def _reconcile_with_ibkr(self) -> None:
        """
        Compare local tracker state with live IBKR positions.
        Add anything IBKR has that we don't track.
        Remove anything we track that IBKR doesn't have.
        """
        active_symbols = {inst['symbol'] for inst in self.cfg.active_instruments}
        unmanaged = set(getattr(self.cfg, 'unmanaged_positions', []))

        ibkr_positions = {}
        for p in self.portfolio.get_all_positions():
            sym = p.contract.symbol
            if sym in active_symbols and sym not in unmanaged and p.position != 0:
                ibkr_positions[sym] = p

        for sym, p in ibkr_positions.items():
            if sym not in self.tracker.open:
                currency = getattr(p.contract, 'currency', 'USD')
                inst = next((i for i in self.cfg.active_instruments
                             if i['symbol'] == sym), None)
                trail_pct = inst.get('trail_stop_pct', 2.0) if inst else 2.0
                self.tracker.init_existing(
                    sym, p.avgCost, p.position, trail_pct, currency
                )
                log(f"[Reconcile] Added missing position: {sym} "
                    f"qty={p.position}", "WARN")

        stale = [sym for sym in list(self.tracker.open)
                 if sym in active_symbols and sym not in ibkr_positions]
        for sym in stale:
            log(f"[Reconcile] Removing stale tracker entry: {sym} "
                f"(no IBKR position)", "WARN")
            self.tracker.open.pop(sym, None)
            self.tracker._delete_open(sym)

    def _process_instrument(self, inst: dict) -> dict:
        symbol   = inst['symbol']
        mkt_open = self.hours.is_open(inst)
        mkt_str  = self.hours.status(inst)

        if not mkt_open:
            return self._closed_row(inst, mkt_str)

        # Determine bar size from instrument timeframe setting
        timeframe = inst.get('timeframe', 'daily')
        bar_size = '4 hours' if timeframe == '4hr' else '1 day'
        df = self.feed.get(inst['contract'], bar_size=bar_size)
        if df is None:
            return self._closed_row(inst, mkt_str)

        bundle = self.indics.calculate(df)
        if bundle is None:
            return self._closed_row(inst, mkt_str)

        inst['_last_bundle']     = bundle
        price                    = bundle.price
        result                   = self.engine.evaluate(bundle)
        inst['_last_confidence'] = result.confidence
        pos_info                 = self.portfolio.get_position_info(symbol, price)
        pos                      = pos_info.qty

        # Per-instrument smart exit config (with sensible defaults)
        trail_stop_pct       = inst.get('trail_stop_pct',        2.0)
        take_profit_pct      = inst.get('take_profit_pct',       8.0)
        reentry_recovery_pct = inst.get('reentry_recovery_pct',  1.5)
        reentry_cooldown     = inst.get('reentry_cooldown_mins',  30)
        loss_limit           = inst.get('loss_limit',            200)

        action = "--"

        if pos != 0:
            # Update tracker: raises trail stop as price rises
            self.tracker.update(symbol, price, trail_stop_pct, inst.get('currency','USD'))

            # Per-instrument hard loss limit
            if pos_info.unreal_pnl < -loss_limit:
                log(f"  LOSS LIMIT hit {symbol}: ${pos_info.unreal_pnl:.2f}", "WARN")
                fill_result = self.orders.close(inst, pos)
                if fill_result:
                    exit_price = fill_result.fill_price or price
                    exit_reason = f"LOSS_LIMIT -${abs(pos_info.unreal_pnl):.0f}"
                    self.tracker.on_close(symbol, exit_price, exit_reason, reentry_cooldown)
                    action = f"CLOSED ({exit_reason})"
                    for p in self.plugins:
                        p.post_trade(inst, 0, action, exit_price)
                else:
                    action = "CLOSE FAILED (loss limit)"

            else:
                # Check take profit / trailing stop
                smart_exit = self.tracker.check_exit(
                    symbol, price, take_profit_pct, trail_stop_pct,
                    inst.get('currency', 'USD')
                )
                if smart_exit:
                    fill_result = self.orders.close(inst, pos)
                    if fill_result:
                        exit_price = fill_result.fill_price or price
                        self.tracker.on_close(symbol, exit_price, smart_exit, reentry_cooldown)
                        action = f"CLOSED ({smart_exit})"
                        for p in self.plugins:
                            p.post_trade(inst, 0, action, exit_price)
                    else:
                        action = "CLOSE FAILED (smart exit)"

                # Signal reversal (short-able CFDs only)
                elif result.signal == -1 and not inst.get('long_only', True):
                    allowed = all(p.pre_trade(inst, -1, result.confidence) for p in self.plugins)
                    if allowed:
                        action, fill_result = self.orders.handle_signal(
                            inst, result.signal, result.confidence, pos)
                        if 'FAILED' not in action:
                            exit_price = fill_result.fill_price or price
                            self.tracker.on_close(symbol, exit_price, 'SIGNAL_REVERSED', reentry_cooldown)
                            for p in self.plugins:
                                p.post_trade(inst, -1, action, exit_price)

        else:
            # No position — check for re-entry or fresh entry

            if self.tracker.is_watching(symbol):
                signal_valid = (result.signal == 1 and result.confidence in ('HIGH', 'MEDIUM'))
                should_reenter, re_reason = self.tracker.check_reentry(
                    symbol, price, signal_valid, reentry_recovery_pct
                )
                if should_reenter:
                    allowed = all(p.pre_trade(inst, 1, result.confidence) for p in self.plugins)
                    if allowed:
                        fill_result = self.orders.place(
                            inst['contract'], 'BUY', inst['qty'], inst['name'])
                        if fill_result:
                            fill_price = fill_result.fill_price or price
                            fill_qty = fill_result.filled_qty or inst['qty']
                            self.tracker.on_open(symbol, fill_price, fill_qty,
                                                 trail_stop_pct, inst.get('currency','USD'))
                            self.tracker.clear_watch(symbol)
                            action = f"RE-ENTRY [{result.confidence}]"
                            for p in self.plugins:
                                p.post_trade(inst, 1, action, fill_price)
                            pos_info = self.portfolio.get_position_info(symbol, price)
                        else:
                            action = "RE-ENTRY FAILED"
                else:
                    action = f"WATCHING: {re_reason}"

            elif result.signal == 1:
                # Fresh entry
                allowed = all(p.pre_trade(inst, result.signal, result.confidence) for p in self.plugins)
                if allowed:
                    action, fill_result = self.orders.handle_signal(
                        inst, result.signal, result.confidence, pos)
                    if 'BOUGHT' in action and fill_result:
                        fill_price = fill_result.fill_price or price
                        fill_qty = fill_result.filled_qty or inst['qty']
                        self.tracker.on_open(symbol, fill_price, fill_qty,
                                             trail_stop_pct, inst.get('currency','USD'))
                        for p in self.plugins:
                            p.post_trade(inst, 1, action, fill_price)
                        pos_info = self.portfolio.get_position_info(symbol, price)
                else:
                    action = "BLOCKED by plugin"

            elif result.signal == -1 and not inst.get('long_only', True):
                # Fresh short from flat
                allowed = all(p.pre_trade(inst, result.signal, result.confidence) for p in self.plugins)
                if allowed:
                    action, fill_result = self.orders.handle_signal(
                        inst, result.signal, result.confidence, pos)
                    if 'SHORTED' in action and fill_result:
                        fill_price = fill_result.fill_price or price
                        fill_qty = fill_result.filled_qty or inst['qty']
                        self.tracker.on_open(symbol, fill_price, -fill_qty,
                                             trail_stop_pct, inst.get('currency','USD'),
                                             side='SHORT')
                        for p in self.plugins:
                            p.post_trade(inst, -1, action, fill_price)
                        pos_info = self.portfolio.get_position_info(symbol, price)
                else:
                    action = "BLOCKED by plugin"

        # Refresh position info after any trades
        pos_info   = self.portfolio.get_position_info(symbol, price)
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
            'currency':   pos_info.currency,
            'stop_level': round(stop_level, 4),
            'peak_price': round(peak_price, 4),
            'watching':   round(watch_info.recovery_pct, 2) if watch_info else 0,
            'action':     action,
            'reason':     result.reason,
        }

    def _closed_row(self, inst: dict, mkt_str: str) -> dict:
        pos_info   = self.portfolio.get_position_info(inst['symbol'])
        stop_level = self.tracker.get_stop_level(inst['symbol'])
        peak_price = self.tracker.get_peak(inst['symbol'])
        watch_info = self.tracker.watch_info(inst['symbol'])
        return {
            'symbol':     inst['symbol'],
            'name':       inst['name'],
            'flag':       inst.get('flag', ''),
            'market':     mkt_str,
            'price':      0,
            'alligator':  '--',
            'direction':  '--',
            'ma200':      '--',
            'wr':         0.0,
            'rsi':        50.0,
            'confidence': '--',
            'signal':     'CLOSED',
            'pos':        pos_info.qty,
            'avg_cost':   pos_info.avg_cost,
            'unreal_pnl': 0,
            'pnl_pct':    0,
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
        Uses IBKR live positions directly so nothing is missed.
        """
        closed_symbols = []

        # Close every position IBKR reports — not just Layer 1 instruments
        all_positions = self.portfolio.get_all_positions()
        for p in all_positions:
            if p.position == 0:
                continue
            symbol = p.contract.symbol

            # Skip unmanaged positions (e.g. ghost XAUUSD)
            unmanaged = set(getattr(self.cfg, 'unmanaged_positions', []))
            if symbol in unmanaged:
                log(f"  [Emergency] Skipping unmanaged: {symbol}")
                continue

            # Try to find the instrument config for this symbol (any layer)
            inst = self._find_instrument(symbol)
            if inst and 'contract' in inst:
                fill_result = self.orders.close(inst, p.position)
            else:
                # No config found — build a minimal close using the IBKR contract
                try:
                    side = 'SELL' if p.position > 0 else 'BUY'
                    qty = abs(p.position)
                    fill_result = self.orders.place(
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
        if self.orders.alerts and closed_symbols:
            self.orders.alerts.send(
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
        """Register any IBKR positions not already tracked (e.g. first run)."""
        for inst in self.cfg.active_instruments:
            symbol   = inst['symbol']
            pos_info = self.portfolio.get_position_info(symbol)
            if pos_info.qty != 0 and symbol not in self.tracker.open:
                trail_pct = inst.get('trail_stop_pct', 2.0)
                currency  = inst.get('currency', 'USD')
                self.tracker.init_existing(
                    symbol, pos_info.avg_cost, pos_info.qty,
                    trail_pct, currency
                )

    @staticmethod
    def _signal_str(signal: int) -> str:
        return {1: 'BUY', -1: 'SELL', 0: 'HOLD'}.get(signal, 'HOLD')
