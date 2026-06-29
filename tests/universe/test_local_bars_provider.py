"""BARS_PROVIDER resolution: concrete broker-free ``LocalBarsSnapshotProvider`` tests.

Proves the provider is broker-free, read-only, injected-only, default-off, and fails CLOSED (returns
``None`` with a stable reason) for every bad input; that a valid local snapshot yields the bars dict
the evaluator consumes AND that the real ``compute_indicators`` the evaluator calls runs on the
returned frame; that the ``main.py`` config seam constructs it ONLY when the master flag is on and a
safe source is configured; and that importing it pulls in NO broker module and touches NO production
DB. Temporary paths only — no production database is read, created, or altered.
"""
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

import pytest

import main
from bot.universe.local_bars_provider import (
    BAR_FUTURE_TIMESTAMP, BAR_PROOF_MISSING, BARS_MALFORMED, BARS_MISSING,
    INSTRUMENT_MISMATCH, PROVIDER_ERROR, SNAPSHOT_MALFORMED, SNAPSHOT_SCHEMA_UNSUPPORTED,
    SOURCE_PATH_MISSING, SOURCE_PATH_NOT_FOUND, SOURCE_PATH_UNREADABLE, SOURCE_PATH_UNSAFE,
    TIMEFRAME_MISMATCH, TRADING_DATE_MISMATCH, LocalBarsSnapshotProvider,
    build_local_bars_provider, validate_source_path)
from tests.universe._fixtures import OFF, ON, flat, inst, write_configs

REPO = pathlib.Path(__file__).resolve().parents[2]

IUID = "instr-aapl-0001"
LUID = "listing-aapl-xnas"
TD = "2026-01-05"
TF = "1d"
# A fixed clock AFTER the snapshot session so a complete bar is never judged "future".
NOW = lambda: datetime(2026, 6, 1, tzinfo=timezone.utc)


def _rec(iuid=IUID, luid=LUID):
    return {"instrument_uid": iuid, "listing_uid": luid, "canonical_instrument_id": iuid}


def _ohlcv(n=250, slope=1.0, volume=2_000_000.0, with_date=False):
    """Deterministic uptrend OHLCV as a JSON-serializable list of dicts (slope>0 → indicators
    defined and a breakout entry fires, mirroring _fixtures.make_bars)."""
    out = []
    for i in range(n):
        c = 100.0 + slope * i
        bar = {"open": c - 0.3, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": volume}
        if with_date:
            bar["date"] = f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}"
        out.append(bar)
    return out


def _bars_rec(**over):
    d = {
        "schema_version": 1, "canonical_instrument_id": IUID,
        "instrument_uid": IUID, "listing_uid": LUID,
        "trading_date": TD, "timeframe": TF, "bar_end_time": f"{TD}T21:00:00Z",
        "source": "local_snapshot:test", "version": "v1", "content_hash": "abc123",
        "generated_at": "2026-01-06T00:00:00Z", "corp_action_status": "ok", "sector": "Tech",
        "bars": _ohlcv(),
    }
    d.update(over)
    return d


def _write(tmp_path, obj, name="bars.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return str(p)


def _provider(tmp_path, obj, name="bars.json", now_fn=NOW, timeframe=TF):
    return LocalBarsSnapshotProvider(_write(tmp_path, obj, name), timeframe=timeframe, now_fn=now_fn)


# ── path-safety (pure validator + factory) ───────────────────────────────────────────────
def test_validate_source_path_missing():
    assert validate_source_path(None) == SOURCE_PATH_MISSING
    assert validate_source_path("") == SOURCE_PATH_MISSING
    assert validate_source_path("   ") == SOURCE_PATH_MISSING


@pytest.mark.parametrize("name", [
    "positions.db", "regime.db", "backtest.db", "universe.db", "universe_shadow.db",
    "tradingbot.db", "orders.db", "executions.db", "fills.db"])
def test_validate_source_path_production_db_unsafe(name):
    # universe_shadow.db is NOT in PRODUCTION_DB_BASENAMES (it is the intended shadow DB), so it is
    # path-safe as a snapshot SOURCE too; assert only the genuine production basenames are unsafe.
    expected = None if name == "universe_shadow.db" else SOURCE_PATH_UNSAFE
    assert validate_source_path(f"/root/trading/{name}") == expected


def test_validate_source_path_safe_returns_none(tmp_path):
    assert validate_source_path(str(tmp_path / "shadow_bars_snapshots")) is None


def test_source_symlink_to_production_db_is_unsafe(tmp_path):
    # Symlink points at a TEMP file named backtest.db — never the real production DB.
    fake_prod = tmp_path / "backtest.db"
    fake_prod.write_text("{}")
    link = tmp_path / "bars_link.json"
    os.symlink(str(fake_prod), str(link))
    assert validate_source_path(str(link)) == SOURCE_PATH_UNSAFE


def test_factory_missing_source_returns_none():
    assert build_local_bars_provider(None) is None
    assert build_local_bars_provider("") is None


def test_factory_unsafe_source_returns_none():
    assert build_local_bars_provider("/root/trading/backtest.db") is None


def test_factory_valid_source_constructs(tmp_path):
    prov = build_local_bars_provider(_write(tmp_path, [_bars_rec()]))
    assert isinstance(prov, LocalBarsSnapshotProvider)
    assert prov.is_live is False


# ── fail-closed resolution (every bad input → None + stable reason) ──────────────────────
def test_missing_snapshot_file_fails_closed(tmp_path):
    prov = LocalBarsSnapshotProvider(str(tmp_path / "nope.json"), timeframe=TF, now_fn=NOW)
    res = prov.resolve(_rec())
    assert prov(_rec()) is None
    assert res.reason == SOURCE_PATH_NOT_FOUND


def test_malformed_snapshot_fails_closed(tmp_path):
    p = tmp_path / "bars.json"
    p.write_text("{not json")
    prov = LocalBarsSnapshotProvider(str(p), timeframe=TF, now_fn=NOW)
    assert prov(_rec()) is None
    assert prov.resolve(_rec()).reason == SNAPSHOT_MALFORMED


def test_unsupported_schema_fails_closed(tmp_path):
    prov = _provider(tmp_path, [_bars_rec(schema_version=99)])
    assert prov(_rec()) is None
    assert prov.resolve(_rec()).reason == SNAPSHOT_SCHEMA_UNSUPPORTED


def test_identity_mismatch_fails_closed(tmp_path):
    prov = _provider(tmp_path, [_bars_rec()])
    assert prov(_rec(iuid="other", luid="other")) is None
    assert prov.resolve(_rec(iuid="other", luid="other")).reason == INSTRUMENT_MISMATCH


def test_matches_on_canonical_instrument_id_only(tmp_path):
    # The REAL evaluator record carries ONLY canonical_instrument_id (canonical_instruments table
    # has no instrument_uid/listing_uid columns). Matching must succeed on cid alone.
    prov = _provider(tmp_path, [_bars_rec()])
    assert prov({"canonical_instrument_id": IUID}) is not None
    # …and a cid-only record with a non-matching cid still fails closed.
    assert prov({"canonical_instrument_id": "nope"}) is None


def test_timeframe_mismatch_fails_closed(tmp_path):
    prov = _provider(tmp_path, [_bars_rec(timeframe="5m")])
    assert prov(_rec()) is None
    assert prov.resolve(_rec()).reason == TIMEFRAME_MISMATCH


def test_trading_date_inconsistent_fails_closed(tmp_path):
    # declared trading_date != bar_end_time date → fail closed (spec: "trading_date mismatch").
    prov = _provider(tmp_path, [_bars_rec(bar_end_time="2026-01-06T21:00:00Z")])
    assert prov(_rec()) is None
    assert prov.resolve(_rec()).reason == TRADING_DATE_MISMATCH


def test_bar_end_time_future_fails_closed(tmp_path):
    future = "2027-01-05"
    prov = _provider(tmp_path, [_bars_rec(trading_date=future,
                                          bar_end_time=f"{future}T21:00:00Z")])
    assert prov(_rec()) is None
    assert prov.resolve(_rec()).reason == BAR_FUTURE_TIMESTAMP


@pytest.mark.parametrize("over", [
    {"source": None},
    {"bar_end_time": None},
    {"trading_date": None},
    {"version": None, "content_hash": None},  # BOTH absent → proof unsatisfied
])
def test_proof_missing_fails_closed(tmp_path, over):
    prov = _provider(tmp_path, [_bars_rec(**over)])
    assert prov(_rec()) is None
    assert prov.resolve(_rec()).reason == BAR_PROOF_MISSING


def test_either_version_or_hash_proves(tmp_path):
    # content_hash present, version absent → still valid (proof satisfied by either).
    assert _provider(tmp_path, [_bars_rec(version=None)])(_rec()) is not None
    # version present, content_hash absent → also valid.
    assert _provider(tmp_path, [_bars_rec(content_hash=None)])(_rec()) is not None


def test_bars_missing_fails_closed(tmp_path):
    assert _provider(tmp_path, [_bars_rec(bars=[])]).resolve(_rec()).reason == BARS_MISSING
    assert _provider(tmp_path, [_bars_rec(bars=None)]).resolve(_rec()).reason == BARS_MISSING


def test_bars_malformed_fails_closed(tmp_path):
    # a bar missing 'volume'
    bad = [{"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}]
    assert _provider(tmp_path, [_bars_rec(bars=bad)]).resolve(_rec()).reason == BARS_MALFORMED
    # a non-numeric close
    bad2 = [{"open": 1.0, "high": 2.0, "low": 0.5, "close": "x", "volume": 10}]
    assert _provider(tmp_path, [_bars_rec(bars=bad2)]).resolve(_rec()).reason == BARS_MALFORMED
    # a boolean (subclass of int) is not a valid price → rejected
    bad3 = [{"open": True, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10}]
    assert _provider(tmp_path, [_bars_rec(bars=bad3)]).resolve(_rec()).reason == BARS_MALFORMED


def test_provider_never_raises_returns_provider_error(tmp_path, monkeypatch):
    prov = _provider(tmp_path, [_bars_rec()])
    monkeypatch.setattr(prov, "_load_records", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert prov(_rec()) is None
    assert prov.resolve(_rec()).reason == PROVIDER_ERROR


# ── valid path: payload shape + the REAL consumer runs ───────────────────────────────────
def test_valid_snapshot_returns_payload(tmp_path):
    prov = _provider(tmp_path, [_bars_rec()])
    payload = prov(_rec())
    assert payload is not None
    assert payload["corp_action_status"] == "ok"
    assert payload["sector"] == "Tech"
    assert payload["fresh_bar"] is True
    assert payload["source"] == "local_snapshot:test"
    import pandas as pd
    assert isinstance(payload["bars"], pd.DataFrame)
    assert set(["open", "high", "low", "close", "volume"]).issubset(payload["bars"].columns)


def test_valid_snapshot_feeds_real_compute_indicators(tmp_path):
    # The discriminating check: the evaluator does compute_indicators(payload["bars"]) then reads
    # the last row + close*volume. Prove the returned frame actually works there.
    from backtest.breakout_strategy import compute_indicators
    payload = _provider(tmp_path, [_bars_rec()])(_rec())
    ind = compute_indicators(payload["bars"])
    last = ind.iloc[-1]
    for col in ["sma50", "sma200", "atr14", "adx14", "high20_excl"]:
        assert last[col] == last[col]  # not NaN — indicators are defined on a 250-bar uptrend
    assert bool(last["entry_signal"]) is True
    _ = float((ind["close"] * ind["volume"]).tail(20).mean())  # ADV20 computable


def test_fresh_bar_strict_bool_rejects_string(tmp_path):
    # P3-2: a string "false" must NOT be coerced truthy; non-bool falls back to the default (True).
    prov = _provider(tmp_path, [_bars_rec(fresh_bar="false")])
    assert prov(_rec())["fresh_bar"] is True  # falls back to default, never the truthy string
    prov2 = _provider(tmp_path, [_bars_rec(fresh_bar=False)])
    assert prov2(_rec())["fresh_bar"] is False


def test_directory_source_and_child_revalidation(tmp_path):
    d = tmp_path / "bars_dir"
    d.mkdir()
    (d / "a.json").write_text(json.dumps([_bars_rec()]))
    prov = LocalBarsSnapshotProvider(str(d), timeframe=TF, now_fn=NOW)
    assert prov(_rec()) is not None
    # P3-1: a .json child that is a SYMLINK resolving to a production DB basename must fail closed
    # even with a safe parent dir (the parent-only check would miss it). Target is a TEMP file.
    fake_prod = tmp_path / "backtest.db"
    fake_prod.write_text("{}")
    os.symlink(str(fake_prod), str(d / "evil.json"))
    assert prov(_rec()) is None
    assert prov.resolve(_rec()).reason == SOURCE_PATH_UNSAFE


def test_most_recent_session_selected(tmp_path):
    older = _bars_rec(trading_date="2026-01-02", bar_end_time="2026-01-02T21:00:00Z")
    newer = _bars_rec(trading_date=TD, bar_end_time=f"{TD}T21:00:00Z", source="newer")
    prov = _provider(tmp_path, [older, newer])
    assert prov(_rec())["trading_date"] == TD


def test_foreign_completed_bar_record_ignored(tmp_path):
    # A completed-bar availability row (no 'bars' array) sharing the source must be ignored, not
    # mis-parsed — the bars provider only consumes bars-shaped records.
    foreign = {"schema_version": 1, "instrument_uid": IUID, "listing_uid": LUID,
               "trading_date": TD, "timeframe": TF, "bar_end_time": f"{TD}T21:00:00Z",
               "available": True, "source": "cbp", "version": "v1"}
    assert _provider(tmp_path, [foreign]).resolve(_rec()).reason == INSTRUMENT_MISMATCH


# ── config seam (main.py) — flag-gated, fail-closed, no live default ─────────────────────
def test_seam_flag_off_no_construction(tmp_path):
    # OFF → None and (proven) NO bot.universe import is required to decide that.
    assert main.build_bars_provider_from_config(
        OFF, {"bars_snapshot_source": _write(tmp_path, [_bars_rec()])}) is None


def test_seam_flag_on_no_source_fails_closed():
    assert main.build_bars_provider_from_config(ON, {}) is None
    assert main.build_bars_provider_from_config(ON, {"bars_snapshot_source": ""}) is None


def test_seam_flag_on_unsafe_source_fails_closed():
    assert main.build_bars_provider_from_config(
        ON, {"bars_snapshot_source": "/root/trading/backtest.db"}) is None


def test_seam_flag_on_valid_source_constructs(tmp_path):
    prov = main.build_bars_provider_from_config(
        ON, {"bars_snapshot_source": _write(tmp_path, [_bars_rec()])})
    assert isinstance(prov, LocalBarsSnapshotProvider)
    assert prov.is_live is False


# ── broker-free + production-DB no-touch (static + import-isolation) ─────────────────────
def test_module_source_has_no_broker_or_indicator_tokens():
    src = (REPO / "bot" / "universe" / "local_bars_provider.py").read_text()
    # Module names are checked against IMPORT lines only (the docstring legitimately *names* these
    # modules to document what it must NOT import); call patterns are checked against full source.
    import_lines = "\n".join(l for l in src.splitlines()
                             if l.strip().startswith(("import ", "from ")))
    for tok in ["bot.brokers", "ib_insync", "trading_ig", "bot.connection",
                "backtest.breakout_strategy"]:
        assert tok not in import_lines, f"unexpected import in provider: {tok}"
    for tok in [".fetch_bars(", "place_order", "submit_order"]:
        assert tok not in src, f"unexpected broker call token in provider: {tok}"


def test_import_pulls_in_no_broker_modules():
    # Fresh interpreter: importing the provider must not import any broker/live-data module.
    code = (
        "import sys; import bot.universe.local_bars_provider as m;"
        "bad=[n for n in sys.modules if any(t in n for t in"
        "('ib_insync','trading_ig','bot.brokers','bot.connection')) ];"
        "print('BAD:'+','.join(bad))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "BAD:", out.stdout  # no broker/live-data module imported


# ── decisive integration: the REAL ShadowEvaluator consumes the provider ─────────────────
def test_real_evaluator_consumes_provider_over_seeded_registry(tmp_path):
    """Drive a registry-produced record through the real ShadowEvaluator (as test_evaluator does
    with SpyProvider). This empirically resolves the identity-field question: if the provider
    matched the wrong field, the real record would never match and price would be None."""
    from bot.universe.registry import Registry
    from bot.universe.seed import canonical_id, seed_registry
    from bot.universe.evaluator import ShadowEvaluator

    p1, p2 = write_configs(tmp_path, [inst("AAPL")], [])
    db = str(tmp_path / "universe.db")
    seed_registry(db, p1, p2)
    reg = Registry(db)
    cid = canonical_id("AAPL", "USD", "NASDAQ")  # "US_AAPL"
    rec = next(r for r in reg.all_canonical() if r["canonical_instrument_id"] == cid)
    assert rec.get("instrument_uid") is None  # confirms the real record has NO uid identity

    run_day = "2026-06-10"
    snap = [_bars_rec(canonical_instrument_id=cid, instrument_uid=None, listing_uid=None,
                      trading_date=run_day, bar_end_time=f"{run_day}T21:00:00Z")]
    prov = build_local_bars_provider(
        _write(tmp_path, snap), now_fn=lambda: datetime(2026, 6, 15, tzinfo=timezone.utc))
    # (1) direct: the provider matches a REAL registry record.
    assert prov(rec) is not None, "provider failed to match a real registry record"
    # (2) full: the evaluator evaluates the instrument and bars flowed through (price computed).
    ev = ShadowEvaluator(reg, prov, ON, equity=100_000, position_provider=flat())
    r = ev.maybe_run(run_day)
    assert r["evaluated"] >= 1
    out = next(o for o in r["outcomes"] if o.get("canonical_instrument_id") == cid)
    assert out.get("price") is not None  # bars → compute_indicators → USD-normalised price


def test_no_production_db_created_or_touched(tmp_path):
    # Resolving against a temp snapshot creates/opens NO production DB at the repo root.
    before = {p.name for p in REPO.glob("*.db")}
    _provider(tmp_path, [_bars_rec()])(_rec())
    after = {p.name for p in REPO.glob("*.db")}
    assert before == after
    assert not (REPO / "universe.db").exists()
    assert not (REPO / "universe_shadow.db").exists()
