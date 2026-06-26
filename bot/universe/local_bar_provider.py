"""Dynamic Universe — BLOCKER-W1 resolution: a concrete, broker-free ``CompletedBarProvider``.

``LocalCompletedBarSnapshotProvider`` answers the shadow path's "is the COMPLETED bar available,
and what proves it?" question from an EXPLICITLY-configured, local, read-only, immutable daily-bar
*snapshot* (a JSON / JSONL file, or a directory of them) that is materialized out-of-band. It:

  * imports NO broker / IBKR / IG / EODHD / live market-data API and constructs no live client;
  * fetches NO live data and opens the snapshot strictly READ-ONLY (mode ``"r"``, never write);
  * touches NO production database (``positions.db`` / ``regime.db`` / ``backtest.db`` /
    ``universe.db`` / order/execution stores) and never the production default DB path;
  * is INJECTED only and is constructed solely through the flag-gated, fail-closed factory
    ``build_local_completed_bar_provider`` — there is NO live default;
  * declares ``is_live = False`` so ``validate_shadow_config`` accepts it (a live provider would
    set ``is_live = True`` and be rejected pending separate approval);
  * fails CLOSED: every error resolves to ``CompletedBarSnapshot(available=False, reason=<code>)``
    and NO exception escapes into the scheduler/evaluator.

This module is NOT wired into any runtime startup that runs in production today; the ``main.py``
seam constructs it only when the master flag is on AND an explicit snapshot source is configured
(neither is true in production). See ``docs/dynamic_universe_completed_bar_provider_completion.md``.
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from bot.universe.bar_provider import CompletedBarSnapshot, DEFAULT_TIMEFRAME
from bot.universe.db import REPO_ROOT
# Reuse the single source of truth for dangerous DB basenames (no duplication).
from bot.universe.shadow_runtime import PRODUCTION_DB_BASENAMES

logger = logging.getLogger("universe.local_bar_provider")

# Snapshot format version this provider understands. Bump (and extend SUPPORTED) on format change.
SNAPSHOT_SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})

# ── Stable fail-closed reason codes (non-sensitive; safe to log/persist as shadow evidence) ──
SOURCE_PATH_MISSING = "source_path_missing"
SOURCE_PATH_UNSAFE = "source_path_unsafe"
SOURCE_PATH_NOT_FOUND = "source_path_not_found"
SOURCE_PATH_UNREADABLE = "source_path_unreadable"
SNAPSHOT_MALFORMED = "snapshot_malformed"
SNAPSHOT_SCHEMA_UNSUPPORTED = "snapshot_schema_unsupported"
INSTRUMENT_MISMATCH = "instrument_mismatch"
TRADING_DATE_MISMATCH = "trading_date_mismatch"
TIMEFRAME_MISMATCH = "timeframe_mismatch"
BAR_NOT_COMPLETED = "bar_not_completed"
BAR_FUTURE_TIMESTAMP = "bar_future_timestamp"
BAR_UNAVAILABLE = "bar_unavailable"
BAR_PROOF_MISSING = "bar_proof_missing"
PROVIDER_ERROR = "provider_error"


def _na(record: Optional[dict], trading_date, timeframe, reason: str) -> CompletedBarSnapshot:
    """Build a fail-closed (not-available) snapshot carrying a stable reason."""
    rec = record or {}
    return CompletedBarSnapshot(
        trading_date=str(trading_date), timeframe=str(timeframe), available=False,
        instrument_uid=rec.get("instrument_uid"), listing_uid=rec.get("listing_uid"),
        reason=reason)


def validate_source_path(source_path) -> Optional[str]:
    """PURE snapshot-source path-safety validation (no snapshot open/read; resolves symlinks only).

    Returns a stable reason string when the path is missing or unsafe, else ``None``:
      * ``source_path_missing`` — absent/empty.
      * ``source_path_unsafe``  — the configured basename is a production DB basename, OR the path
        (after symlink resolution) resolves to a production DB basename. The plain-basename check
        runs FIRST so an obvious production path (e.g. ``/root/trading/backtest.db``) is rejected
        WITHOUT resolving/stat-ing it.

    This mirrors the ``validate_shadow_config`` (pure) vs ``build_shadow_scheduler`` (lazy build)
    split in ``shadow_runtime.py`` so the specific reason is unit-testable."""
    if not source_path or not str(source_path).strip():
        return SOURCE_PATH_MISSING
    p = str(source_path)
    if os.path.basename(os.path.normpath(p)) in PRODUCTION_DB_BASENAMES:
        return SOURCE_PATH_UNSAFE
    # Only now resolve symlinks (a clean basename may still be a link to a production DB).
    if os.path.basename(os.path.realpath(p)) in PRODUCTION_DB_BASENAMES:
        return SOURCE_PATH_UNSAFE
    return None


def build_local_completed_bar_provider(source_path, *, now_fn=None
                                       ) -> Optional["LocalCompletedBarSnapshotProvider"]:
    """Fail-closed factory: construct the provider ONLY for a present, safe snapshot source.

    Returns ``None`` (logging a stable reason; constructing NOTHING) when the source is missing or
    unsafe — so the caller's ``completed_bar_provider`` stays ``None`` and ``validate_shadow_config``
    fails closed at ``completed_bar_provider_missing``. Existence/readability/format are NOT checked
    here (no filesystem read) — they are validated fail-closed at ``completed_bar`` time. ``now_fn``
    is an injectable UTC clock (tests pass a fixed clock; default is real ``datetime.now(UTC)``)."""
    reason = validate_source_path(source_path)
    if reason is not None:
        logger.info("[CompletedBarProvider] not constructed: %s", reason)
        return None
    return LocalCompletedBarSnapshotProvider(str(source_path), now_fn=now_fn)


def _parse_records_from_text(text: str):
    """Parse one snapshot file's text into a list of record dicts.

    Accepts the minimal shapes only: a single JSON object, a JSON array of objects, or JSONL (one
    JSON object per non-blank line). Returns ``(records, None)`` or ``(None, reason)``."""
    s = (text or "").strip()
    if not s:
        return [], None
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        # Fall back to JSONL: every non-blank line must be a JSON object.
        recs = []
        for line in s.splitlines():
            ls = line.strip()
            if not ls:
                continue
            try:
                r = json.loads(ls)
            except json.JSONDecodeError:
                return None, SNAPSHOT_MALFORMED
            if not isinstance(r, dict):
                return None, SNAPSHOT_MALFORMED
            recs.append(r)
        return recs, None
    if isinstance(obj, dict):
        return [obj], None
    if isinstance(obj, list):
        if not all(isinstance(r, dict) for r in obj):
            return None, SNAPSHOT_MALFORMED
        return obj, None
    return None, SNAPSHOT_MALFORMED


def _parse_bar_end_time(value) -> Optional[datetime]:
    """Parse a bar_end_time that is either a ``YYYY-MM-DD`` date or an ISO-8601 datetime
    (``...Z`` accepted). Returns a tz-aware UTC datetime, or ``None`` if unparseable."""
    s = str(value)
    try:
        if len(s) == 10:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _identity_matches(rec: dict, req_iuid, req_luid) -> bool:
    """True when the snapshot record matches the requested canonical identity (uid/listing).
    Identity is the R2A-1 canonical id, never a ticker; an absent requested id never matches."""
    if req_iuid and rec.get("instrument_uid") == req_iuid:
        return True
    if req_luid and rec.get("listing_uid") == req_luid:
        return True
    return False


class LocalCompletedBarSnapshotProvider:
    """Concrete broker-free ``CompletedBarProvider`` over a local, read-only snapshot source.

    Constructed only via ``build_local_completed_bar_provider`` (path already validated safe).
    ``completed_bar`` reads the snapshot READ-ONLY, matches the request by canonical identity →
    trading_date → timeframe, and returns an ``available=True`` snapshot ONLY for a recognized,
    available, fully-proven, completed (non-future) bar covering the requested session. Every other
    outcome is a stable fail-closed reason. NEVER raises, NEVER writes, NEVER calls a broker."""

    # Declared non-live so validate_shadow_config accepts it (a live source would set this True).
    is_live = False

    def __init__(self, source_path: str, *, now_fn=None):
        self._source_path = str(source_path)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def completed_bar(self, *, record: dict, trading_date: str,
                      timeframe: str = DEFAULT_TIMEFRAME) -> CompletedBarSnapshot:
        """Fail-closed completed-bar availability answer (see class docstring). Never raises."""
        try:
            return self._completed_bar(record=record, trading_date=trading_date,
                                       timeframe=timeframe)
        except Exception:  # pragma: no cover - defensive: no exception may escape
            logger.warning("[CompletedBarProvider] unexpected error — failing closed")
            return _na(record, trading_date, timeframe, PROVIDER_ERROR)

    def _completed_bar(self, *, record, trading_date, timeframe) -> CompletedBarSnapshot:
        td, tf = str(trading_date), str(timeframe)

        records, reason = self._load_records()
        if reason is not None:
            return _na(record, td, tf, reason)

        req = record or {}
        # Match by identity → trading_date → timeframe, with a distinct reason at each narrowing.
        id_matches = [r for r in records
                      if _identity_matches(r, req.get("instrument_uid"), req.get("listing_uid"))]
        if not id_matches:
            return _na(record, td, tf, INSTRUMENT_MISMATCH)
        date_matches = [r for r in id_matches if str(r.get("trading_date")) == td]
        if not date_matches:
            return _na(record, td, tf, TRADING_DATE_MISMATCH)
        tf_matches = [r for r in date_matches if str(r.get("timeframe")) == tf]
        if not tf_matches:
            return _na(record, td, tf, TIMEFRAME_MISMATCH)
        r = tf_matches[0]

        # Validate the SELECTED record only (no cross-record poisoning).
        if r.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            return _na(record, td, tf, SNAPSHOT_SCHEMA_UNSUPPORTED)
        if not bool(r.get("available")):
            return _na(record, td, tf, BAR_UNAVAILABLE)
        bar_end_time = r.get("bar_end_time")
        source = r.get("source")
        version = r.get("version") or r.get("content_hash")
        if not bar_end_time or not source or not version:
            return _na(record, td, tf, BAR_PROOF_MISSING)
        end_dt = _parse_bar_end_time(bar_end_time)
        if end_dt is None:
            return _na(record, td, tf, SNAPSHOT_MALFORMED)
        # The completed bar must end on the requested session date (not a different/earlier day).
        if str(bar_end_time)[:10] != td:
            return _na(record, td, tf, BAR_NOT_COMPLETED)
        # And it must not be in the future relative to the injected/real clock.
        if end_dt > self._now_fn():
            return _na(record, td, tf, BAR_FUTURE_TIMESTAMP)

        return CompletedBarSnapshot(
            trading_date=td, timeframe=tf, available=True,
            instrument_uid=r.get("instrument_uid"), listing_uid=r.get("listing_uid"),
            bar_end_time=str(bar_end_time), source=str(source), version=str(version),
            reason=None)

    def _load_records(self):
        """Read snapshot records READ-ONLY from the configured file or directory of files.
        Returns ``(records, None)`` or ``(None, reason)``. Never raises; never writes."""
        path = self._source_path
        if not os.path.exists(path):
            return None, SOURCE_PATH_NOT_FOUND
        if os.path.isdir(path):
            try:
                names = sorted(n for n in os.listdir(path)
                               if n.endswith(".json") or n.endswith(".jsonl"))
            except OSError:
                return None, SOURCE_PATH_UNREADABLE
            files = [os.path.join(path, n) for n in names]
        else:
            files = [path]

        records = []
        for fp in files:
            try:
                with open(fp, "r") as fh:  # strictly READ-ONLY
                    text = fh.read()
            except OSError:
                return None, SOURCE_PATH_UNREADABLE
            recs, reason = _parse_records_from_text(text)
            if reason is not None:
                return None, reason
            records.extend(recs)
        return records, None
