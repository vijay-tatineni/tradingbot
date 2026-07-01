#!/usr/bin/env python3
"""Dynamic Universe — OFFLINE snapshot VALIDATION tool (broker-free, offline-only).

Out-of-band operator tool. It is **NOT imported by ``main.py`` / ``bot.universe`` runtime**. Given a
snapshot root produced by ``gen_shadow_snapshots.py`` (``local_bars/`` + ``completed_bars/`` +
``MANIFEST.json``), it enforces the design's pre-enablement checks and exits non-zero on ANY failure:

  * path safety — dirs/files basename + symlink-resolved basename ∉ production-DB set; no symlinks;
  * manifest integrity — every listed output exists, sha256 matches, record_count matches;
  * per-record schema — schema_version==1, identity, timeframe, dates, proof, ≥200 OHLCV bars (numeric,
    non-bool), non-future ``bar_end_time``, and NO credential-shaped keys;
  * cross-snapshot consistency — same instruments both sides, agreeing identity triple + trading_date;
  * per-record ``content_hash`` recompute (determinism / tamper proof);
  * identity/build via the REAL ``build_shadow_records_source`` (no exclusions, one record per instrument);
  * provider dry-run (G6) via the REAL ``build_local_bars_provider`` / ``build_local_completed_bar_provider``
    against the local files only — asserts ``available`` for every instrument;
  * zero-broker/provider proof (G7) — a SUBPROCESS imports this tool + the providers and asserts no
    ``ib_insync`` / ``trading_ig`` / ``bot.brokers*`` module was loaded (isolated from any test-process
    pollution). No network/broker call is ever made — the providers open files mode ``"r"`` only.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from bot.universe.local_bar_provider import (
    PRODUCTION_DB_BASENAMES, _parse_records_from_text,
    build_local_completed_bar_provider, validate_source_path)
from bot.universe.local_bars_provider import build_local_bars_provider
from bot.universe.shadow_records import build_shadow_records_source
from tools.gen_shadow_snapshots import (
    MIN_BARS, SCHEMA_VERSION, _OHLCV_FIELDS, _content_hash, _find_forbidden_keys, _num_ok)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEFRAME = "1d"
_DATE_LEN = 10


class Checks:
    """Accumulates named pass/fail check results."""

    def __init__(self):
        self.items = []

    def record(self, name, ok, detail=""):
        self.items.append({"name": name, "ok": bool(ok), "detail": detail})
        return ok

    @property
    def ok(self):
        return all(c["ok"] for c in self.items)


def _read_jsonl_dir(path):
    """Read + concatenate all .json/.jsonl records under a dir (same shapes the providers accept)."""
    recs = []
    for n in sorted(os.listdir(path)):
        if not (n.endswith(".json") or n.endswith(".jsonl")):
            continue
        with open(os.path.join(path, n), "r") as fh:
            parsed, reason = _parse_records_from_text(fh.read())
        if reason is not None:
            raise ValueError(f"{n}: {reason}")
        recs.extend(parsed)
    return recs


def _sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _valid_date(s):
    if not isinstance(s, str) or len(s) != _DATE_LEN:
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _parse_end(s):
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _check_path_safety(chk, label, path):
    ok = True
    if validate_source_path(path) is not None:
        ok = chk.record(f"path_safety:{label}", False, f"unsafe source path: {path}")
    elif os.path.islink(path):
        ok = chk.record(f"path_safety:{label}", False, f"symlink: {path}")
    else:
        bad = []
        for root, dirs, files in os.walk(path):
            for n in dirs + files:
                fp = os.path.join(root, n)
                if os.path.islink(fp):
                    bad.append(f"symlink:{fp}")
                if (os.path.basename(os.path.normpath(fp)) in PRODUCTION_DB_BASENAMES
                        or os.path.basename(os.path.realpath(fp)) in PRODUCTION_DB_BASENAMES):
                    bad.append(f"prod-db-basename:{fp}")
        ok = chk.record(f"path_safety:{label}", not bad, "; ".join(bad) or "ok")
    return ok


def _check_local_record(chk, rec, now):
    cid = rec.get("canonical_instrument_id")
    tag = f"local:{cid}"
    problems = []
    if rec.get("schema_version") != SCHEMA_VERSION:
        problems.append("schema_version!=1")
    for k in ("canonical_instrument_id", "instrument_uid", "listing_uid"):
        if not isinstance(rec.get(k), str) or not rec.get(k).strip():
            problems.append(f"missing {k}")
    if str(rec.get("timeframe")) != TIMEFRAME:
        problems.append("timeframe!=1d")
    td, bet = rec.get("trading_date"), rec.get("bar_end_time")
    if not _valid_date(td):
        problems.append("bad trading_date")
    end = _parse_end(bet)
    if end is None:
        problems.append("unparseable bar_end_time")
    else:
        if str(bet)[:_DATE_LEN] != str(td):
            problems.append("bar_end_time[:10]!=trading_date")
        if end > now:
            problems.append("bar_end_time in future")
    if not (rec.get("content_hash") or rec.get("version")):
        problems.append("missing proof (version/content_hash)")
    bars = rec.get("bars")
    if not isinstance(bars, list) or len(bars) < MIN_BARS:
        problems.append(f"<{MIN_BARS} bars")
    else:
        for b in bars:
            if not isinstance(b, dict) or any(not _num_ok(b.get(f)) for f in _OHLCV_FIELDS):
                problems.append("non-numeric/bool OHLCV")
                break
    fb = _find_forbidden_keys(rec)
    if fb:
        problems.append(f"forbidden keys: {','.join(fb)}")
    # content_hash recompute (tamper/determinism proof)
    if isinstance(bars, list):
        content = {"canonical_instrument_id": rec.get("canonical_instrument_id"),
                   "instrument_uid": rec.get("instrument_uid"),
                   "listing_uid": rec.get("listing_uid"), "currency": rec.get("currency"),
                   "timeframe": rec.get("timeframe"), "trading_date": td,
                   "bar_end_time": bet, "bars": bars}
        if rec.get("content_hash") and _content_hash(content) != rec.get("content_hash"):
            problems.append("content_hash mismatch")
    return chk.record(tag, not problems, "; ".join(problems) or "ok")


def _check_completed_record(chk, rec, now):
    cid = rec.get("canonical_instrument_id") or rec.get("instrument_uid")
    tag = f"completed:{cid}"
    problems = []
    if rec.get("schema_version") != SCHEMA_VERSION:
        problems.append("schema_version!=1")
    for k in ("instrument_uid", "listing_uid"):
        if not isinstance(rec.get(k), str) or not rec.get(k).strip():
            problems.append(f"missing {k}")
    if str(rec.get("timeframe")) != TIMEFRAME:
        problems.append("timeframe!=1d")
    if rec.get("available") is not True:
        problems.append("available!=true(bool)")
    td, bet = rec.get("trading_date"), rec.get("bar_end_time")
    if not _valid_date(td):
        problems.append("bad trading_date")
    end = _parse_end(bet)
    if end is None:
        problems.append("unparseable bar_end_time")
    else:
        if str(bet)[:_DATE_LEN] != str(td):
            problems.append("bar_end_time[:10]!=trading_date")
        if end > now:
            problems.append("bar_end_time in future")
    if not (rec.get("content_hash") or rec.get("version")):
        problems.append("missing proof (version/content_hash)")
    fb = _find_forbidden_keys(rec)
    if fb:
        problems.append(f"forbidden keys: {','.join(fb)}")
    content = {"canonical_instrument_id": rec.get("canonical_instrument_id"),
               "instrument_uid": rec.get("instrument_uid"),
               "listing_uid": rec.get("listing_uid"), "timeframe": rec.get("timeframe"),
               "trading_date": td, "bar_end_time": bet, "available": rec.get("available")}
    if rec.get("content_hash") and _content_hash(content) != rec.get("content_hash"):
        problems.append("content_hash mismatch")
    return chk.record(tag, not problems, "; ".join(problems) or "ok")


def _g7_broker_free_proof(chk):
    """SUBPROCESS proof: importing this validator (→ providers/seam) loads NO broker module. Isolated
    from the pytest process so a sibling test's ``import main`` cannot pollute the check."""
    code = (
        "import sys, json\n"
        "import tools.validate_shadow_snapshots as v  # pulls providers + seam + gen\n"
        "bad = sorted(m for m in sys.modules "
        "if m in ('ib_insync', 'trading_ig') or m.startswith('bot.brokers'))\n"
        "print('G7:' + json.dumps(bad))\n"
    )
    env = dict(os.environ, PYTHONPATH=REPO_ROOT)
    try:
        r = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=env,
                           capture_output=True, text=True, timeout=120)
    except Exception as e:  # pragma: no cover - defensive
        return chk.record("g7_broker_free", False, f"subprocess error: {e}")
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("G7:")), None)
    if r.returncode != 0 or line is None:
        return chk.record("g7_broker_free", False, f"rc={r.returncode} err={r.stderr.strip()[:200]}")
    bad = json.loads(line[3:])
    return chk.record("g7_broker_free", not bad, "no broker module loaded" if not bad
                      else f"broker modules present: {bad}")


def validate(snapshots_dir, *, now=None):
    """Run all checks against a snapshot root. Returns ``(ok: bool, Checks)``. Never raises for a
    validation failure — only genuinely unexpected IO raises."""
    now = now or datetime.now(timezone.utc)
    chk = Checks()
    snapshots_dir = os.path.abspath(snapshots_dir)
    bars_dir = os.path.join(snapshots_dir, "local_bars")
    completed_dir = os.path.join(snapshots_dir, "completed_bars")
    manifest_path = os.path.join(snapshots_dir, "MANIFEST.json")

    # ── structure ──
    if not chk.record("structure:local_bars", os.path.isdir(bars_dir), bars_dir):
        return chk.ok, chk
    if not chk.record("structure:completed_bars", os.path.isdir(completed_dir), completed_dir):
        return chk.ok, chk
    if not chk.record("structure:manifest", os.path.isfile(manifest_path), manifest_path):
        return chk.ok, chk

    # ── path safety ──
    _check_path_safety(chk, "local_bars", bars_dir)
    _check_path_safety(chk, "completed_bars", completed_dir)

    # ── manifest integrity ──
    with open(manifest_path, "r") as fh:
        manifest = json.load(fh)
    for out in manifest.get("outputs", []):
        fp = os.path.join(snapshots_dir, out["path"])
        if not os.path.isfile(fp):
            chk.record(f"manifest:{out['path']}", False, "missing output file")
            continue
        sha_ok = _sha256_file(fp) == out.get("sha256")
        with open(fp, "r") as f2:
            n = sum(1 for ln in f2 if ln.strip())
        chk.record(f"manifest:{out['path']}", sha_ok and n == out.get("record_count"),
                   f"sha256_ok={sha_ok} count={n}/{out.get('record_count')}")
    chk.record("manifest:allowed_use", manifest.get("allowed_use") == "Dynamic Universe shadow-only",
               str(manifest.get("allowed_use")))

    # ── load records ──
    try:
        local_recs = _read_jsonl_dir(bars_dir)
        completed_recs = _read_jsonl_dir(completed_dir)
    except ValueError as e:
        chk.record("parse", False, str(e))
        return chk.ok, chk
    chk.record("nonempty", bool(local_recs) and bool(completed_recs),
               f"local={len(local_recs)} completed={len(completed_recs)}")

    # ── per-record schema ──
    for r in local_recs:
        _check_local_record(chk, r, now)
    for r in completed_recs:
        _check_completed_record(chk, r, now)

    # ── cross-snapshot consistency ──
    local_by_cid = {r.get("canonical_instrument_id"): r for r in local_recs}
    completed_by_cid = {r.get("canonical_instrument_id"): r for r in completed_recs}
    same_set = set(local_by_cid) == set(completed_by_cid) and None not in local_by_cid
    chk.record("cross:same_instruments", same_set,
               f"local={sorted(k for k in local_by_cid)} completed={sorted(k for k in completed_by_cid)}")
    if same_set:
        for cid, lr in local_by_cid.items():
            cr = completed_by_cid[cid]
            agree = (lr.get("instrument_uid") == cr.get("instrument_uid")
                     and lr.get("listing_uid") == cr.get("listing_uid")
                     and lr.get("trading_date") == cr.get("trading_date"))
            chk.record(f"cross:{cid}", agree,
                       "identity+trading_date agree" if agree else "mismatch")

    # ── seam identity/build (real records source) ──
    src = build_shadow_records_source(bars_source=bars_dir, completed_source=completed_dir,
                                      now_fn=lambda: now)
    if src is None:
        chk.record("seam:build", False, "records source not constructed (path unsafe/missing)")
        return chk.ok, chk
    result = src.build()
    seam_ok = (result.source_reason is None and not result.excluded
               and len(result.records) == len(local_by_cid))
    chk.record("seam:build", seam_ok,
               f"records={len(result.records)} excluded={result.excluded} "
               f"reason={result.source_reason}")

    # ── provider dry-run (G6): local files only, no broker/network ──
    bars_provider = build_local_bars_provider(bars_dir, now_fn=lambda: now)
    completed_provider = build_local_completed_bar_provider(completed_dir, now_fn=lambda: now)
    if bars_provider is None or completed_provider is None:
        chk.record("dryrun:providers", False, "provider(s) not constructed")
    else:
        for rec in result.records:
            cid = rec["canonical_instrument_id"]
            payload = bars_provider(rec)
            chk.record(f"dryrun:bars:{cid}", payload is not None,
                       "available" if payload is not None else "bars provider returned None")
            cr = completed_by_cid.get(cid, {})
            td = cr.get("trading_date")
            snap = completed_provider.completed_bar(record=rec, trading_date=td, timeframe=TIMEFRAME)
            chk.record(f"dryrun:completed:{cid}", snap.available,
                       "available" if snap.available else f"reason={snap.reason}")

    # ── G7 broker-free proof (subprocess) ──
    _g7_broker_free_proof(chk)
    return chk.ok, chk


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Validate broker-free Dynamic Universe shadow snapshot artifacts (offline; no live "
                    "provider/broker calls). Exits non-zero on any failure.")
    p.add_argument("--snapshots", required=True,
                   help="snapshot root (contains local_bars/, completed_bars/, MANIFEST.json)")
    p.add_argument("--quiet", action="store_true", help="only print failing checks")
    args = p.parse_args(argv)

    ok, chk = validate(args.snapshots)
    for c in chk.items:
        if c["ok"] and args.quiet:
            continue
        print(f"[{'PASS' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}")
    print(f"\nVALIDATE_{'OK' if ok else 'FAILED'}: "
          f"{sum(1 for c in chk.items if c['ok'])}/{len(chk.items)} checks passed")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
