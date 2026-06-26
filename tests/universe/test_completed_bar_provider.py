"""BLOCKER-W1 resolution: concrete broker-free ``LocalCompletedBarSnapshotProvider`` tests.

Proves the provider is broker-free, read-only, injected-only, default-off, and fails CLOSED with a
stable reason for every bad input; that a valid local snapshot yields an available completed bar
(both directly AND through the real ``safe_completed_bar`` / ``as_bar_available_fn`` boundary the
scheduler uses); that the ``main.py`` config seam constructs it ONLY when the master flag is on and
a safe source is configured; and that importing it pulls in NO broker module and touches NO
production DB. Temporary paths only — no production database is read, created, or altered.
"""
import builtins
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

import pytest

import main
from bot.universe.bar_provider import (CompletedBarSnapshot, as_bar_available_fn,
                                        safe_completed_bar)
from bot.universe.local_bar_provider import (
    BAR_FUTURE_TIMESTAMP, BAR_NOT_COMPLETED, BAR_PROOF_MISSING, BAR_UNAVAILABLE,
    INSTRUMENT_MISMATCH, PROVIDER_ERROR, SNAPSHOT_MALFORMED, SNAPSHOT_SCHEMA_UNSUPPORTED,
    SOURCE_PATH_MISSING, SOURCE_PATH_NOT_FOUND, SOURCE_PATH_UNREADABLE, SOURCE_PATH_UNSAFE,
    TIMEFRAME_MISMATCH, TRADING_DATE_MISMATCH, LocalCompletedBarSnapshotProvider,
    build_local_completed_bar_provider, validate_source_path)
from tests.universe._fixtures import OFF, ON

REPO = pathlib.Path(__file__).resolve().parents[2]

IUID = "instr-aapl-0001"
LUID = "listing-aapl-xnas"
TD = "2026-01-05"
TF = "1d"
# A fixed clock AFTER the snapshot session so a complete bar is never judged "future".
NOW = lambda: datetime(2026, 6, 1, tzinfo=timezone.utc)


def _rec(iuid=IUID, luid=LUID):
    return {"instrument_uid": iuid, "listing_uid": luid, "canonical_instrument_id": iuid}


def _bar(**over):
    d = {
        "schema_version": 1, "instrument_uid": IUID, "listing_uid": LUID,
        "trading_date": TD, "timeframe": TF, "bar_end_time": f"{TD}T21:00:00Z",
        "available": True, "source": "local_snapshot:test", "version": "v1",
        "content_hash": "abc123", "generated_at": "2026-01-06T00:00:00Z",
    }
    d.update(over)
    return d


def _write(tmp_path, obj, name="snap.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return str(p)


def _provider(tmp_path, obj, name="snap.json", now_fn=NOW):
    return LocalCompletedBarSnapshotProvider(_write(tmp_path, obj, name), now_fn=now_fn)


# ── path-safety (pure validator + factory) ───────────────────────────────────────────────
def test_validate_source_path_missing():
    assert validate_source_path(None) == SOURCE_PATH_MISSING
    assert validate_source_path("") == SOURCE_PATH_MISSING
    assert validate_source_path("   ") == SOURCE_PATH_MISSING


@pytest.mark.parametrize("name", [
    "positions.db", "regime.db", "backtest.db", "universe.db",
    "tradingbot.db", "orders.db", "executions.db", "fills.db"])
def test_validate_source_path_production_db_unsafe(name):
    assert validate_source_path(f"/root/trading/{name}") == SOURCE_PATH_UNSAFE


def test_validate_source_path_safe_returns_none(tmp_path):
    assert validate_source_path(str(tmp_path / "shadow_bar_snapshots")) is None


def test_source_symlink_to_production_db_is_unsafe(tmp_path):
    # Symlink points at a TEMP file named backtest.db — never the real production DB.
    fake_prod = tmp_path / "backtest.db"
    fake_prod.write_text("{}")
    link = tmp_path / "snap_link.json"
    os.symlink(str(fake_prod), str(link))
    assert validate_source_path(str(link)) == SOURCE_PATH_UNSAFE
    assert build_local_completed_bar_provider(str(link)) is None


def test_factory_missing_source_returns_none():
    assert build_local_completed_bar_provider(None) is None
    assert build_local_completed_bar_provider("") is None


def test_factory_unsafe_source_returns_none():
    assert build_local_completed_bar_provider("/root/trading/backtest.db") is None


def test_factory_valid_source_constructs_provider(tmp_path):
    prov = build_local_completed_bar_provider(_write(tmp_path, [_bar()]))
    assert isinstance(prov, LocalCompletedBarSnapshotProvider)
    assert prov.is_live is False


# ── load-time fail-closed ────────────────────────────────────────────────────────────────
def test_missing_snapshot_file_fails_closed(tmp_path):
    prov = LocalCompletedBarSnapshotProvider(str(tmp_path / "nope.json"), now_fn=NOW)
    snap = prov.completed_bar(record=_rec(), trading_date=TD, timeframe=TF)
    assert snap.available is False and snap.reason == SOURCE_PATH_NOT_FOUND


def test_unreadable_snapshot_fails_closed(tmp_path):
    p = tmp_path / "snap.json"
    p.write_text(json.dumps([_bar()]))
    os.chmod(str(p), 0)
    prov = LocalCompletedBarSnapshotProvider(str(p), now_fn=NOW)
    try:
        snap = prov.completed_bar(record=_rec(), trading_date=TD, timeframe=TF)
    finally:
        os.chmod(str(p), 0o644)
    if os.geteuid() == 0:
        pytest.skip("running as root bypasses chmod 0 read protection")
    assert snap.available is False and snap.reason == SOURCE_PATH_UNREADABLE


def test_malformed_snapshot_fails_closed(tmp_path):
    p = tmp_path / "snap.json"
    p.write_text("{not valid json,,,")
    prov = LocalCompletedBarSnapshotProvider(str(p), now_fn=NOW)
    snap = prov.completed_bar(record=_rec(), trading_date=TD, timeframe=TF)
    assert snap.available is False and snap.reason == SNAPSHOT_MALFORMED


def test_unsupported_schema_version_fails_closed(tmp_path):
    prov = _provider(tmp_path, [_bar(schema_version=999)])
    snap = prov.completed_bar(record=_rec(), trading_date=TD, timeframe=TF)
    assert snap.available is False and snap.reason == SNAPSHOT_SCHEMA_UNSUPPORTED


# ── match-time fail-closed ───────────────────────────────────────────────────────────────
def test_identity_mismatch_fails_closed(tmp_path):
    prov = _provider(tmp_path, [_bar()])
    snap = prov.completed_bar(record=_rec(iuid="other-uid", luid="other-listing"),
                              trading_date=TD, timeframe=TF)
    assert snap.available is False and snap.reason == INSTRUMENT_MISMATCH


def test_trading_date_mismatch_fails_closed(tmp_path):
    prov = _provider(tmp_path, [_bar()])
    snap = prov.completed_bar(record=_rec(), trading_date="2026-02-02", timeframe=TF)
    assert snap.available is False and snap.reason == TRADING_DATE_MISMATCH


def test_timeframe_mismatch_fails_closed(tmp_path):
    prov = _provider(tmp_path, [_bar()])
    snap = prov.completed_bar(record=_rec(), trading_date=TD, timeframe="1h")
    assert snap.available is False and snap.reason == TIMEFRAME_MISMATCH


def test_bar_unavailable_fails_closed(tmp_path):
    prov = _provider(tmp_path, [_bar(available=False)])
    snap = prov.completed_bar(record=_rec(), trading_date=TD, timeframe=TF)
    assert snap.available is False and snap.reason == BAR_UNAVAILABLE


def test_proof_missing_fails_closed(tmp_path):
    prov = _provider(tmp_path, [_bar(source=None, version=None, content_hash=None)])
    snap = prov.completed_bar(record=_rec(), trading_date=TD, timeframe=TF)
    assert snap.available is False and snap.reason == BAR_PROOF_MISSING


def test_bar_not_completed_fails_closed(tmp_path):
    # bar_end_time on a DIFFERENT (earlier) day than the requested completed session.
    prov = _provider(tmp_path, [_bar(bar_end_time="2026-01-04T21:00:00Z")])
    snap = prov.completed_bar(record=_rec(), trading_date=TD, timeframe=TF)
    assert snap.available is False and snap.reason == BAR_NOT_COMPLETED


def test_bar_future_timestamp_fails_closed(tmp_path):
    fut = "2999-01-01"
    prov = _provider(tmp_path, [_bar(trading_date=fut, bar_end_time=f"{fut}T21:00:00Z")],
                     now_fn=NOW)
    snap = prov.completed_bar(record=_rec(), trading_date=fut, timeframe=TF)
    assert snap.available is False and snap.reason == BAR_FUTURE_TIMESTAMP


def test_provider_never_raises_on_bad_now_fn(tmp_path):
    def boom():
        raise RuntimeError("clock blew up")
    prov = _provider(tmp_path, [_bar()], now_fn=boom)
    snap = prov.completed_bar(record=_rec(), trading_date=TD, timeframe=TF)
    assert snap.available is False and snap.reason == PROVIDER_ERROR


# ── success paths ────────────────────────────────────────────────────────────────────────
def test_valid_snapshot_completed_bar_available(tmp_path):
    prov = _provider(tmp_path, [_bar()])
    snap = prov.completed_bar(record=_rec(), trading_date=TD, timeframe=TF)
    assert snap.available is True and snap.is_available
    assert snap.bar_end_time == f"{TD}T21:00:00Z"
    assert snap.source == "local_snapshot:test" and snap.version == "v1"
    assert snap.reason is None


def test_valid_snapshot_jsonl_and_directory(tmp_path):
    # JSONL file + a directory of files both load and match.
    d = tmp_path / "shadow_bar_snapshots"
    d.mkdir()
    (d / "a.jsonl").write_text(json.dumps(_bar(instrument_uid="x", listing_uid="y")) + "\n"
                               + json.dumps(_bar()) + "\n")
    prov = LocalCompletedBarSnapshotProvider(str(d), now_fn=NOW)
    snap = prov.completed_bar(record=_rec(), trading_date=TD, timeframe=TF)
    assert snap.available is True


def test_valid_bar_survives_real_boundary(tmp_path):
    # End-to-end through the SAME wrappers the scheduler uses (safe_completed_bar re-validates
    # bar_end_time[:10]==trading_date and requires source+version+bar_end_time).
    prov = _provider(tmp_path, [_bar()])
    snap = safe_completed_bar(prov, record=_rec(), trading_date=TD, timeframe=TF)
    assert isinstance(snap, CompletedBarSnapshot) and snap.available is True
    fn = as_bar_available_fn(prov)
    assert fn(_rec(), TD) is True
    # and an unavailable record is False through the same callable
    assert as_bar_available_fn(_provider(tmp_path, [_bar(available=False)], name="u.json"))(
        _rec(), TD) is False


# ── config seam (main.py) — default-off / flag-gated ─────────────────────────────────────
def test_seam_flag_off_no_construction(tmp_path):
    # Flag off → None, regardless of a configured (even valid) source. No provider built.
    cfg = {"completed_bar_snapshot_source": _write(tmp_path, [_bar()])}
    assert main.build_completed_bar_provider_from_config(OFF, cfg) is None


def test_seam_flag_on_no_source_fails_closed():
    assert main.build_completed_bar_provider_from_config(ON, {}) is None


def test_seam_flag_on_unsafe_source_fails_closed():
    cfg = {"completed_bar_snapshot_source": "/root/trading/backtest.db"}
    assert main.build_completed_bar_provider_from_config(ON, cfg) is None


def test_seam_flag_on_valid_source_constructs(tmp_path):
    cfg = {"completed_bar_snapshot_source": _write(tmp_path, [_bar()])}
    prov = main.build_completed_bar_provider_from_config(ON, cfg)
    assert isinstance(prov, LocalCompletedBarSnapshotProvider)


# ── broker-free / no-touch proofs ────────────────────────────────────────────────────────
def test_provider_source_references_no_broker_and_no_fetch_bars():
    text = (REPO / "bot" / "universe" / "local_bar_provider.py").read_text()
    # Mirror the repo convention (test_shadow_prereqs_w1_w2): forbid the actual broker IMPORT
    # tokens / call, not prose names a docstring may legitimately cite as "the thing NOT used".
    for forbidden in ("import bot.brokers", "from bot.brokers", "ib_insync", "trading_ig",
                      "bot.connection"):
        assert forbidden not in text, f"local_bar_provider references {forbidden}"
    assert ".fetch_bars(" not in text


def test_provider_import_pulls_in_no_broker_module():
    code = (
        "import sys\n"
        "import bot.universe.local_bar_provider\n"
        "bad = [m for m in sys.modules if m == 'ib_insync' or m.startswith('ib_insync.')\n"
        "       or m == 'trading_ig' or m.startswith('trading_ig.')\n"
        "       or m.startswith('bot.brokers')]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                       capture_output=True, text=True)
    assert r.returncode == 0, f"a broker module was imported at import time: {r.stderr}"


def test_provider_opens_only_the_snapshot_no_production_db(tmp_path, monkeypatch):
    prov = _provider(tmp_path, [_bar()])
    opened = []
    real_open = builtins.open

    def tracking_open(file, *a, **k):
        opened.append(os.path.abspath(str(file)))
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", tracking_open)
    prov.completed_bar(record=_rec(), trading_date=TD, timeframe=TF)
    prod = {"positions.db", "regime.db", "backtest.db", "universe.db", "universe_shadow.db",
            "tradingbot.db", "orders.db", "executions.db", "fills.db"}
    for path in opened:
        assert os.path.basename(path) not in prod, f"provider opened a production DB: {path}"
    # every opened path is under the temp snapshot dir
    assert all(str(tmp_path) in p for p in opened)
