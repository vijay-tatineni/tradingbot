"""Tests for the Classify-tab backend endpoints (/api/regime/budget,
/api/regime/instruments, /api/regime/classify)."""
import json
import sqlite3
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


# ── Shared fixtures ─────────────────────────────────────────────────

def _seed_classification_row(db_path, instrument, features=None,
                             trading_date="2026-05-10", raw_regime="RANGING",
                             confidence=0.8):
    """Insert one regime_classification_cache row so manual classify can
    read features from it."""
    features = features or {
        "adx_14": 25.0,
        "atr_14": 5.0,
        "atr_pct": 2.5,
        "ma_200_slope_pct_per_day": 0.05,
        "range_efficiency": 0.45,
        "realized_volatility_20d": 15.0,
        "close_above_ma200": True,
        "distance_to_ma200_pct": 3.5,
    }
    payload = {
        "instrument": instrument,
        "classified_at": "2026-05-10T16:00:00+00:00",
        "trading_date": trading_date,
        "raw_regime": raw_regime,
        "confidence": confidence,
        "rationale": "seeded",
        "features": features,
        "model_version": "claude-sonnet-4-6",
        "prompt_version": "V1",
        "input_hash": f"seed_{instrument}",
    }
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS regime_classification_cache ("
        "instrument TEXT, trading_date TEXT, input_hash TEXT, "
        "classification_json TEXT, created_at TEXT, "
        "PRIMARY KEY (instrument, trading_date, input_hash))"
    )
    conn.execute(
        "INSERT OR REPLACE INTO regime_classification_cache "
        "(instrument, trading_date, input_hash, classification_json, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (instrument, trading_date, f"seed_{instrument}",
         json.dumps(payload), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def _write_config(path, symbols):
    cfg = {
        "_version": "1.0",
        "settings": {"broker": "test", "check_interval_mins": 5,
                     "portfolio_loss_limit": 1000},
        "layer1_active": [{"symbol": s, "enabled": True} for s in symbols],
        "layer2_accumulation": [],
    }
    with open(path, 'w') as f:
        json.dump(cfg, f)


def _fake_anthropic_response(regime: str, confidence: float):
    block = MagicMock()
    block.type = "tool_use"
    block.name = "classify_regime"
    block.input = {
        "regime": regime, "confidence": confidence,
        "rationale": f"synthetic {regime}", "key_features": [],
    }
    resp = MagicMock()
    resp.content = [block]
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 50
    return resp


@pytest.fixture
def api_setup(tmp_path):
    db = str(tmp_path / "regime.db")
    cfg = str(tmp_path / "instruments.json")
    _write_config(cfg, ["AAPL", "MSFT", "BARC"])
    _seed_classification_row(db, "AAPL")
    _seed_classification_row(db, "MSFT")
    _seed_classification_row(db, "BARC")

    import api_server
    api_server.REGIME_DB = db
    api_server.CONFIG_FILE = cfg
    api_server.JWT_SECRET = "classify_test_secret"
    api_server._classify_last_ts.clear()

    app = api_server.app
    app.config['TESTING'] = True
    token = api_server.create_token("tester")
    client = app.test_client()
    return client, token, db, cfg, api_server


def _hdrs(token):
    return {"Authorization": f"Bearer {token}"}


# ── Budget endpoint ─────────────────────────────────────────────────

class TestBudgetEndpoint:
    def test_requires_auth(self, api_setup):
        client, _, _, _, _ = api_setup
        resp = client.get("/api/regime/budget")
        assert resp.status_code == 401

    def test_empty_log_returns_zero_spend(self, api_setup):
        client, token, _, _, _ = api_setup
        resp = client.get("/api/regime/budget", headers=_hdrs(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["spent_today_usd"] == 0.0
        assert data["max_daily_cost_usd"] == 5.0
        assert data["remaining_usd"] == 5.0

    def test_log_rows_aggregate_correctly(self, api_setup):
        client, token, db, _, _ = api_setup
        from bot.regime.cost_tracker import CostTracker, MODEL_ID
        ct = CostTracker(db)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ct.log_classification(
            instrument="AAPL", trading_date=today, model=MODEL_ID,
            prompt_version="V1", cost_usd=0.50,
        )
        ct.log_classification(
            instrument="MSFT", trading_date=today, model=MODEL_ID,
            prompt_version="V1", cost_usd=0.25,
        )
        # Cache hit should NOT count (cost_tracker filters cache_hit=1)
        ct.log_classification(
            instrument="BARC", trading_date=today, model=MODEL_ID,
            prompt_version="V1", cost_usd=0.99, cache_hit=True,
        )
        resp = client.get("/api/regime/budget", headers=_hdrs(token))
        data = resp.get_json()
        assert abs(data["spent_today_usd"] - 0.75) < 1e-6
        assert abs(data["remaining_usd"] - 4.25) < 1e-6


# ── Instruments endpoint ────────────────────────────────────────────

class TestInstrumentsEndpoint:
    def test_requires_auth(self, api_setup):
        client, _, _, _, _ = api_setup
        resp = client.get("/api/regime/instruments")
        assert resp.status_code == 401

    def test_returns_active_symbols(self, api_setup):
        client, token, _, _, _ = api_setup
        resp = client.get("/api/regime/instruments", headers=_hdrs(token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert sorted(data["instruments"]) == ["AAPL", "BARC", "MSFT"]
        assert data["estimated_cost_per_classification_usd"] > 0


# ── Classify endpoint ───────────────────────────────────────────────

class TestClassifyEndpoint:
    def test_requires_auth(self, api_setup):
        client, _, _, _, _ = api_setup
        resp = client.post("/api/regime/classify",
                           json={"instrument": "AAPL"})
        assert resp.status_code == 401

    def test_unknown_instrument_returns_400(self, api_setup):
        client, token, _, _, _ = api_setup
        resp = client.post("/api/regime/classify",
                           json={"instrument": "ZZZZ"},
                           headers=_hdrs(token))
        assert resp.status_code == 400

    def test_missing_body_returns_400(self, api_setup):
        client, token, _, _, _ = api_setup
        resp = client.post("/api/regime/classify",
                           json={}, headers=_hdrs(token))
        assert resp.status_code == 400

    def test_single_instrument_success(self, api_setup):
        client, token, db, _, api_server = api_setup
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.messages.create.return_value = (
                    _fake_anthropic_response("TRENDING", 0.9)
                )
                mock_client_cls.return_value = mock_client
                resp = client.post(
                    "/api/regime/classify",
                    json={"instrument": "AAPL"},
                    headers=_hdrs(token),
                )

        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()
        assert data["instrument"] == "AAPL"
        assert data["raw_regime"] == "TRENDING"
        assert data["confidence"] == 0.9
        assert data["smoothed_regime"] == "TRENDING"
        assert data["cost_usd"] > 0
        assert "rationale" in data

        # Cache + smoothed store should now reflect today's date
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        from bot.regime.cache import RegimeCache
        from bot.regime.smoothing_store import SmoothedStateStore
        assert RegimeCache(db).has_for_day("AAPL", today)
        assert SmoothedStateStore(db).get_latest("AAPL").effective_regime == "TRENDING"

    def test_classify_replaces_todays_cache_row(self, api_setup):
        """Two manual classifies in a row should result in one cache row
        for today (INSERT OR REPLACE on the input_hash PK), not duplicates."""
        client, token, db, _, api_server = api_setup
        # Bump rate limit window down to 0 for this test only
        original = api_server._CLASSIFY_RATE_LIMIT_SECONDS
        api_server._CLASSIFY_RATE_LIMIT_SECONDS = 0
        try:
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
                with patch("anthropic.Anthropic") as mock_client_cls:
                    mock_client = MagicMock()
                    mock_client.messages.create.return_value = (
                        _fake_anthropic_response("RANGING", 0.85)
                    )
                    mock_client_cls.return_value = mock_client
                    client.post("/api/regime/classify",
                                json={"instrument": "AAPL"},
                                headers=_hdrs(token))
                    client.post("/api/regime/classify",
                                json={"instrument": "AAPL"},
                                headers=_hdrs(token))
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            conn = sqlite3.connect(db)
            count = conn.execute(
                "SELECT COUNT(*) FROM regime_classification_cache "
                "WHERE instrument = ? AND trading_date = ?",
                ("AAPL", today),
            ).fetchone()[0]
            conn.close()
            # input_hash is deterministic from features; both calls produce
            # the same row → exactly 1.
            assert count == 1
        finally:
            api_server._CLASSIFY_RATE_LIMIT_SECONDS = original

    def test_rate_limit_blocks_second_call(self, api_setup):
        client, token, _, _, api_server = api_setup
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.messages.create.return_value = (
                    _fake_anthropic_response("TRENDING", 0.9)
                )
                mock_client_cls.return_value = mock_client
                resp1 = client.post("/api/regime/classify",
                                    json={"instrument": "AAPL"},
                                    headers=_hdrs(token))
                assert resp1.status_code == 200

                resp2 = client.post("/api/regime/classify",
                                    json={"instrument": "AAPL"},
                                    headers=_hdrs(token))
                assert resp2.status_code == 429
                assert "Rate limited" in resp2.get_json()["error"]
                assert resp2.get_json()["retry_after_seconds"] > 0

    def test_budget_exhausted_returns_402(self, api_setup):
        client, token, db, _, _ = api_setup
        # Pre-load today's log with > $5 spend
        from bot.regime.cost_tracker import CostTracker, MODEL_ID
        ct = CostTracker(db)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ct.log_classification(
            instrument="AAPL", trading_date=today, model=MODEL_ID,
            prompt_version="V1", cost_usd=5.01,
        )

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            resp = client.post("/api/regime/classify",
                               json={"instrument": "AAPL"},
                               headers=_hdrs(token))
        assert resp.status_code == 402
        assert "budget" in resp.get_json()["error"].lower()

    def test_no_cached_features_returns_409(self, api_setup):
        client, token, db, cfg, _ = api_setup
        # Add an instrument that has no cache row
        with open(cfg) as f:
            cfg_data = json.load(f)
        cfg_data["layer1_active"].append({"symbol": "FRESH", "enabled": True})
        with open(cfg, 'w') as f:
            json.dump(cfg_data, f)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            resp = client.post("/api/regime/classify",
                               json={"instrument": "FRESH"},
                               headers=_hdrs(token))
        assert resp.status_code == 409
        assert "No cached features" in resp.get_json()["error"]

    def test_all_classifies_every_active_instrument(self, api_setup):
        client, token, _, _, _ = api_setup
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.messages.create.return_value = (
                    _fake_anthropic_response("TRENDING", 0.9)
                )
                mock_client_cls.return_value = mock_client
                resp = client.post("/api/regime/classify",
                                   json={"all": True},
                                   headers=_hdrs(token))
        assert resp.status_code == 200
        data = resp.get_json()
        symbols = sorted(r["instrument"] for r in data["results"])
        assert symbols == ["AAPL", "BARC", "MSFT"]
        for r in data["results"]:
            assert r["raw_regime"] == "TRENDING"

    def test_all_partial_result_on_failure(self, api_setup):
        """If budget runs out mid-loop, return partial_results with the
        symbol it stopped on."""
        client, token, db, _, _ = api_setup
        from bot.regime.cost_tracker import CostTracker, MODEL_ID
        ct = CostTracker(db)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Set spend just under the limit so the first call passes the
        # pre-flight budget check but the second sees the limit exceeded.
        # Mock Anthropic call costs ≈ $0.00105 (100 input + 50 output tokens)
        # so seed must leave less than that under the limit.
        ct.log_classification(
            instrument="seed", trading_date=today, model=MODEL_ID,
            prompt_version="V1", cost_usd=4.9999,
        )

        # Each successful Anthropic call adds ~$0.008 → first call pushes
        # spend over $5, second pre-flight rejects.
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.messages.create.return_value = (
                    _fake_anthropic_response("TRENDING", 0.9)
                )
                mock_client_cls.return_value = mock_client
                resp = client.post("/api/regime/classify",
                                   json={"all": True},
                                   headers=_hdrs(token))

        assert resp.status_code == 402
        body = resp.get_json()
        assert len(body["partial_results"]) == 1
        assert body["partial_results"][0]["instrument"] == "AAPL"
        assert body["stopped_on"] == "BARC"
