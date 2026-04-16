"""Tests for Fix 2 — disabled instruments are skipped and shown on dashboard."""

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent


def _load_instruments():
    with open(BASE_DIR / 'instruments.json') as f:
        return json.load(f)


def test_disabled_instrument_skipped():
    """Instrument with enabled=false is not traded (Config filters them out)."""
    from bot.config import Config
    cfg = Config(str(BASE_DIR / 'instruments.json'))
    active_symbols = {i['symbol'] for i in cfg.active_instruments}
    assert 'CVX' not in active_symbols, "CVX should be disabled"
    assert 'VRT' not in active_symbols, "VRT should be disabled"


def test_enabled_true_is_traded():
    """Instrument with enabled=true is traded normally."""
    from bot.config import Config
    cfg = Config(str(BASE_DIR / 'instruments.json'))
    active_symbols = {i['symbol'] for i in cfg.active_instruments}
    # These should still be active
    assert 'SGLN' in active_symbols
    assert 'SSLN' in active_symbols


def test_missing_enabled_defaults_true():
    """If enabled field is missing, default to true."""
    from bot.config import Config
    cfg = Config(str(BASE_DIR / 'instruments.json'))
    # Config filters by: i.get('enabled', True)
    # An instrument without 'enabled' key should be included
    data = _load_instruments()
    for inst in data['layer1_active']:
        if 'enabled' not in inst:
            # Should be in active_instruments
            sym = inst['symbol']
            assert sym in {i['symbol'] for i in cfg.active_instruments}, \
                f"{sym} has no 'enabled' field and should default to active"


def test_disabled_shown_on_dashboard():
    """Disabled instruments appear on dashboard as greyed out."""
    from bot.config import Config
    from bot.dashboard import Dashboard

    cfg = Config(str(BASE_DIR / 'instruments.json'))
    dash = Dashboard(cfg)
    disabled_rows = dash._disabled_instrument_rows()

    disabled_symbols = {r['symbol'] for r in disabled_rows}
    assert 'CVX' in disabled_symbols
    assert 'VRT' in disabled_symbols
    for row in disabled_rows:
        assert row['disabled'] is True
        assert row['action'] == 'DISABLED'
        assert row['signal'] == 'DISABLED'


def test_no_edge_instruments_disabled():
    """All no-edge instruments from walk-forward are disabled."""
    data = _load_instruments()
    no_edge = {'CVX', 'VRT', 'CEG', 'CRWD', 'MU', 'ASML', 'GOOGL', 'SHEL', 'FCX'}
    for inst in data['layer1_active']:
        if inst['symbol'] in no_edge:
            assert inst.get('enabled') is False, \
                f"{inst['symbol']} should be disabled (no edge per walk-forward)"
