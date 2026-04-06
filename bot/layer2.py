"""
bot/layer2.py
Layer 2 — Accumulation ETFs (RSI + Williams %R dip buying).

Runs every 6th cycle from main loop.
Buys ETFs when RSI < oversold AND Williams %R < oversold.
Sells 50% when RSI > overbought AND Williams %R > overbought.
"""

from bot.config       import Config
from bot.brokers.base import BaseBroker
from bot.market_hours import MarketHours
from bot.indicators   import Indicators
from bot.logger       import log, separator


class Accumulation:

    def __init__(self, cfg: Config, broker: BaseBroker):
        self.cfg       = cfg
        self.broker    = broker
        self.hours     = MarketHours()
        self.indics    = Indicators(cfg)
        self.accum_rows: list = []

    def run(self) -> None:
        """Run one Layer 2 accumulation cycle."""
        separator("LAYER 2: ACCUMULATION ETFs")
        self.accum_rows = []

        for inst in self.cfg.accum_instruments:
            row = self._process(inst)
            self.accum_rows.append(row)
            self.broker.sleep(1)

    def _process(self, inst: dict) -> dict:
        symbol   = inst['symbol']
        mkt_open = self.hours.is_open(inst)

        if not mkt_open:
            return {'symbol': symbol, 'name': inst['name'],
                    'flag': inst.get('flag',''), 'price': 0,
                    'rsi': 50, 'wr': 0, 'pos': 0, 'action': '--'}

        df = self.broker.fetch_bars(inst['contract'])
        if df is None:
            return {'symbol': symbol, 'name': inst['name'],
                    'flag': inst.get('flag',''), 'price': 0,
                    'rsi': 50, 'wr': 0, 'pos': 0, 'action': '--'}

        ind_settings = self.cfg.get_indicator_settings(inst)
        bundle = self.indics.calculate(df, indicator_settings=ind_settings)
        if bundle is None:
            return {'symbol': symbol, 'name': inst['name'],
                    'flag': inst.get('flag',''), 'price': bundle.price if bundle else 0,
                    'rsi': 50, 'wr': 0, 'pos': 0, 'action': '--'}

        price = bundle.price
        rsi   = bundle.rsi
        wr    = bundle.wr.value
        pos   = self.broker.get_position(symbol)
        action = "--"

        oversold   = rsi < ind_settings['rsi_oversold']   and wr < ind_settings['williams_r_oversold']
        overbought = rsi > ind_settings['rsi_overbought'] and wr > ind_settings['williams_r_overbought']

        if oversold and pos == 0:
            result = self.broker.place_order(inst['contract'], 'BUY', inst['qty'], inst['name'])
            if result:
                action = f"BOUGHT DIP [RSI {rsi:.1f}]"
                pos = self.broker.get_position(symbol)  # refresh after fill
            else:
                action = "BUY FAILED"
        elif overbought and pos > 0:
            sell_qty = max(1, pos // 2)
            result = self.broker.place_order(inst['contract'], 'SELL', sell_qty, inst['name'])
            if result:
                action = f"SOLD 50% [RSI {rsi:.1f}]"
                pos = self.broker.get_position(symbol)  # refresh after fill
            else:
                action = "SELL FAILED"

        log(f"  {symbol:<6} price:{price:>8.2f}  RSI:{rsi:>5.1f}  WR:{wr:>6.1f}  "
            f"pos:{pos:.0f}  {action}")

        return {
            'symbol': symbol, 'name': inst['name'], 'flag': inst.get('flag',''),
            'price': price, 'rsi': rsi, 'wr': wr, 'pos': pos, 'action': action,
        }
