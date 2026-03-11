"""
bot/connection.py
IBKR connection management — connect, reconnect, qualify contracts.
"""

import time
from ib_insync import IB, CFD, Stock
from bot.config import Config
from bot.logger import log


class IBConnection:
    """
    Wraps ib_insync IB object.
    Handles initial connection and auto-reconnect on drop.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ib  = IB()
        self.connect()

    def connect(self) -> None:
        """Connect to IB Gateway, retry until successful."""
        while True:
            try:
                if self.ib.isConnected():
                    self.ib.disconnect()
                self.ib.connect(
                    self.cfg.host,
                    self.cfg.port,
                    clientId=self.cfg.client_id
                )
                log(f"Connected: {self.ib.isConnected()}  |  Account: {self.cfg.account}")
                return
            except Exception as e:
                log(f"Connection failed: {e} — retrying in 30s...", "WARN")
                time.sleep(30)

    def reconnect(self) -> None:
        """Called automatically when connection is lost."""
        log("Reconnecting to IB Gateway...", "WARN")
        time.sleep(30)
        self.connect()

    def ensure_connected(self) -> None:
        """Check connection and reconnect if needed."""
        if not self.ib.isConnected():
            self.reconnect()

    def sleep(self, seconds: int) -> None:
        """Sleep using ib_insync event loop."""
        self.ib.sleep(seconds)

    def qualify_contracts(self, instruments: list) -> list:
        """
        Qualify a list of instrument dicts against IBKR.
        Returns only successfully qualified instruments with 'contract' field added.
        """
        qualified = []
        for inst in instruments:
            try:
                contract = self._build_contract(inst)
                self.ib.qualifyContracts(contract)
                inst['contract'] = contract
                qualified.append(inst)
                log(f"  OK   {inst.get('flag','')} {inst['name']:<30} "
                    f"{inst['symbol']:<8} {inst['sec_type']:<4} "
                    f"{inst['exchange']:<6} {inst['currency']}")
            except Exception as e:
                log(f"  FAIL {inst['name']:<30} {inst['symbol']} — {e}", "WARN")
        return qualified

    @staticmethod
    def _build_contract(inst: dict):
        if inst['sec_type'] == 'CFD':
            return CFD(inst['symbol'], inst['exchange'], inst['currency'])
        return Stock(inst['symbol'], inst['exchange'], inst['currency'])
