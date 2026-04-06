"""
bot/config.py
Loads instruments.json and exposes all settings as typed properties.
To change any setting — edit instruments.json and restart. No code changes needed.
"""

import json
import os
import sys
from pathlib import Path
from bot.logger import log

BASE_DIR = Path(__file__).parent.parent
CONFIG_FILE = str(BASE_DIR / 'instruments.json')


class Config:
    """
    Single source of truth for all bot settings and instruments.
    Loaded once at startup from instruments.json.
    """

    def __init__(self, path: str = CONFIG_FILE):
        self.path = path
        self._raw = self._load()
        s = self._raw['settings']

        # ── Connection ────────────────────────────────────────
        self.host       : str   = s['host']
        self.port       : int   = s['port']
        self.client_id  : int   = s['client_id']
        self.account    : str   = s['account']

        # ── Timing ────────────────────────────────────────────
        self.check_interval_mins : int = s['check_interval_mins']
        self.check_interval      : int = s['check_interval_mins'] * 60

        # ── Risk ──────────────────────────────────────────────
        self.portfolio_loss_limit : float = s['portfolio_loss_limit']

        # ── Web dashboard ─────────────────────────────────────
        web_raw = s.get('web_dir', 'web')
        if web_raw.startswith('~'):
            self.web_dir : str = os.path.expanduser(web_raw)
        elif os.path.isabs(web_raw):
            self.web_dir : str = web_raw
        else:
            self.web_dir : str = str(BASE_DIR / web_raw)

        # ── Alligator ─────────────────────────────────────────
        self.alligator_min_gap_pct : float = s['alligator_min_gap_pct']

        # ── MA200 ─────────────────────────────────────────────
        self.ma200_period : int = s['ma200_period']

        # ── Williams %R ───────────────────────────────────────
        self.williams_r_period     : int   = s['williams_r_period']
        self.williams_r_mid        : float = s['williams_r_mid']
        self.williams_r_oversold   : float = s['williams_r_oversold']
        self.williams_r_overbought : float = s['williams_r_overbought']

        # ── RSI ───────────────────────────────────────────────
        self.rsi_period     : int   = s['rsi_period']
        self.rsi_oversold   : float = s['rsi_oversold']
        self.rsi_overbought : float = s['rsi_overbought']

        # ── ADX ──────────────────────────────────────────────
        self.adx_period    : int   = s.get('adx_period', 14)
        self.adx_threshold : float = s.get('adx_threshold', 20)

        # ── Unmanaged positions (excluded from P&L / emergency stop) ──
        self.unmanaged_positions : list = s.get('unmanaged_positions', [])

        # ── Instruments ───────────────────────────────────────
        self.active_instruments : list = [
            i for i in self._raw['layer1_active']
            if i.get('enabled', True)
        ]
        self.accum_instruments : list = [
            i for i in self._raw['layer2_accumulation']
            if i.get('enabled', True)
        ]

        log(f"Config loaded: {path}")
        log(f"  Active    : {len(self.active_instruments)} instruments")
        log(f"  Accum     : {len(self.accum_instruments)} instruments")
        log(f"  Interval  : every {self.check_interval_mins} minutes")
        log(f"  Risk limit: ${self.portfolio_loss_limit}")

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            print(f"ERROR: Config file not found: {self.path}")
            sys.exit(1)
        try:
            with open(self.path) as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"ERROR: instruments.json has a syntax error: {e}")
            print(f"Run: python3 -c \"import json; json.load(open('{self.path}'))\"")
            sys.exit(1)

    # ── Settings dict for backtest/offline use ─────────────────
    @property
    def _settings(self) -> dict:
        return self._raw.get('settings', {})

    def get_indicator_settings(self, instrument: dict) -> dict:
        """
        Get resolved indicator settings for an instrument.
        Per-instrument overrides take priority over global defaults.
        """
        s = self._settings
        settings = {
            "rsi_period": s.get("rsi_period", 14),
            "rsi_oversold": s.get("rsi_oversold", 35),
            "rsi_overbought": s.get("rsi_overbought", 70),
            "williams_r_period": s.get("williams_r_period", 14),
            "williams_r_mid": s.get("williams_r_mid", -50),
            "williams_r_oversold": s.get("williams_r_oversold", -80),
            "williams_r_overbought": s.get("williams_r_overbought", -20),
            "adx_period": s.get("adx_period", 14),
            "adx_threshold": s.get("adx_threshold", 20),
            "ma200_period": s.get("ma200_period", 200),
            "alligator_min_gap_pct": s.get("alligator_min_gap_pct", 0.003),
        }

        # Apply per-instrument overrides (skip None values — null means revert to global)
        overrides = instrument.get("indicators", {})
        for key, val in overrides.items():
            if val is not None:
                settings[key] = val

        return settings

    def reload(self) -> None:
        """Reload config from disk — call after editing instruments.json without restart."""
        self.__init__(self.path)
