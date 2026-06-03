"""Tests for the regime-filter performance aggregator (Commit 3
of the regime-filter experiment).

Pure aggregation tests + endpoint tests.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

os.environ.setdefault("JWT_SECRET", "test-secret-for-filter-perf")

from bot.regime.filter_performance import aggregate, _safe_pct_return
import api_server  # noqa: E402
import jwt as _jwt  # noqa: E402


def _live(entry: float, exit_: float, pnl_usd: float = 0.0) -> dict:
    return {"entry_price": entry, "exit_price": exit_, "pnl_usd": pnl_usd}


def _blocked(regime: str, shadow_id: str | None = None) -> dict:
    return {"smoothed_regime": regime, "shadow_trade_id": shadow_id}


def _shadow(sid: str, status: str, entry: float = 100.0,
            exit_: float = 105.0, pnl: float = 0.0,
            pnl_pct: float | None = None) -> dict:
    return {"id": sid, "status": status, "entry_price": entry,
            "exit_price": exit_, "pnl": pnl, "pnl_pct": pnl_pct}


# ── _safe_pct_return ────────────────────────────────────────

class TestSafePctReturn:
    def test_typical(self):
        assert _safe_pct_return(100.0, 110.0) == pytest.approx(10.0)

    def test_loss(self):
        assert _safe_pct_return(100.0, 95.0) == pytest.approx(-5.0)

    def test_zero_entry_returns_none(self):
        assert _safe_pct_return(0, 1) is None

    def test_none_returns_none(self):
        assert _safe_pct_return(None, 100) is None
        assert _safe_pct_return(100, None) is None


# ── Aggregate, base cases ───────────────────────────────────

class TestAggregateBase:
    def test_empty_everything(self):
        out = aggregate([], [], [])
        assert out["live_trades"]["n"] == 0
        assert out["live_trades"]["mean_pnl_pct"] is None
        assert out["blocked_entries"]["n_total"] == 0
        assert out["comparison"]["net_effect_pct_per_trade"] is None
        assert "insufficient data" in out["comparison"]["verdict"]

    def test_only_live_trades_no_comparison(self):
        out = aggregate(
            [_live(100, 110), _live(100, 95)], [], []
        )
        assert out["live_trades"]["n"] == 2
        assert out["live_trades"]["mean_pnl_pct"] == pytest.approx(2.5)
        assert out["live_trades"]["win_rate"] == 0.5
        assert "no closed shadow" in out["comparison"]["verdict"]

    def test_only_blocked_no_live(self):
        out = aggregate(
            [],
            [_blocked("UNCLEAR", "x")],
            [_shadow("x", "CLOSED", pnl_pct=-2.0)],
        )
        assert out["blocked_entries"]["n_total"] == 1
        assert out["blocked_entries"]["n_closed_shadows"] == 1
        assert "no live trades" in out["comparison"]["verdict"]


# ── Aggregate, comparison verdict ───────────────────────────

class TestAggregateComparison:
    def test_filter_helped_when_live_outperforms_blocked(self):
        out = aggregate(
            live_trades=[_live(100, 110), _live(100, 108)],  # +9% mean
            blocked_entries=[_blocked("UNCLEAR", "a"),
                             _blocked("UNCLEAR", "b")],
            shadow_trades=[
                _shadow("a", "CLOSED", pnl_pct=-3.0),
                _shadow("b", "CLOSED", pnl_pct=-1.0),  # -2% mean
            ],
        )
        c = out["comparison"]
        assert c["mean_live_pnl_pct"] == pytest.approx(9.0)
        assert c["mean_blocked_shadow_pnl_pct"] == pytest.approx(-2.0)
        assert c["net_effect_pct_per_trade"] == pytest.approx(11.0)
        assert "filter HELPED" in c["verdict"]

    def test_filter_hurt_when_live_underperforms_blocked(self):
        out = aggregate(
            live_trades=[_live(100, 100.5), _live(100, 100.5)],
            blocked_entries=[_blocked("UNCLEAR", "a")],
            shadow_trades=[_shadow("a", "CLOSED", pnl_pct=8.0)],
        )
        assert "filter HURT" in out["comparison"]["verdict"]
        assert out["comparison"]["net_effect_pct_per_trade"] < 0

    def test_roughly_neutral(self):
        out = aggregate(
            live_trades=[_live(100, 102)],
            blocked_entries=[_blocked("UNCLEAR", "a")],
            shadow_trades=[_shadow("a", "CLOSED", pnl_pct=2.05)],
        )
        assert "roughly neutral" in out["comparison"]["verdict"]


# ── Aggregate, regime split ─────────────────────────────────

class TestRegimeSplit:
    def test_blocked_grouped_by_regime(self):
        out = aggregate(
            live_trades=[],
            blocked_entries=[
                _blocked("UNCLEAR", "a"),
                _blocked("UNCLEAR", "b"),
                _blocked("RANGING", "c"),
            ],
            shadow_trades=[
                _shadow("a", "CLOSED", pnl_pct=-1.0),
                _shadow("b", "CLOSED", pnl_pct=-2.0),
                _shadow("c", "CLOSED", pnl_pct=+1.0),
            ],
        )
        by_regime = out["blocked_entries"]["by_regime"]
        assert by_regime["UNCLEAR"]["n"] == 2
        assert by_regime["UNCLEAR"]["mean_pnl_pct"] == pytest.approx(-1.5)
        assert by_regime["RANGING"]["n"] == 1
        assert by_regime["RANGING"]["mean_pnl_pct"] == pytest.approx(1.0)
        # 'unknown' bucket present even when empty (stable keys)
        assert by_regime["unknown"]["n"] == 0

    def test_null_regime_becomes_unknown(self):
        out = aggregate(
            live_trades=[],
            blocked_entries=[_blocked(None, "a")],
            shadow_trades=[_shadow("a", "CLOSED", pnl_pct=0.5)],
        )
        assert out["blocked_entries"]["by_regime"]["unknown"]["n"] == 1


# ── Aggregate, open / missing shadow trades ────────────────

class TestOpenAndMissing:
    def test_open_shadow_counted_separately(self):
        out = aggregate(
            live_trades=[],
            blocked_entries=[_blocked("UNCLEAR", "a")],
            shadow_trades=[_shadow("a", "OPEN")],
        )
        assert out["blocked_entries"]["n_open_shadows"] == 1
        assert out["blocked_entries"]["n_closed_shadows"] == 0
        # No closed shadow → no comparison material
        assert "no closed shadow" in out["comparison"]["verdict"] \
            or "insufficient data" in out["comparison"]["verdict"]

    def test_blocked_without_shadow_id(self):
        out = aggregate(
            live_trades=[],
            blocked_entries=[_blocked("UNCLEAR", None)],
            shadow_trades=[],
        )
        assert out["blocked_entries"]["n_with_no_shadow"] == 1
        assert out["blocked_entries"]["n_closed_shadows"] == 0

    def test_blocked_with_dangling_shadow_id(self):
        # shadow_trade_id present but no matching row in shadow_trades
        out = aggregate(
            live_trades=[],
            blocked_entries=[_blocked("UNCLEAR", "ghost")],
            shadow_trades=[],
        )
        assert out["blocked_entries"]["n_with_no_shadow"] == 1


# ── Endpoint tests ─────────────────────────────────────────

def _auth_header():
    return {"Authorization":
            f"Bearer {_jwt.encode({'sub':'t'}, api_server.JWT_SECRET, algorithm='HS256')}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    regime_db = tmp_path / "regime.db"
    learning_db = tmp_path / "learning_loop.db"

    with sqlite3.connect(str(regime_db)) as conn:
        conn.execute("""CREATE TABLE regime_blocked_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, instrument TEXT NOT NULL,
            signal_type TEXT NOT NULL, signal_confidence TEXT,
            smoothed_regime TEXT, classifier_rationale TEXT,
            would_have_entry_price REAL, bar_time TEXT,
            shadow_trade_id TEXT)""")
        conn.execute("""CREATE TABLE shadow_hypothetical_trades (
            id TEXT PRIMARY KEY, instrument TEXT NOT NULL,
            opened_at TEXT NOT NULL, opened_bar TEXT NOT NULL,
            entry_engine TEXT NOT NULL, entry_regime TEXT,
            entry_price REAL NOT NULL, entry_quantity REAL NOT NULL,
            entry_stop REAL, closed_at TEXT, closed_bar TEXT,
            exit_price REAL, exit_reason TEXT, pnl REAL, pnl_pct REAL,
            status TEXT NOT NULL)""")
    with sqlite3.connect(str(learning_db)) as conn:
        conn.execute("""CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, symbol TEXT, name TEXT, action TEXT,
            entry_price REAL, exit_price REAL, qty REAL,
            pnl_usd REAL, hold_days INTEGER, outcome TEXT,
            open INTEGER DEFAULT 1, currency TEXT DEFAULT 'USD')""")
    monkeypatch.setattr(api_server, "REGIME_DB", str(regime_db))
    monkeypatch.setattr(api_server, "LEARNING_DB", str(learning_db))
    api_server.app.config["TESTING"] = True
    return api_server.app.test_client(), regime_db, learning_db


class TestFilterPerformanceEndpoint:
    def test_empty_dbs_returns_200_with_insufficient_data(self, client):
        c, _, _ = client
        resp = c.get("/api/regime/filter_performance",
                     headers=_auth_header())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["live_trades"]["n"] == 0
        assert body["blocked_entries"]["n_total"] == 0
        assert "insufficient" in body["comparison"]["verdict"]

    def test_unauthenticated_returns_401(self, client):
        c, _, _ = client
        resp = c.get("/api/regime/filter_performance")
        assert resp.status_code == 401

    def test_blocked_with_closed_shadow_renders_comparison(self, client):
        c, regime_db, learning_db = client
        # Seed: 1 blocked → 1 closed shadow with +2% pct
        # plus 1 live trade with +5%
        with sqlite3.connect(str(regime_db)) as conn:
            conn.execute(
                "INSERT INTO regime_blocked_entries "
                "(ts, instrument, signal_type, smoothed_regime, "
                "shadow_trade_id) "
                "VALUES (?,?,?,?,?)",
                ("2026-05-25T10:00:00", "AAPL", "BUY", "UNCLEAR", "sh1"))
            conn.execute(
                "INSERT INTO shadow_hypothetical_trades "
                "(id, instrument, opened_at, opened_bar, entry_engine, "
                "entry_price, entry_quantity, status, pnl_pct) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                ("sh1", "AAPL", "t", "t", "would_have_taken",
                 100.0, 10.0, "CLOSED", 2.0))
        with sqlite3.connect(str(learning_db)) as conn:
            conn.execute(
                "INSERT INTO trades (timestamp, symbol, action, "
                "entry_price, exit_price, qty, pnl_usd, open) "
                "VALUES (?,?,?,?,?,?,?,?)",
                ("2026-05-26T10:00:00", "MSFT", "BOUGHT",
                 100.0, 105.0, 1.0, 5.0, 0))

        resp = c.get("/api/regime/filter_performance",
                     headers=_auth_header())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["live_trades"]["n"] == 1
        assert body["blocked_entries"]["n_closed_shadows"] == 1
        assert body["comparison"]["mean_live_pnl_pct"] == pytest.approx(5.0)
        assert body["comparison"]["mean_blocked_shadow_pnl_pct"] \
            == pytest.approx(2.0)
        assert "filter HELPED" in body["comparison"]["verdict"]

    def test_since_param_narrows_window(self, client):
        c, regime_db, learning_db = client
        with sqlite3.connect(str(learning_db)) as conn:
            conn.executemany(
                "INSERT INTO trades (timestamp, symbol, action, "
                "entry_price, exit_price, qty, pnl_usd, open) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [
                    ("2026-01-01T00:00:00", "OLD", "BOUGHT",
                     100.0, 110.0, 1.0, 10.0, 0),
                    ("2026-05-26T00:00:00", "NEW", "BOUGHT",
                     100.0, 105.0, 1.0, 5.0, 0),
                ],
            )
        resp = c.get(
            "/api/regime/filter_performance?since=2026-05-01",
            headers=_auth_header(),
        )
        body = resp.get_json()
        assert body["live_trades"]["n"] == 1
        assert body["live_trades"]["mean_pnl_pct"] == pytest.approx(5.0)
        assert body["since"] == "2026-05-01"
