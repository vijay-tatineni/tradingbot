"""Shared test helpers for the Dynamic Universe shadow tests (synthetic data only —
no downloads, no broker, no live DB)."""
import json

import numpy as np
import pandas as pd


def make_bars(n=300, start=100.0, slope=1.0, volume=2_000_000.0):
    """Deterministic OHLCV frame. slope>0 → clean uptrend (fires breakout entry +
    high ADX); slope==0 → flat (no breakout signal)."""
    close = start + slope * np.arange(n, dtype=float)
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="D"),
        "open": close - 0.3, "high": close + 0.5, "low": close - 0.5,
        "close": close, "volume": np.full(n, float(volume)),
    })


class FlagsStub:
    """Minimal flags object supporting .get(name) (mirrors FeatureFlags.get)."""
    def __init__(self, **vals):
        self._vals = vals

    def get(self, name):
        return bool(self._vals.get(name, False))


ON = FlagsStub(enable_dynamic_universe_shadow=True)
OFF = FlagsStub(enable_dynamic_universe_shadow=False)


def write_configs(tmp_path, instruments, ig_instruments=None):
    """Write minimal instruments.json + instruments_ig.json into tmp_path.
    Returns (instruments_path, instruments_ig_path)."""
    ibkr = {"_version": "1.0", "settings": {"broker": "ibkr"},
            "layer1_active": instruments, "layer2_accumulation": [], "layer3_silver": []}
    ig = {"_version": "1.0", "settings": {"broker": "ig"},
          "layer1_active": ig_instruments if ig_instruments is not None else [],
          "layer2_accumulation": [], "layer3_silver": []}
    p1 = tmp_path / "instruments.json"
    p2 = tmp_path / "instruments_ig.json"
    p1.write_text(json.dumps(ibkr, indent=2))
    p2.write_text(json.dumps(ig, indent=2))
    return str(p1), str(p2)


def inst(symbol, currency="USD", exchange="NASDAQ", enabled=True,
         hard_disabled=False, **extra):
    d = {"symbol": symbol, "currency": currency, "exchange": exchange,
         "enabled": enabled, "name": symbol, "sec_type": "STK"}
    if hard_disabled:
        d["hard_disabled"] = True
        d["enabled"] = False
    d.update(extra)
    return d


class SpyProvider:
    """Bars provider that records calls and asserts no broker is ever touched.
    Maps canonical_instrument_id -> source dict."""
    def __init__(self, sources):
        self.sources = sources
        self.calls = []

    def __call__(self, rec):
        cid = rec["canonical_instrument_id"]
        self.calls.append(cid)
        return self.sources.get(cid)
