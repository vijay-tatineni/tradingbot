#!/usr/bin/env python3
"""Dynamic Universe — OFFLINE snapshot GENERATION tool (broker-free, approved-offline-input-only).

Out-of-band operator tool. It is **NOT imported by ``main.py`` / ``bot.universe`` runtime**; an operator
runs it manually, under approval, to transform an APPROVED local offline OHLCV export into the two
``schema_version=1`` snapshot artifacts the deployed providers consume:

  * ``<out>/local_bars/local_bars.jsonl``      → ``bars_snapshot_source``      (LocalBarsSnapshotProvider)
  * ``<out>/completed_bars/completed_bars.jsonl`` → ``completed_bar_snapshot_source`` (LocalCompletedBar…)
  * ``<out>/MANIFEST.json``                    → per-file sha256 + record counts + approval metadata

Approved source (Option A operator file drop) per
``/root/deployment_records/offline_ohlcv_data_source_approval_proposal.md``. **Live feeds
(EODHD/IBKR/IG) and production ``backtest.db`` remain BLOCKED.**

Guarantees:
  * calls NO broker / IBKR / IG / EODHD / live market-data / FX / portfolio API — imports only stdlib +
    the broker-free path-safety helper from ``bot.universe.local_bar_provider``;
  * reads ONLY local ``approval.json`` + ``ohlcv.json``/``ohlcv.jsonl`` under the approved input dir —
    it opens no database and never a production execution DB;
  * refuses any input/output path that is (or symlink-resolves to) a production-DB basename, and refuses
    input records carrying credential-shaped keys (``*password*``/``*token*``/``*secret*``/``account*``/…);
  * writes ONLY under the explicit ``--out`` dir the operator supplies (there is NO default output path,
    so importing or smoke-running this tool can never create the real runtime snapshot dirs);
  * is DETERMINISTIC: records sorted by ``canonical_instrument_id``, JSON serialized with sorted keys,
    and each record's ``content_hash`` = sha256 over its content fields (audit metadata excluded). Pass
    ``--generated-at`` to reproduce byte-identical artifacts.
"""
import argparse
import getpass
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

# Single source of truth for dangerous DB basenames + pure path-safety (broker-free, no snapshot read).
from bot.universe.local_bar_provider import PRODUCTION_DB_BASENAMES, validate_source_path

TOOL_NAME = "gen_shadow_snapshots.py"
TOOL_VERSION = "1.0.0"

SCHEMA_VERSION = 1
DEFAULT_TIMEFRAME = "1d"
MIN_BARS = 200                      # evaluator computes sma200 → hard floor (matches shadow_records.MIN_BARS)
_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")
_DEFAULT_BAR_CLOSE_UTC = "00:00:00Z"   # non-future-safe for any date ≤ today

# Approval metadata the operator MUST supply in approval.json (proposal §4). Coverage/date_range/source
# hashes/raw-file manifest are DERIVED by this tool; the rest are attested by the approver.
_REQUIRED_APPROVAL_KEYS = (
    "approval_id", "approver", "approval_timestamp", "data_source_name",
    "data_source_version", "license_status", "asset_class", "allowed_use", "not_approved_for",
)
_ALLOWED_USE_EXACT = "Dynamic Universe shadow-only"

# Credential-/payload-shaped key substrings that must never appear in approved input or emitted output.
FORBIDDEN_KEY_SUBSTRINGS = (
    "password", "token", "secret", "account", "api_key", "apikey",
    "credential", "raw_payload", "raw_response", "private_key",
)


class GenError(Exception):
    """Fatal generation error (fail-closed; nothing is written)."""


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────
def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_json(obj) -> str:
    """Deterministic JSON: sorted keys, compact separators (used for content_hash + artifact lines)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _content_hash(content: dict) -> str:
    return _sha256_bytes(_canonical_json(content).encode("utf-8"))


def _find_forbidden_keys(obj, path="") -> list:
    """Recursively collect any key whose lower-case name contains a forbidden substring."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if any(sub in kl for sub in FORBIDDEN_KEY_SUBSTRINGS):
                found.append(f"{path}{k}")
            found.extend(_find_forbidden_keys(v, f"{path}{k}."))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(_find_forbidden_keys(v, f"{path}{i}."))
    return found


def _require_safe_path(label: str, path: str) -> None:
    """Reject a path that is empty, a production-DB basename, symlink-resolves to one, is a symlink, or
    ends in ``.db`` (this tool only ever reads/writes JSON/JSONL)."""
    reason = validate_source_path(path)
    if reason is not None:
        raise GenError(f"{label} path unsafe ({reason}): {path}")
    if os.path.islink(path):
        raise GenError(f"{label} path is a symlink (refused): {path}")
    base = os.path.basename(os.path.normpath(path))
    if base.endswith(".db") or base in PRODUCTION_DB_BASENAMES:
        raise GenError(f"{label} path resolves to a database basename (refused): {path}")


def _num_ok(v) -> bool:
    """Numeric and NOT bool (bool is an int subclass but never a valid price/volume)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ── input loading ────────────────────────────────────────────────────────────────────────────────
def _load_approval(input_dir: str):
    ap = os.path.join(input_dir, "approval.json")
    if not os.path.isfile(ap):
        raise GenError(f"missing approval.json in input dir: {input_dir}")
    with open(ap, "r") as fh:
        meta = json.load(fh)
    if not isinstance(meta, dict):
        raise GenError("approval.json must be a JSON object")
    missing = [k for k in _REQUIRED_APPROVAL_KEYS if not str(meta.get(k, "")).strip()]
    if missing:
        raise GenError(f"approval.json missing required metadata: {', '.join(missing)}")
    if str(meta.get("allowed_use")) != _ALLOWED_USE_EXACT:
        raise GenError(f'approval.json allowed_use must be exactly "{_ALLOWED_USE_EXACT}"')
    forbidden = _find_forbidden_keys(meta)
    if forbidden:
        raise GenError(f"approval.json carries forbidden key(s): {', '.join(forbidden)}")
    return meta, ap


def _load_ohlcv(input_dir: str):
    js = os.path.join(input_dir, "ohlcv.json")
    jl = os.path.join(input_dir, "ohlcv.jsonl")
    have = [p for p in (js, jl) if os.path.isfile(p)]
    if not have:
        raise GenError(f"missing ohlcv.json or ohlcv.jsonl in input dir: {input_dir}")
    if len(have) == 2:
        raise GenError("input dir has BOTH ohlcv.json and ohlcv.jsonl — provide exactly one")
    path = have[0]
    with open(path, "r") as fh:
        text = fh.read()
    if path.endswith(".jsonl"):
        instruments = []
        for line in text.splitlines():
            ls = line.strip()
            if not ls:
                continue
            instruments.append(json.loads(ls))
    else:
        instruments = json.loads(text)
    if not isinstance(instruments, list) or not instruments:
        raise GenError("ohlcv input must be a non-empty JSON array (or JSONL) of instrument records")
    if not all(isinstance(r, dict) for r in instruments):
        raise GenError("every ohlcv instrument record must be a JSON object")
    return instruments, path


# ── per-instrument transform ───────────────────────────────────────────────────────────────────────
def _build_records_for_instrument(inst: dict, *, as_of, timeframe, bar_close, min_bars, now):
    """Return (local_bars_record, completed_record) for one approved instrument, or raise GenError."""
    forbidden = _find_forbidden_keys(inst)
    if forbidden:
        raise GenError(f"instrument record carries forbidden key(s): {', '.join(forbidden)}")

    cid = inst.get("canonical_instrument_id")
    iuid = inst.get("instrument_uid")
    luid = inst.get("listing_uid")
    for name, val in (("canonical_instrument_id", cid), ("instrument_uid", iuid),
                      ("listing_uid", luid)):
        if not isinstance(val, str) or not val.strip():
            raise GenError(f"instrument missing/invalid {name} (identity is R2A-1, never a ticker)")
    currency = inst.get("currency")
    if not isinstance(currency, str) or not currency.strip():
        raise GenError(f"instrument {cid} missing currency")

    bars_in = inst.get("bars")
    if not isinstance(bars_in, list) or not bars_in:
        raise GenError(f"instrument {cid} has no bars")

    # Normalize + validate each OHLCV bar; keep only bars on/before the as-of session.
    norm = []
    for b in bars_in:
        if not isinstance(b, dict):
            raise GenError(f"instrument {cid} has a non-object bar")
        d = b.get("date")
        if not isinstance(d, str) or len(d) != 10:
            raise GenError(f"instrument {cid} bar missing YYYY-MM-DD date")
        for f in _OHLCV_FIELDS:
            if not _num_ok(b.get(f)):
                raise GenError(f"instrument {cid} bar {d} has non-numeric/bool {f}")
        norm.append({"date": d, **{f: float(b[f]) for f in _OHLCV_FIELDS}})

    norm.sort(key=lambda x: x["date"])
    target_date = as_of or norm[-1]["date"]
    kept = [b for b in norm if b["date"] <= target_date]
    if not kept or kept[-1]["date"] != target_date:
        raise GenError(f"instrument {cid} has no bar on as-of date {target_date}")
    if len(kept) < min_bars:
        raise GenError(f"instrument {cid} has {len(kept)} bars ≤ {target_date}; need ≥ {min_bars}")

    bar_end_time = f"{target_date}T{bar_close}"
    # Non-future guard (relative to the tool's clock): never emit an un-closed/future session.
    _end = datetime.fromisoformat(bar_end_time.replace("Z", "+00:00"))
    if _end > now:
        raise GenError(f"instrument {cid} bar_end_time {bar_end_time} is in the future")

    # Deterministic content proof (audit metadata excluded).
    bars_content = kept  # oldest→newest, normalized
    local_content = {
        "canonical_instrument_id": cid, "instrument_uid": iuid, "listing_uid": luid,
        "currency": currency, "timeframe": timeframe, "trading_date": target_date,
        "bar_end_time": bar_end_time, "bars": bars_content,
    }
    completed_content = {
        "canonical_instrument_id": cid, "instrument_uid": iuid, "listing_uid": luid,
        "timeframe": timeframe, "trading_date": target_date, "bar_end_time": bar_end_time,
        "available": True,
    }
    local_hash = _content_hash(local_content)
    completed_hash = _content_hash(completed_content)

    return (cid, target_date, len(kept), currency, local_content, completed_content,
            local_hash, completed_hash, inst.get("display_symbol"))


def generate(input_dir: str, out_dir: str, *, as_of=None, timeframe=DEFAULT_TIMEFRAME,
             bar_close=_DEFAULT_BAR_CLOSE_UTC, min_bars=MIN_BARS, generated_at=None,
             generated_by=None, readonly=False, now=None) -> dict:
    """Transform the approved input dir into snapshot artifacts + MANIFEST. Returns the manifest dict.

    Fail-closed: any validation error raises ``GenError`` before/while writing; on a mid-write error the
    partially-written files are best-effort left for inspection (the manifest is written LAST and its
    presence is the completion proof). ``now`` is an injectable UTC clock for tests."""
    now = now or datetime.now(timezone.utc)
    generated_at = generated_at or now.isoformat().replace("+00:00", "Z")
    generated_by = generated_by or (os.environ.get("SUDO_USER") or _safe_getuser())

    input_dir = os.path.abspath(input_dir)
    out_dir = os.path.abspath(out_dir)
    if not os.path.isdir(input_dir):
        raise GenError(f"input dir not found: {input_dir}")

    bars_dir = os.path.join(out_dir, "local_bars")
    completed_dir = os.path.join(out_dir, "completed_bars")
    _require_safe_path("input", input_dir)
    for label, p in (("out", out_dir), ("local_bars", bars_dir), ("completed_bars", completed_dir)):
        _require_safe_path(label, p)

    meta, approval_path = _load_approval(input_dir)
    instruments, ohlcv_path = _load_ohlcv(input_dir)

    approval_id = str(meta["approval_id"])
    built = []
    seen_cids = set()
    for inst in instruments:
        rec = _build_records_for_instrument(
            inst, as_of=as_of, timeframe=timeframe, bar_close=bar_close, min_bars=min_bars, now=now)
        cid = rec[0]
        if cid in seen_cids:
            raise GenError(f"duplicate canonical_instrument_id in input: {cid}")
        seen_cids.add(cid)
        built.append(rec)

    if not built:
        raise GenError("no instruments produced")

    # Single as-of across the set (validator asserts bars/completed trading_date agree per instrument).
    trading_dates = {r[1] for r in built}
    if len(trading_dates) != 1:
        raise GenError(f"instruments resolved to multiple as-of dates {sorted(trading_dates)}; "
                       f"pass --as-of to pin one")
    the_date = built[0][1]

    audit = {
        "generated_at": generated_at, "generated_by": generated_by,
        "approved_by": str(meta["approver"]), "approved_at": str(meta["approval_timestamp"]),
        "approval_ref": approval_id,
        "data_source_name": str(meta["data_source_name"]),
        "data_source_version": str(meta["data_source_version"]),
    }
    source_label = f"local_snapshot:{meta['data_source_name']}:{meta['data_source_version']}"

    local_records, completed_records, inst_manifest = [], [], []
    for (cid, td, nbars, currency, lc, cc, lh, ch, disp) in sorted(built, key=lambda r: r[0]):
        lrec = {"schema_version": SCHEMA_VERSION, **lc, "source": source_label,
                "content_hash": lh, "version": lh, **audit}
        if disp:
            lrec["display_symbol"] = disp
        crec = {"schema_version": SCHEMA_VERSION, **cc, "source": source_label,
                "content_hash": ch, "version": ch, **audit}
        # Defensive: emitted output must be forbidden-key clean.
        for r in (lrec, crec):
            bad = _find_forbidden_keys(r)
            if bad:
                raise GenError(f"emitted record for {cid} carries forbidden key(s): {', '.join(bad)}")
        local_records.append(lrec)
        completed_records.append(crec)
        inst_manifest.append({"canonical_instrument_id": cid, "instrument_uid": lc["instrument_uid"],
                              "listing_uid": lc["listing_uid"], "currency": currency,
                              "trading_date": td, "bar_count": nbars})

    # ── write artifacts (deterministic: sorted records, sorted keys per line) ──
    os.makedirs(bars_dir, exist_ok=True)
    os.makedirs(completed_dir, exist_ok=True)
    bars_file = os.path.join(bars_dir, "local_bars.jsonl")
    completed_file = os.path.join(completed_dir, "completed_bars.jsonl")
    _write_jsonl(bars_file, local_records)
    _write_jsonl(completed_file, completed_records)

    inputs_manifest = [
        {"path": "approval.json", "sha256": _sha256_file(approval_path), "record_count": 1},
        {"path": os.path.basename(ohlcv_path), "sha256": _sha256_file(ohlcv_path),
         "record_count": len(instruments)},
    ]
    outputs_manifest = [
        {"path": "local_bars/local_bars.jsonl", "sha256": _sha256_file(bars_file),
         "record_count": len(local_records)},
        {"path": "completed_bars/completed_bars.jsonl", "sha256": _sha256_file(completed_file),
         "record_count": len(completed_records)},
    ]
    manifest = {
        "manifest_version": 1, "generator": TOOL_NAME, "generator_version": TOOL_VERSION,
        "generated_at": generated_at, "generated_by": generated_by,
        "approval": {k: meta[k] for k in _REQUIRED_APPROVAL_KEYS},
        "approval_id": approval_id, "timeframe": timeframe, "trading_date": the_date,
        "instrument_count": len(local_records),
        "inputs": inputs_manifest, "outputs": outputs_manifest, "instruments": inst_manifest,
        "allowed_use": _ALLOWED_USE_EXACT,
        "not_approved_for": meta["not_approved_for"],
    }
    manifest_file = os.path.join(out_dir, "MANIFEST.json")
    with open(manifest_file, "w") as fh:
        fh.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    if readonly:
        # Defense-in-depth (runtime guarantee is STRUCTURAL — providers open mode "r"). Leave dirs
        # writable so re-generation / cleanup works; hardening dirs (0555) / chattr +i is a G5 op step.
        for f in (bars_file, completed_file, manifest_file):
            os.chmod(f, 0o444)

    return manifest


def _safe_getuser() -> str:
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - environments without a passwd entry
        return "operator"


def _write_jsonl(path: str, records: list) -> None:
    with open(path, "w") as fh:
        for r in records:
            fh.write(_canonical_json(r) + "\n")


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Generate broker-free Dynamic Universe shadow snapshot artifacts from an approved "
                    "offline OHLCV input dir. No live provider / broker calls; no production DB reads.")
    p.add_argument("--input", required=True,
                   help="approved input dir (approval.json + ohlcv.json|ohlcv.jsonl)")
    p.add_argument("--out", required=True,
                   help="output root; writes local_bars/, completed_bars/, MANIFEST.json (NO default)")
    p.add_argument("--as-of", default=None, help="pin the completed session date YYYY-MM-DD (default: "
                                                 "each instrument's latest bar; all must agree)")
    p.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    p.add_argument("--bar-close-utc", default=_DEFAULT_BAR_CLOSE_UTC,
                   help='time-of-day for bar_end_time, e.g. "21:00:00Z" (default midnight, non-future safe)')
    p.add_argument("--min-bars", type=int, default=MIN_BARS)
    p.add_argument("--generated-at", default=None, help="override audit timestamp (for reproducibility)")
    p.add_argument("--generated-by", default=None)
    p.add_argument("--readonly", action="store_true", help="chmod 0444 the emitted files (opt-in)")
    args = p.parse_args(argv)

    try:
        manifest = generate(
            args.input, args.out, as_of=args.as_of, timeframe=args.timeframe,
            bar_close=args.bar_close_utc, min_bars=args.min_bars,
            generated_at=args.generated_at, generated_by=args.generated_by, readonly=args.readonly)
    except (GenError, json.JSONDecodeError, OSError) as e:
        print(f"GEN_FAILED: {e}", file=sys.stderr)
        return 2
    print(f"GEN_OK: {manifest['instrument_count']} instrument(s) as-of {manifest['trading_date']} "
          f"→ {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
