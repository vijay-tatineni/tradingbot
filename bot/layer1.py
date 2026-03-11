"""
bot/layer1.py  — v7.0
Layer 1 — Active Trading with smart exits and re-entry.

v7.0 changes:
  - All exit paths now call post_trade (loss limit, signal reversal)
  - Syncs existing IBKR positions with tracker on first run
  - Passes proper exit_reason to learning loop
"""

import datetime
from bot.config           import Config
from bot.connection       import IBConnection
from bot.market_hours     import MarketHours
from bot.data             import DataFeed
from bot.indicators       import Indicators
from bot.signals          import SignalEngine
from bot.portfolio        import Portfolio
from bot.orders           import OrderManager
from bot.position_tracker import PositionTracker
from bot.logger           import log, separator


class ActiveTrading:

    def __init__(self, cfg: Config, ib_conn: IBConnection, plugins: list = None):
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

        if self.portfolio.is_emergency_stop(self.total_pnl):
            log(f"EMERGENCY STOP: P&L ${self.total_pnl:.2f} hit loss limit!", "ERROR")
            self._close_all()
            return

        self.signal_rows = []
        for inst in self.cfg.active_instruments:
            row = self._process_instrument(inst)
            self.signal_rows.append(row)
            self.ib_conn.sleep(1)

    def _process_instrument(self, inst: dict) -> dict:
        symbol   = inst['symbol']
        mkt_open = self.hours.is_open(inst)
        mkt_str  = self.hours.status(inst)

        if not mkt_open:
            return self._closed_row(inst, mkt_str)

        df = self.feed.get(inst['contract'])
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
                self.orders.close(inst, pos)
                exit_reason = f"LOSS_LIMIT -${abs(pos_info.unreal_pnl):.0f}"
                self.tracker.on_close(symbol, price, exit_reason, reentry_cooldown)
                action = f"CLOSED ({exit_reason})"
                for p in self.plugins:
                    p.post_trade(inst, 0, action, price)

            else:
                # Check take profit / trailing stop
                smart_exit = self.tracker.check_exit(
                    symbol, price, take_profit_pct, trail_stop_pct,
                    inst.get('currency', 'USD')
                )
                if smart_exit:
                    self.orders.close(inst, pos)
                    self.tracker.on_close(symbol, price, smart_exit, reentry_cooldown)
                    action = f"CLOSED ({smart_exit})"
                    for p in self.plugins:
                        p.post_trade(inst, 0, action, price)

                # Signal reversal (short-able CFDs only)
                elif result.signal == -1 and not inst.get('long_only', True):
                    allowed = all(p.pre_trade(inst, -1, result.confidence) for p in self.plugins)
                    if allowed:
                        action = self.orders.handle_signal(inst, result.signal, result.confidence, pos)
                        self.tracker.on_close(symbol, price, 'SIGNAL_REVERSED', reentry_cooldown)
                        for p in self.plugins:
                            p.post_trade(inst, -1, action, price)

        else:
            # No position — check for re-entry or fresh entry

            if self.tracker.is_watching(symbol):
                # Re-entry: price must have recovered from its post-exit low
                signal_valid = (result.signal == 1 and result.confidence in ('HIGH', 'MEDIUM'))
                should_reenter, re_reason = self.tracker.check_reentry(
                    symbol, price, signal_valid, reentry_recovery_pct
                )
                if should_reenter:
                    allowed = all(p.pre_trade(inst, 1, result.confidence) for p in self.plugins)
                    if allowed:
                        self.orders.place(inst['contract'], 'BUY', inst['qty'], inst['name'])
                        self.tracker.on_open(symbol, price, inst['qty'], trail_stop_pct, inst.get('currency','USD'))
                        self.tracker.clear_watch(symbol)
                        action = f"RE-ENTRY [{result.confidence}]"
                        for p in self.plugins:
                            p.post_trade(inst, 1, action, price)
                        pos_info = self.portfolio.get_position_info(symbol, price)
                else:
                    action = f"WATCHING: {re_reason}"

            elif result.signal == 1:
                # Fresh entry
                allowed = all(p.pre_trade(inst, result.signal, result.confidence) for p in self.plugins)
                if allowed:
                    action = self.orders.handle_signal(inst, result.signal, result.confidence, pos)
                    if 'BOUGHT' in action:
                        self.tracker.on_open(symbol, price, inst['qty'], trail_stop_pct, inst.get('currency','USD'))
                        for p in self.plugins:
                            p.post_trade(inst, 1, action, price)
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
        for inst in self.cfg.active_instruments:
            if 'contract' in inst:
                pos = self.portfolio.get_position(inst['symbol'])
                if pos != 0:
                    self.orders.close(inst, pos)

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
