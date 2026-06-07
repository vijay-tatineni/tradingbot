"""
tests/test_hard_disabled_guardrail.py

Tests for the hard-disabled invariant (bot/guardrails.py +
api_server.py + main.py + bot/config.py).

No normal dashboard/API write may leave a broker-ineligible /
administratively hard-disabled instrument enabled. The authoritative
structured flag is `hard_disabled: true`; the legacy free-text reason
`disabled_reason == "no_cfd_market_data_paper_account"` is accepted as
backward-compatible defense-in-depth only.

Coverage:
  * validator unit cases (flag, legacy reason, defaults, bypass attempts);
  * startup config validation (main.validate_environment + Config);
  * every API writer path: full-config save, layer1 save, toggle-enable,
    apply-wf, and the save() backstop / errorhandler for the remaining
    routes;
  * rejected writes leave the config file unchanged and emit an audit entry;
  * valid unrelated changes still save;
  * XAUUSD / XAGUSD remain disabled after every attempted writer path.
"""

import importlib
import json
import logging
import os
import sqlite3

import pytest

from bot.guardrails import (
    validate_hard_disabled_instruments,
    HARD_DISABLED_REASON,
    HardDisabledViolation,
)

from pathlib import Path

BASE_DIR = Path(__file__).parent.parent


# ════════════════════════════════════════════════════════════════════
# Validator unit tests
# ════════════════════════════════════════════════════════════════════

def _cfg(*instruments):
    return {"layer1_active": list(instruments)}


def _inst(symbol="X", enabled=True, **extra):
    d = {"symbol": symbol, "name": symbol, "enabled": enabled}
    d.update(extra)
    return d


def test_validator_flags_enabled_hard_disabled():
    errs = validate_hard_disabled_instruments(
        _cfg(_inst("XAUUSD", enabled=True, hard_disabled=True)))
    assert len(errs) == 1
    assert "XAUUSD" in errs[0]


def test_validator_flags_enabled_legacy_reason_only():
    """Defense in depth: the legacy free-text reason alone (no structured
    flag) must still be rejected."""
    errs = validate_hard_disabled_instruments(
        _cfg(_inst("XAGUSD", enabled=True, disabled_reason=HARD_DISABLED_REASON)))
    assert len(errs) == 1
    assert "XAGUSD" in errs[0]


def test_validator_passes_disabled_hard_disabled():
    """The correct resting state — disabled + hard_disabled — is clean."""
    errs = validate_hard_disabled_instruments(
        _cfg(_inst("XAUUSD", enabled=False, hard_disabled=True,
                   disabled_reason=HARD_DISABLED_REASON)))
    assert errs == []


def test_validator_passes_clean_enabled():
    errs = validate_hard_disabled_instruments(
        _cfg(_inst("MSFT", enabled=True)))
    assert errs == []


def test_validator_hard_disabled_false_passes():
    """hard_disabled:false with no legacy reason is a normal instrument."""
    errs = validate_hard_disabled_instruments(
        _cfg(_inst("MSFT", enabled=True, hard_disabled=False)))
    assert errs == []


def test_validator_missing_enabled_defaults_true():
    """Missing 'enabled' defaults to True (fail closed), so an instrument
    that omits the flag but is hard-disabled is still flagged."""
    errs = validate_hard_disabled_instruments(
        _cfg({"symbol": "XAUUSD", "hard_disabled": True}))
    assert len(errs) == 1


def test_validator_free_text_reason_change_cannot_bypass():
    """Editing disabled_reason away from the legacy string does NOT bypass
    the invariant while hard_disabled:true remains the authoritative flag."""
    errs = validate_hard_disabled_instruments(
        _cfg(_inst("XAUUSD", enabled=True, hard_disabled=True,
                   disabled_reason="user wants this on")))
    assert len(errs) == 1
    assert "hard_disabled" in errs[0]


def test_validator_multiple_offenders_all_reported():
    errs = validate_hard_disabled_instruments(_cfg(
        _inst("XAUUSD", enabled=True, hard_disabled=True),
        _inst("XAGUSD", enabled=True, disabled_reason=HARD_DISABLED_REASON),
        _inst("MSFT", enabled=True),
    ))
    joined = " ".join(errs)
    assert "XAUUSD" in joined and "XAGUSD" in joined and "MSFT" not in joined
    assert len(errs) == 2


def test_live_instruments_json_xau_xag_disabled_and_clean():
    """The committed live config must pass the invariant, and XAU/XAG must
    carry hard_disabled:true while remaining disabled."""
    with open(BASE_DIR / "instruments.json") as f:
        cfg = json.load(f)
    assert validate_hard_disabled_instruments(cfg) == []
    by_sym = {i["symbol"]: i for i in cfg["layer1_active"]}
    for sym in ("XAUUSD", "XAGUSD"):
        assert by_sym[sym]["hard_disabled"] is True
        assert by_sym[sym]["enabled"] is False
        assert by_sym[sym]["disabled_reason"] == HARD_DISABLED_REASON


# ════════════════════════════════════════════════════════════════════
# Startup validation
# ════════════════════════════════════════════════════════════════════

def _write_cfg(path, instruments, broker="ibkr"):
    data = {
        "settings": {
            "host": "127.0.0.1", "port": 4000, "client_id": 1,
            "account": "TEST", "check_interval_mins": 1,
            "portfolio_loss_limit": 1000, "web_dir": "web",
            "broker": broker,
        },
        "layer1_active": instruments,
        "layer2_accumulation": [],
    }
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def _ibkr_inst(symbol, enabled, **extra):
    d = {"symbol": symbol, "name": symbol, "sec_type": "STK",
         "exchange": "SMART", "currency": "USD", "qty": 1, "enabled": enabled}
    d.update(extra)
    return d


def test_startup_rejects_enabled_hard_disabled(tmp_path):
    """main.validate_environment exits non-zero when a hard-disabled
    instrument is enabled."""
    import main
    cfg = str(tmp_path / "instruments.json")
    _write_cfg(cfg, [_ibkr_inst("XAUUSD", True, hard_disabled=True)])
    with pytest.raises(SystemExit) as exc:
        main.validate_environment(config_file=cfg)
    assert exc.value.code == 1


def test_startup_accepts_disabled_hard_disabled(tmp_path):
    """A correctly disabled hard-disabled instrument does not block startup."""
    import main
    cfg = str(tmp_path / "instruments.json")
    _write_cfg(cfg, [_ibkr_inst("XAUUSD", False, hard_disabled=True),
                     _ibkr_inst("MSFT", True)])
    # Should not raise SystemExit for the hard-disabled invariant.
    main.validate_environment(config_file=cfg)


def test_config_construction_rejects_enabled_hard_disabled(tmp_path):
    """bot.config.Config refuses to construct on an enabled hard-disabled
    instrument (defense in depth)."""
    from bot.config import Config
    from bot.guardrails import ConfigGuardrailError
    cfg = str(tmp_path / "instruments.json")
    _write_cfg(cfg, [_ibkr_inst("XAUUSD", True, hard_disabled=True)])
    with pytest.raises(ConfigGuardrailError):
        Config(cfg)


# ════════════════════════════════════════════════════════════════════
# API writer paths
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def api(tmp_path):
    """Flask test client with a temp config holding a normal instrument plus
    XAUUSD (structured hard_disabled) and XAGUSD (legacy reason only)."""
    instruments = [
        _ibkr_inst("MSFT", True, trail_stop_pct=2.0, take_profit_pct=8.0,
                   emergency_stop_pct=5.0),
        _ibkr_inst("XAUUSD", False, hard_disabled=True,
                   disabled_reason=HARD_DISABLED_REASON,
                   trail_stop_pct=5.0, take_profit_pct=20.0,
                   emergency_stop_pct=10.0),
        _ibkr_inst("XAGUSD", False, disabled_reason=HARD_DISABLED_REASON,
                   trail_stop_pct=1.0, take_profit_pct=10.0,
                   emergency_stop_pct=5.0),
    ]
    config_file = str(tmp_path / "instruments.json")
    _write_cfg(config_file, instruments)

    backup_dir = str(tmp_path / "backups")
    os.makedirs(backup_dir, exist_ok=True)

    db_path = str(tmp_path / "backtest.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wf_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT, symbol TEXT, timeframe TEXT,
            is_pnl REAL, is_profit_factor REAL, is_win_rate REAL, is_trade_count INTEGER,
            oos_pnl REAL, oos_profit_factor REAL, oos_win_rate REAL, oos_trade_count INTEGER,
            wf_efficiency REAL, best_stop_pct REAL, best_tp_pct REAL,
            param_stability TEXT, verdict TEXT, train_months INTEGER, test_months INTEGER
        );
    """)
    # XAUUSD has a 'robust' WF row so apply-wf would try to touch it.
    conn.execute(
        "INSERT INTO wf_results VALUES (NULL, '2026-03-25', 'XAUUSD', '4hr', "
        "50000, 3.5, 0.72, 400, 33791, 2.85, 0.70, 373, 0.76, 7.0, 22.0, "
        "'stable', 'robust', 6, 3)"
    )
    conn.commit()
    conn.close()

    import api_server
    importlib.reload(api_server)  # ensure a clean app/errorhandler each run
    api_server.CONFIG_FILE = config_file
    api_server.BACKUP_DIR = backup_dir
    api_server.BACKTEST_DB = db_path
    api_server.JWT_SECRET = "test_secret_key_12345"
    api_server.app.config["TESTING"] = True

    token = api_server.create_token("testuser")
    client = api_server.app.test_client()
    return client, token, config_file, api_server


def _hdr(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _read(config_file):
    with open(config_file) as f:
        return json.load(f)


def _sym(cfg, symbol):
    return next(i for i in cfg["layer1_active"] if i["symbol"] == symbol)


# ── toggle-enable ────────────────────────────────────────────────────

def test_toggle_enable_rejects_hard_disabled(api, caplog):
    client, token, cfg_file, _ = api
    before = open(cfg_file).read()
    with caplog.at_level(logging.WARNING, logger="cogniflowai.audit"):
        r = client.post("/api/instruments/toggle-enable", headers=_hdr(token),
                        data=json.dumps({"symbol": "XAUUSD", "enabled": True}))
    assert r.status_code in (400, 409)
    assert _sym(_read(cfg_file), "XAUUSD")["enabled"] is False
    assert open(cfg_file).read() == before                  # file unchanged
    assert any("HARD_DISABLED_REJECT" in m for m in caplog.messages)


def test_toggle_enable_rejects_legacy_reason(api):
    """XAGUSD has only the legacy free-text reason — still cannot be enabled."""
    client, token, cfg_file, _ = api
    r = client.post("/api/instruments/toggle-enable", headers=_hdr(token),
                    data=json.dumps({"symbol": "XAGUSD", "enabled": True}))
    assert r.status_code in (400, 409)
    assert _sym(_read(cfg_file), "XAGUSD")["enabled"] is False


def test_toggle_disable_normal_instrument_still_works(api):
    """Valid unrelated change (disable a normal instrument) still saves."""
    client, token, cfg_file, _ = api
    r = client.post("/api/instruments/toggle-enable", headers=_hdr(token),
                    data=json.dumps({"symbol": "MSFT", "enabled": False}))
    assert r.status_code == 200
    assert _sym(_read(cfg_file), "MSFT")["enabled"] is False


# ── full-config save (POST /api/instruments) ─────────────────────────

def test_full_config_save_rejects_enabled_hard_disabled(api):
    client, token, cfg_file, _ = api
    data = _read(cfg_file)
    _sym(data, "XAUUSD")["enabled"] = True
    before = open(cfg_file).read()
    r = client.post("/api/instruments", headers=_hdr(token), data=json.dumps(data))
    assert r.status_code in (400, 409)
    assert open(cfg_file).read() == before
    assert _sym(_read(cfg_file), "XAUUSD")["enabled"] is False


def test_full_config_save_free_text_reason_change_cannot_bypass(api):
    """Editing disabled_reason while enabling XAUUSD cannot bypass the flag."""
    client, token, cfg_file, _ = api
    data = _read(cfg_file)
    x = _sym(data, "XAUUSD")
    x["enabled"] = True
    x["disabled_reason"] = "operator override"   # legacy match removed…
    # …but hard_disabled:true remains authoritative.
    r = client.post("/api/instruments", headers=_hdr(token), data=json.dumps(data))
    assert r.status_code in (400, 409)
    assert _sym(_read(cfg_file), "XAUUSD")["enabled"] is False


def test_full_config_save_valid_change_succeeds(api):
    """A valid full-config save (no hard-disabled enabled) still works."""
    client, token, cfg_file, _ = api
    data = _read(cfg_file)
    _sym(data, "MSFT")["trail_stop_pct"] = 3.0
    r = client.post("/api/instruments", headers=_hdr(token), data=json.dumps(data))
    assert r.status_code == 200
    assert _sym(_read(cfg_file), "MSFT")["trail_stop_pct"] == 3.0


# ── layer1 save (POST /api/instruments/layer1) ───────────────────────

def test_layer1_save_rejects_enabled_hard_disabled(api):
    client, token, cfg_file, _ = api
    data = _read(cfg_file)
    layer1 = data["layer1_active"]
    _sym({"layer1_active": layer1}, "XAUUSD")["enabled"] = True
    before = open(cfg_file).read()
    r = client.post("/api/instruments/layer1", headers=_hdr(token),
                    data=json.dumps(layer1))
    assert r.status_code in (400, 409)
    assert open(cfg_file).read() == before
    assert _sym(_read(cfg_file), "XAUUSD")["enabled"] is False


# ── apply-wf (POST /api/instruments/apply-wf) ────────────────────────

def test_apply_wf_cannot_enable_hard_disabled(api):
    """apply-wf updates stops but must never enable XAUUSD; it stays
    disabled regardless of its WF verdict."""
    client, token, cfg_file, _ = api
    r = client.post("/api/instruments/apply-wf", headers=_hdr(token),
                    data=json.dumps({"symbols": ["XAUUSD"]}))
    assert r.status_code in (200, 400, 409)
    # The invariant — XAUUSD remains disabled no matter what apply-wf did.
    assert _sym(_read(cfg_file), "XAUUSD")["enabled"] is False


# ── backstop routes (errorhandler → 409, never 500) ──────────────────

def test_save_backstop_raises_on_corrupt_config(api):
    """Directly exercising save() with an enabled hard-disabled instrument
    raises the typed violation and leaves the file untouched."""
    client, token, cfg_file, api_server = api
    data = _read(cfg_file)
    _sym(data, "XAUUSD")["enabled"] = True
    before = open(cfg_file).read()
    with pytest.raises(HardDisabledViolation):
        api_server.save(data)
    assert open(cfg_file).read() == before


def test_backstop_route_returns_409_not_500(api, caplog):
    """If the on-disk config is already corrupt (enabled hard-disabled), a
    backstop-only route (global-settings) returns a clean 409 via the
    errorhandler — never an unhandled 500 — and audits."""
    client, token, cfg_file, _ = api
    # Seed a corrupt on-disk state directly (bypassing the API).
    data = _read(cfg_file)
    _sym(data, "XAUUSD")["enabled"] = True
    with open(cfg_file, "w") as f:
        json.dump(data, f)
    with caplog.at_level(logging.WARNING, logger="cogniflowai.audit"):
        r = client.post("/api/instruments/global-settings", headers=_hdr(token),
                        data=json.dumps({"check_interval_mins": 2}))
    assert r.status_code == 409
    assert any("HARD_DISABLED_REJECT" in m for m in caplog.messages)


# ── invariant holds across every writer path ─────────────────────────

def test_xau_xag_remain_disabled_after_all_writer_paths(api):
    client, token, cfg_file, _ = api
    attempts = [
        ("/api/instruments/toggle-enable",
         {"symbol": "XAUUSD", "enabled": True}),
        ("/api/instruments/toggle-enable",
         {"symbol": "XAGUSD", "enabled": True}),
    ]
    for route, payload in attempts:
        client.post(route, headers=_hdr(token), data=json.dumps(payload))

    # Full-config + layer1 attempts that flip both on.
    data = _read(cfg_file)
    for s in ("XAUUSD", "XAGUSD"):
        _sym(data, s)["enabled"] = True
    client.post("/api/instruments", headers=_hdr(token), data=json.dumps(data))
    client.post("/api/instruments/layer1", headers=_hdr(token),
                data=json.dumps(data["layer1_active"]))

    final = _read(cfg_file)
    assert _sym(final, "XAUUSD")["enabled"] is False
    assert _sym(final, "XAGUSD")["enabled"] is False
