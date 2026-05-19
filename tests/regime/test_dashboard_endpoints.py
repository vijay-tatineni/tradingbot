"""§15.5: API endpoint tests for regime dashboard tabs."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest


def _setup_regime_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS regime_classification_cache (
        instrument TEXT, trading_date TEXT, input_hash TEXT,
        classification_json TEXT, created_at TEXT,
        PRIMARY KEY (instrument, trading_date, input_hash))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS instrument_entry_pauses (
        instrument TEXT PRIMARY KEY, paused_at TEXT,
        paused_by_overlay TEXT, reason TEXT, cleared_at TEXT, cleared_by TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS degradation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, component TEXT,
        severity TEXT, trigger_reason TEXT, action_taken TEXT,
        flag_disabled TEXT, instruments_paused TEXT, recovery_instructions TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS shadow_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, instrument TEXT,
        bar_time TEXT, live_engine TEXT, live_signal_json TEXT,
        live_action_taken TEXT, live_trade_id TEXT, shadow_regime TEXT,
        shadow_confidence REAL, shadow_smoothed_regime TEXT,
        shadow_smoothed_days_in_regime INTEGER, shadow_overlays_active TEXT,
        shadow_engine_selected TEXT, shadow_signal_json TEXT,
        shadow_action_would_be TEXT, disagreement_type TEXT,
        hypothetical_trade_id TEXT, flag_snapshot_json TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS macro_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, event_date TEXT NOT NULL,
        event_time TEXT, name TEXT NOT NULL, source TEXT NOT NULL,
        impact TEXT DEFAULT 'medium', region TEXT DEFAULT 'US',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE (event_date, event_time, name, region))""")
    conn.commit()
    conn.close()


def _write_config(path: str, symbols: list) -> None:
    cfg = {
        "_version": "1.0",
        "settings": {"broker": "test", "check_interval_mins": 5,
                     "portfolio_loss_limit": 1000, "alligator_min_gap_pct": 0.003,
                     "ma200_period": 200, "williams_r_period": 14,
                     "williams_r_mid": -50, "williams_r_oversold": -80,
                     "williams_r_overbought": -20, "rsi_period": 14,
                     "rsi_oversold": 35, "rsi_overbought": 70},
        "layer1_active": [{"symbol": s, "enabled": True} for s in symbols],
        "layer2_accumulation": [],
    }
    with open(path, 'w') as f:
        json.dump(cfg, f)


@pytest.fixture
def api_setup(tmp_path):
    db = str(tmp_path / "regime.db")
    _setup_regime_db(db)
    cfg = str(tmp_path / "instruments.json")
    _write_config(cfg, ["AAPL", "MSFT"])

    import api_server
    api_server.REGIME_DB = db
    api_server.CONFIG_FILE = cfg
    api_server.JWT_SECRET = "endpoint_test_secret"

    # Re-init the overlay registry against the test DB so macro events
    # load from the right place.
    from bot.overlays.registry import init_overlay_registry
    init_overlay_registry(db)

    app = api_server.app
    app.config['TESTING'] = True
    token = api_server.create_token("tester")
    client = app.test_client()
    return client, token, db, cfg


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _insert_regime(db, instrument, raw_regime, confidence=0.85):
    conn = sqlite3.connect(db)
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "instrument": instrument, "classified_at": now,
        "trading_date": "2026-05-19", "raw_regime": raw_regime,
        "confidence": confidence, "rationale": "test", "features": {},
        "model_version": "v1", "prompt_version": "v1", "input_hash": "h1",
    }
    conn.execute(
        "INSERT INTO regime_classification_cache VALUES (?,?,?,?,?)",
        (instrument, "2026-05-19", "h1", json.dumps(payload), now),
    )
    conn.commit()
    conn.close()


def _insert_shadow(db, instrument, **kwargs):
    conn = sqlite3.connect(db)
    now = datetime.now(timezone.utc).isoformat()
    cols = {
        "ts": now, "instrument": instrument, "bar_time": now,
        "shadow_regime": "TRENDING", "shadow_smoothed_regime": "TRENDING",
        "shadow_smoothed_days_in_regime": 3,
        "shadow_engine_selected": "TripleConfirmationEngine",
        "shadow_overlays_active": "",
        "shadow_action_would_be": "BUY", "live_engine": "TripleConfirmationEngine",
        "live_action_taken": "BUY", "disagreement_type": None,
    }
    cols.update(kwargs)
    keys = ",".join(cols.keys())
    placeholders = ",".join("?" * len(cols))
    conn.execute(
        f"INSERT INTO shadow_decisions ({keys}) VALUES ({placeholders})",
        tuple(cols.values()),
    )
    conn.commit()
    conn.close()


def _insert_pause(db, instrument, cleared=False):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO instrument_entry_pauses (instrument, paused_at, "
        "paused_by_overlay, reason, cleared_at, cleared_by) "
        "VALUES (?,?,?,?,?,?)",
        (instrument, "2026-05-19T14:00:00", "DATA_QUALITY", "stale bars",
         "2026-05-19T15:00:00" if cleared else None,
         "auto" if cleared else None),
    )
    conn.commit()
    conn.close()


def _insert_degradation(db, component="classifier"):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO degradation_events (ts, component, severity, "
        "trigger_reason, action_taken) VALUES (?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), component, "hard",
         "3 failures", "Disabled flag"),
    )
    conn.commit()
    conn.close()


def _insert_macro_event(db, name, days_from_now=0, event_time=None):
    """Date-only events trigger a full-day lockout, avoiding wall-clock flakiness."""
    from datetime import timedelta
    conn = sqlite3.connect(db)
    event_date = (datetime.now(timezone.utc) + timedelta(days=days_from_now)).strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO macro_events (event_date, event_time, name, source, "
        "impact, region, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (event_date, event_time, name, "test", "high", "US", now, now),
    )
    conn.commit()
    conn.close()


# ─── Auth enforcement ─────────────────────────────────

ENDPOINTS = [
    '/api/regime/states',
    '/api/overlays/active',
    '/api/routing/decisions',
    '/api/shadow/comparison',
    '/api/degradation/events',
    '/api/pauses/list',
]


@pytest.mark.parametrize("path", ENDPOINTS)
def test_no_token_returns_401(api_setup, path):
    client, _, _, _ = api_setup
    r = client.get(path)
    assert r.status_code == 401


@pytest.mark.parametrize("path", ENDPOINTS)
def test_bad_token_returns_401(api_setup, path):
    client, _, _, _ = api_setup
    r = client.get(path, headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


# ─── /api/regime/states ───────────────────────────────

class TestRegimeStates:
    def test_empty(self, api_setup):
        client, token, _, _ = api_setup
        r = client.get('/api/regime/states', headers=_auth(token))
        assert r.status_code == 200
        assert r.get_json() == []

    def test_populated_returns_flattened_fields(self, api_setup):
        client, token, db, _ = api_setup
        _insert_regime(db, "AAPL", "TRENDING", confidence=0.91)
        _insert_shadow(db, "AAPL", shadow_smoothed_regime="TRENDING",
                       shadow_smoothed_days_in_regime=4)
        r = client.get('/api/regime/states', headers=_auth(token))
        assert r.status_code == 200
        rows = r.get_json()
        assert len(rows) == 1
        row = rows[0]
        assert row["instrument"] == "AAPL"
        assert row["raw_regime"] == "TRENDING"
        assert row["confidence"] == 0.91
        assert row["smoothed_regime"] == "TRENDING"
        assert row["days_in_regime"] == 4


# ─── /api/overlays/active ─────────────────────────────

class TestOverlaysActive:
    def test_empty(self, api_setup):
        client, token, _, _ = api_setup
        r = client.get('/api/overlays/active', headers=_auth(token))
        assert r.status_code == 200
        assert r.get_json() == []

    def test_macro_event_today_fires_overlay(self, api_setup):
        client, token, db, _ = api_setup
        _insert_macro_event(db, "FOMC Rate Decision", days_from_now=0)
        r = client.get('/api/overlays/active', headers=_auth(token))
        assert r.status_code == 200
        rows = r.get_json()
        # MACRO_LOCKOUT should be present and aggregate both configured
        # instruments (AAPL, MSFT).
        names = [row["overlay_name"] for row in rows]
        assert "MACRO_LOCKOUT" in names
        macro_row = [r for r in rows if r["overlay_name"] == "MACRO_LOCKOUT"][0]
        assert set(macro_row["instruments_affected"]) == {"AAPL", "MSFT"}


# ─── /api/routing/decisions ───────────────────────────

class TestRoutingDecisions:
    def test_empty(self, api_setup):
        client, token, _, _ = api_setup
        r = client.get('/api/routing/decisions', headers=_auth(token))
        assert r.status_code == 200
        assert r.get_json() == []

    def test_returns_latest_per_instrument(self, api_setup):
        client, token, db, _ = api_setup
        _insert_shadow(db, "AAPL", shadow_smoothed_regime="UNCLEAR",
                       shadow_engine_selected="NoOpEngine")
        _insert_shadow(db, "AAPL", shadow_smoothed_regime="TRENDING",
                       shadow_engine_selected="TripleConfirmationEngine")
        _insert_shadow(db, "MSFT", shadow_smoothed_regime="RANGING",
                       shadow_engine_selected="MeanReversionEngine")
        r = client.get('/api/routing/decisions', headers=_auth(token))
        assert r.status_code == 200
        rows = r.get_json()
        by_instr = {r["instrument"]: r for r in rows}
        assert len(rows) == 2
        assert by_instr["AAPL"]["smoothed_regime"] == "TRENDING"
        assert by_instr["AAPL"]["allow_new_entries"] is True
        assert "block_reason" in by_instr["AAPL"]


# ─── /api/shadow/comparison ───────────────────────────

class TestShadowComparison:
    def test_empty(self, api_setup):
        client, token, _, _ = api_setup
        r = client.get('/api/shadow/comparison', headers=_auth(token))
        assert r.status_code == 200
        assert r.get_json() == []

    def test_populated(self, api_setup):
        client, token, db, _ = api_setup
        _insert_shadow(db, "AAPL")
        _insert_shadow(db, "AAPL", disagreement_type="ENGINE_MISMATCH")
        r = client.get('/api/shadow/comparison', headers=_auth(token))
        assert r.status_code == 200
        rows = r.get_json()
        assert len(rows) == 2
        # Should be ts DESC — latest first
        assert rows[0]["instrument"] == "AAPL"


# ─── /api/degradation/events ──────────────────────────

class TestDegradationEvents:
    def test_empty(self, api_setup):
        client, token, _, _ = api_setup
        r = client.get('/api/degradation/events', headers=_auth(token))
        assert r.status_code == 200
        assert r.get_json() == []

    def test_populated(self, api_setup):
        client, token, db, _ = api_setup
        _insert_degradation(db, component="classifier")
        r = client.get('/api/degradation/events', headers=_auth(token))
        assert r.status_code == 200
        rows = r.get_json()
        assert len(rows) == 1
        assert rows[0]["component"] == "classifier"


# ─── /api/pauses/list ─────────────────────────────────

class TestPausesList:
    def test_empty(self, api_setup):
        client, token, _, _ = api_setup
        r = client.get('/api/pauses/list', headers=_auth(token))
        assert r.status_code == 200
        assert r.get_json() == []

    def test_active_only(self, api_setup):
        client, token, db, _ = api_setup
        _insert_pause(db, "AAPL", cleared=False)
        _insert_pause(db, "MSFT", cleared=True)
        r = client.get('/api/pauses/list', headers=_auth(token))
        assert r.status_code == 200
        rows = r.get_json()
        assert len(rows) == 1
        assert rows[0]["instrument"] == "AAPL"
