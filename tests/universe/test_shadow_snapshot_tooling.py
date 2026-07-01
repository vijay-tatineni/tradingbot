"""Fixtures-only tests for the offline snapshot generation + validation tooling.

Proves ``tools/gen_shadow_snapshots.py`` + ``tools/validate_shadow_snapshots.py`` are broker-free,
offline-only, deterministic, and fail-closed: a valid approved input yields schema_version=1
completed/local artifacts + a hashed manifest that the validator (and the REAL providers/records seam)
accept; and every bad input (too few bars, bool OHLCV, future bar, forbidden key, production-DB path,
tampered artifact) is rejected. All temporary paths — no production DB / live provider is read, created,
or altered; no runtime snapshot dir is created.
"""
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timezone, timedelta

import pytest

from tools import gen_shadow_snapshots as gen
from tools.gen_shadow_snapshots import GenError, generate
from tools.validate_shadow_snapshots import validate

REPO = pathlib.Path(__file__).resolve().parents[2]

# Fixture clock AFTER the synthetic 2025 sessions so a completed bar is never judged "future".
NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
INSTRUMENTS = [
    {"canonical_instrument_id": "ci-aaa-001", "instrument_uid": "iu-aaa",
     "listing_uid": "lu-aaa-usd", "currency": "USD", "display_symbol": "AAA"},
    {"canonical_instrument_id": "ci-bbb-002", "instrument_uid": "iu-bbb",
     "listing_uid": "lu-bbb-usd", "currency": "USD", "display_symbol": "BBB"},
]


def _bars(n=220, start="2025-01-01", bad_volume=False):
    d0 = datetime.strptime(start, "%Y-%m-%d")
    out = []
    for i in range(n):
        d = (d0 + timedelta(days=i)).strftime("%Y-%m-%d")
        base = 100.0 + i * 0.5
        out.append({"date": d, "open": base, "high": base + 1, "low": base - 1,
                    "close": base + 0.25,
                    "volume": (True if bad_volume else 1000.0 + i)})
    return out


def _write_input(tmp, *, n_bars=220, mutate_inst=None, extra_key=None):
    """Create an approved input dir (approval.json + ohlcv.json). Returns the dir path."""
    ind = tmp / "approved_in"
    ind.mkdir()
    approval = {
        "approval_id": "OHLCV-APPR-2026-07-01-01", "approver": "operator@cogniflowai",
        "approval_timestamp": "2026-07-01T00:00:00Z", "data_source_name": "operator_offline_export",
        "data_source_version": "v1", "license_status": "internal_shadow_use_permitted",
        "asset_class": "equity", "allowed_use": "Dynamic Universe shadow-only",
        "not_approved_for": "paper trading; live trading; order submission",
    }
    (ind / "approval.json").write_text(json.dumps(approval))
    insts = []
    for i, base in enumerate(INSTRUMENTS):
        rec = dict(base)
        rec["bars"] = _bars(n=n_bars)
        if mutate_inst and i == 0:
            mutate_inst(rec)
        if extra_key and i == 0:
            rec[extra_key] = "leak"
        insts.append(rec)
    (ind / "ohlcv.json").write_text(json.dumps(insts))
    return str(ind)


# ── happy path ──────────────────────────────────────────────────────────────────────────────────
def test_generate_then_validate_ok(tmp_path):
    ind = _write_input(tmp_path)
    out = tmp_path / "snap"
    manifest = generate(ind, str(out), generated_at="2026-07-01T00:00:00Z", now=NOW)

    expected_td = (datetime(2025, 1, 1) + timedelta(days=219)).strftime("%Y-%m-%d")
    assert manifest["instrument_count"] == 2
    assert manifest["trading_date"] == expected_td  # 2025-01-01 + 219 days
    assert (out / "local_bars" / "local_bars.jsonl").is_file()
    assert (out / "completed_bars" / "completed_bars.jsonl").is_file()
    assert (out / "MANIFEST.json").is_file()

    # Every emitted record is schema_version=1 with the full identity triple and a proof.
    lines = (out / "local_bars" / "local_bars.jsonl").read_text().splitlines()
    recs = [json.loads(x) for x in lines]
    assert all(r["schema_version"] == 1 for r in recs)
    assert all(r["canonical_instrument_id"] and r["instrument_uid"] and r["listing_uid"] for r in recs)
    assert all(r["content_hash"] and r["timeframe"] == "1d" and len(r["bars"]) == 220 for r in recs)
    # No credential-shaped keys leaked into output.
    assert all(not gen._find_forbidden_keys(r) for r in recs)

    ok, chk = validate(str(out), now=NOW)
    failed = [c for c in chk.items if not c["ok"]]
    assert ok, f"unexpected failures: {failed}"
    # The dry-run + seam + G7 checks actually ran and passed.
    names = {c["name"] for c in chk.items}
    assert "seam:build" in names
    assert any(n.startswith("dryrun:bars:") for n in names)
    assert any(n.startswith("dryrun:completed:") for n in names)
    assert ("g7_broker_free", True) in {(c["name"], c["ok"]) for c in chk.items}


def test_generation_is_deterministic(tmp_path):
    ind = _write_input(tmp_path)
    out1, out2 = tmp_path / "s1", tmp_path / "s2"
    generate(ind, str(out1), generated_at="2026-07-01T00:00:00Z", generated_by="op", now=NOW)
    generate(ind, str(out2), generated_at="2026-07-01T00:00:00Z", generated_by="op", now=NOW)
    for rel in ("local_bars/local_bars.jsonl", "completed_bars/completed_bars.jsonl"):
        assert (out1 / rel).read_bytes() == (out2 / rel).read_bytes()


def test_cli_roundtrip_exit_codes(tmp_path):
    ind = _write_input(tmp_path)
    out = tmp_path / "snap"
    env = dict(os.environ, PYTHONPATH=str(REPO))
    g = subprocess.run([sys.executable, "-m", "tools.gen_shadow_snapshots", "--input", ind,
                        "--out", str(out), "--generated-at", "2026-07-01T00:00:00Z"],
                       cwd=str(REPO), env=env, capture_output=True, text=True)
    assert g.returncode == 0, g.stderr
    v = subprocess.run([sys.executable, "-m", "tools.validate_shadow_snapshots",
                        "--snapshots", str(out)], cwd=str(REPO), env=env,
                       capture_output=True, text=True)
    # NOTE: CLI validate uses the real wall clock; the 2025 fixture sessions are safely in the past.
    assert v.returncode == 0, v.stdout + v.stderr
    assert "VALIDATE_OK" in v.stdout


# ── negatives (generation fails closed) ───────────────────────────────────────────────────────────
def test_too_few_bars_rejected(tmp_path):
    ind = _write_input(tmp_path, n_bars=199)
    with pytest.raises(GenError, match="need ≥ 200"):
        generate(ind, str(tmp_path / "snap"), now=NOW)


def test_bool_volume_rejected(tmp_path):
    ind = _write_input(tmp_path, mutate_inst=lambda r: r["bars"].__setitem__(
        5, {**r["bars"][5], "volume": True}))
    with pytest.raises(GenError, match="non-numeric/bool"):
        generate(ind, str(tmp_path / "snap"), now=NOW)


def test_future_bar_rejected(tmp_path):
    ind = _write_input(tmp_path)
    # Clock BEFORE the fixture sessions → the completed bar_end_time is in the future.
    past_now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(GenError, match="future"):
        generate(ind, str(tmp_path / "snap"), now=past_now)


def test_forbidden_key_rejected(tmp_path):
    ind = _write_input(tmp_path, extra_key="api_token")
    with pytest.raises(GenError, match="forbidden key"):
        generate(ind, str(tmp_path / "snap"), now=NOW)


def test_production_db_output_path_rejected(tmp_path):
    ind = _write_input(tmp_path)
    with pytest.raises(GenError, match="unsafe|database basename"):
        generate(ind, str(tmp_path / "backtest.db"), now=NOW)


def test_missing_ohlcv_input_rejected(tmp_path):
    ind = tmp_path / "in"
    ind.mkdir()
    (ind / "approval.json").write_text(json.dumps({
        "approval_id": "x", "approver": "y", "approval_timestamp": "z",
        "data_source_name": "n", "data_source_version": "v", "license_status": "l",
        "asset_class": "equity", "allowed_use": "Dynamic Universe shadow-only",
        "not_approved_for": "paper/live"}))
    with pytest.raises(GenError, match="ohlcv"):
        generate(str(ind), str(tmp_path / "snap"), now=NOW)


# ── negatives (validation catches tampering) ─────────────────────────────────────────────────────
def test_tampered_artifact_fails_validation(tmp_path):
    ind = _write_input(tmp_path)
    out = tmp_path / "snap"
    generate(ind, str(out), generated_at="2026-07-01T00:00:00Z", now=NOW)
    lb = out / "local_bars" / "local_bars.jsonl"
    recs = [json.loads(x) for x in lb.read_text().splitlines()]
    recs[0]["bars"][0]["close"] = 99999.0  # tamper: content no longer matches content_hash/manifest sha
    lb.write_text("\n".join(gen._canonical_json(r) for r in recs) + "\n")

    ok, chk = validate(str(out), now=NOW)
    assert not ok
    fails = {c["name"] for c in chk.items if not c["ok"]}
    assert any(n.startswith("manifest:") for n in fails)  # sha256 no longer matches manifest
    assert any(n.startswith("local:") for n in fails)     # content_hash recompute mismatch


# ── G7: broker-free proof, isolated in a subprocess (independent of the pytest process) ───────────
def test_tools_import_no_broker_modules():
    code = (
        "import sys, json\n"
        "import tools.gen_shadow_snapshots, tools.validate_shadow_snapshots\n"
        "bad = sorted(m for m in sys.modules "
        "if m in ('ib_insync', 'trading_ig') or m.startswith('bot.brokers'))\n"
        "print(json.dumps(bad))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(REPO))
    r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1]) == []
