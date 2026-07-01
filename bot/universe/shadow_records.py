"""Dynamic Universe — offline snapshot RECORDS / SEED seam (broker-free, explicit-config-only).

The shadow scheduler's per-cycle entry (``DailyUniverseScheduler.maybe_run(canonical_records)``)
needs a list of *canonical shadow records* to gate (post-close + completed-bar availability) and to
hand the evaluator as ``only_ids``. Until now the runtime source of that list was a documented
placeholder returning ``[]`` (``TradingBot._shadow_canonical_records``), so the shadow path could
never advance even with valid providers. This module resolves that seam.

``SnapshotShadowRecordsSource`` builds canonical shadow records from the SAME approved, local,
read-only snapshot files the two providers already consume (a completed-bar availability snapshot +
a local-bars OHLCV snapshot), and NOTHING else. It:

  * imports NO broker / IBKR / IG / EODHD / live market-data / FX / portfolio API and constructs no
    live client — it reads local JSON/JSONL files ONLY;
  * opens every snapshot file strictly READ-ONLY (mode ``"r"``, never write) and creates no file,
    directory, or database;
  * reuses the already-reviewed path-safety + parsing helpers of the sibling providers
    (``validate_source_path`` / ``_parse_records_from_text`` / ``PRODUCTION_DB_BASENAMES``), so a
    configured OR planted-child path that is (or resolves to) a production DB basename is rejected;
  * is INJECTED only and is constructed solely through the flag-gated, fail-closed factory
    ``build_shadow_records_source`` — there is NO live default and NO production source;
  * enforces the R2A-1 canonical identity co-dependency (design §4.3): a record is emitted ONLY when
    its local-bars row and its completed-bar row AGREE on the full ``(canonical_instrument_id,
    instrument_uid, listing_uid)`` triple. Identity is the R2A-1 canonical id, never a ticker/symbol;
  * fails CLOSED: a missing/unsafe/not-found source yields an EMPTY record list (a stable
    ``source_reason``); a per-instrument validation failure DROPS that instrument (a stable reason
    under ``excluded``) — it is never silently included. No exception escapes to the scheduler.

Registry seeding into a shadow DB (``seed_registry(shadow_db_path)``) and any snapshot generation
remain SEPARATE, out-of-band, gated steps (Gate E/F + data-ops); this module opens no database and
generates no snapshot — it only READS approved local files to build the in-memory record list. See
``docs/dynamic_universe_offline_snapshot_seed_seam_completion.md``.

This module is NOT wired into any runtime startup that runs in production today; the ``main.py`` seam
constructs it only when the master flag is on AND both explicit snapshot sources are configured
(neither is true in production).
"""
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# Reuse the already-reviewed single-source-of-truth path-safety + parsing helpers (no duplication;
# identical semantics to the two providers). PRODUCTION_DB_BASENAMES lives in shadow_runtime.py and
# is re-used transitively through the completed-bar provider.
from bot.universe.local_bar_provider import (
    PRODUCTION_DB_BASENAMES,
    SOURCE_PATH_MISSING,
    SOURCE_PATH_UNSAFE,
    _parse_bar_end_time,
    _parse_records_from_text,
    validate_source_path,
)

logger = logging.getLogger("universe.shadow_records")

# Daily timeframe the shadow evaluator's indicators use.
DEFAULT_TIMEFRAME = "1d"

# Snapshot format version this seam understands. Kept in lock-step with the two providers
# (both pin schema_version == 1); bump together on a format change.
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})

# Minimum completed daily bars required per instrument: the evaluator computes sma200 (evaluator.py),
# so a local-bars row with fewer than 200 bars can never yield indicators. ≥200 is the hard floor.
MIN_BARS = 200

# Required OHLCV columns on each local-bars bar (volume required — the evaluator computes ADV20).
_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")

# ── Stable fail-closed reason codes (non-sensitive; safe to log/persist as shadow evidence) ──
SNAPSHOT_SOURCE_MISSING = "snapshot_source_missing"        # a source path is absent/empty in config
SNAPSHOT_SOURCE_UNSAFE = "snapshot_source_unsafe"          # a source path is/resolves-to a prod DB
SNAPSHOT_SOURCE_NOT_FOUND = "snapshot_source_not_found"    # a configured source file/dir is absent
SNAPSHOT_SOURCE_UNREADABLE = "snapshot_source_unreadable"  # an OS error reading a source file
SNAPSHOT_MALFORMED = "snapshot_malformed"                  # a source file is not parseable JSON/JSONL
SNAPSHOT_SCHEMA_UNSUPPORTED = "snapshot_schema_unsupported"
SNAPSHOT_IDENTITY_MISMATCH = "snapshot_identity_mismatch"  # bars vs completed identity disagree/absent
SNAPSHOT_TRADING_DATE_MISMATCH = "snapshot_trading_date_mismatch"
SNAPSHOT_TIMEFRAME_MISMATCH = "snapshot_timeframe_mismatch"
SNAPSHOT_FUTURE_BAR = "snapshot_future_bar"
SNAPSHOT_PROOF_MISSING = "snapshot_proof_missing"          # missing version/hash, or no completed row
SNAPSHOT_BARS_INSUFFICIENT = "snapshot_bars_insufficient"  # < MIN_BARS bars
SNAPSHOT_OHLCV_INVALID = "snapshot_ohlcv_invalid"          # non-numeric / bool / malformed OHLCV
SNAPSHOT_PROVIDER_ERROR = "snapshot_provider_error"        # defensive: unexpected error, fail closed

# Map the sibling path helper's reasons onto this seam's stable source reasons.
_PATH_REASON_MAP = {
    SOURCE_PATH_MISSING: SNAPSHOT_SOURCE_MISSING,
    SOURCE_PATH_UNSAFE: SNAPSHOT_SOURCE_UNSAFE,
}


@dataclass(frozen=True)
class ShadowRecordsResult:
    """Structured outcome of building canonical shadow records from the snapshots.

    ``records`` is the list of fully-proven canonical shadow records (each carrying the R2A-1
    identity triple + currency) the scheduler consumes; it is possibly empty. ``source_reason`` is a
    stable, source-level fail-closed reason when the WHOLE build could not proceed (missing/unsafe/
    not-found/unparseable source) — in that case ``records`` is empty. ``excluded`` maps a per-
    instrument identity key to the stable reason that DROPPED it (fail-closed, never included). This
    is the structured sibling of the plain ``__call__`` runtime contract, so a reason is testable and
    loggable while the runtime seam keeps returning a plain ``list``."""
    records: list = field(default_factory=list)
    source_reason: Optional[str] = None
    excluded: dict = field(default_factory=dict)


def build_shadow_records_source(*, bars_source, completed_source,
                                timeframe: str = DEFAULT_TIMEFRAME, now_fn=None
                                ) -> Optional["SnapshotShadowRecordsSource"]:
    """Fail-closed factory: construct the records source ONLY when BOTH snapshot sources are present
    and path-safe. Returns ``None`` (logging a stable reason; constructing NOTHING, reading NOTHING)
    when either source is missing or unsafe — so the caller's records fn stays ``None`` and
    ``TradingBot._shadow_canonical_records`` keeps returning ``[]`` (the inert placeholder posture).

    Existence / readability / schema / identity are NOT checked here (no filesystem read); they are
    validated fail-closed at call time. ``now_fn`` is an injectable UTC clock (tests pass a fixed
    clock; default is the real ``datetime.now(UTC)``)."""
    for label, src in (("bars", bars_source), ("completed", completed_source)):
        reason = validate_source_path(src)
        if reason is not None:
            logger.info("[ShadowRecords] not constructed: %s source %s",
                        label, _PATH_REASON_MAP.get(reason, reason))
            return None
    return SnapshotShadowRecordsSource(str(bars_source), str(completed_source),
                                       timeframe=timeframe, now_fn=now_fn)


class SnapshotShadowRecordsSource:
    """Builds canonical shadow records from a local-bars snapshot + a completed-bar snapshot.

    Constructed only via ``build_shadow_records_source`` (both paths already validated safe).
    ``__call__`` returns the plain ``list`` the runtime seam expects; ``build`` returns the structured
    ``ShadowRecordsResult`` (records + reasons) for tests/logging. Both read the snapshots strictly
    READ-ONLY and NEVER raise, NEVER write, NEVER open a database, NEVER call a broker."""

    # Declared non-live so any downstream ``is_live`` inspection treats it as broker-free.
    is_live = False

    def __init__(self, bars_source: str, completed_source: str, *,
                 timeframe: str = DEFAULT_TIMEFRAME, now_fn=None):
        self._bars_source = str(bars_source)
        self._completed_source = str(completed_source)
        self._timeframe = str(timeframe)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def __call__(self) -> list:
        """Runtime records contract: ``Callable[[], list]``. Never raises; fail-closed → ``[]``/subset."""
        return self.build().records

    def build(self) -> ShadowRecordsResult:
        """Fail-closed build carrying stable reasons (see ``ShadowRecordsResult``). Never raises."""
        try:
            return self._build()
        except Exception:  # pragma: no cover - defensive: no exception may escape to the scheduler
            logger.warning("[ShadowRecords] unexpected error — failing closed")
            return ShadowRecordsResult(records=[], source_reason=SNAPSHOT_PROVIDER_ERROR)

    def _build(self) -> ShadowRecordsResult:
        bars_rows, reason = _load_snapshot_records(self._bars_source)
        if reason is not None:
            return ShadowRecordsResult(records=[], source_reason=reason)
        completed_rows, reason = _load_snapshot_records(self._completed_source)
        if reason is not None:
            return ShadowRecordsResult(records=[], source_reason=reason)

        # Index completed-bar availability rows by each identity they carry (uid and listing), so a
        # bars row can be matched by either half of the R2A-1 identity.
        completed_by_iuid, completed_by_luid = {}, {}
        for cr in completed_rows:
            if not isinstance(cr, dict):
                continue
            iuid, luid = cr.get("instrument_uid"), cr.get("listing_uid")
            if iuid:
                completed_by_iuid.setdefault(str(iuid), cr)
            if luid:
                completed_by_luid.setdefault(str(luid), cr)

        records, excluded = [], {}
        seen_cids = set()
        # The local-bars snapshot defines the candidate universe (each instrument with ≥200 bars).
        for br in bars_rows:
            if not isinstance(br, dict) or "bars" not in br:
                continue  # foreign/availability-only rows are not bars rows — skip silently
            cid = br.get("canonical_instrument_id")
            iuid = br.get("instrument_uid")
            luid = br.get("listing_uid")
            key = str(cid or iuid or luid or "?")
            # ── R2A-1 identity: the seam requires the FULL triple on the bars row so the record can
            # carry every id each downstream consumer keys on (scheduler cid; completed-bar gate
            # uid/listing; bars provider cid). No ticker/symbol fallback. ──
            if not cid or not iuid or not luid:
                excluded[key] = SNAPSHOT_IDENTITY_MISMATCH
                continue
            cid, iuid, luid = str(cid), str(iuid), str(luid)
            if cid in seen_cids:
                continue  # first proven row per cid wins; ignore duplicates
            bad = self._validate_bars_row(br)
            if bad is not None:
                excluded[cid] = bad
                continue
            # ── locate + validate the matching completed-bar availability row ──
            cr = completed_by_iuid.get(iuid) or completed_by_luid.get(luid)
            if cr is None:
                excluded[cid] = SNAPSHOT_PROOF_MISSING  # no completed-bar availability proof
                continue
            if not self._identities_agree(br, cr, cid, iuid, luid):
                excluded[cid] = SNAPSHOT_IDENTITY_MISMATCH
                continue
            bad = self._validate_completed_row(cr)
            if bad is not None:
                excluded[cid] = bad
                continue

            seen_cids.add(cid)
            records.append({
                "canonical_instrument_id": cid,
                "instrument_uid": iuid,
                "listing_uid": luid,
                "currency": br.get("currency"),
                "primary_gateway": "IBKR",
                "display_symbol": br.get("display_symbol"),
                "source": str(br.get("source")),
            })
        return ShadowRecordsResult(records=records, excluded=excluded)

    # ── per-row validation (each returns a stable reason string, or None if valid) ──
    def _validate_bars_row(self, r: dict) -> Optional[str]:
        if r.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            return SNAPSHOT_SCHEMA_UNSUPPORTED
        if str(r.get("timeframe")) != self._timeframe:
            return SNAPSHOT_TIMEFRAME_MISMATCH
        trading_date = r.get("trading_date")
        bar_end_time = r.get("bar_end_time")
        source = r.get("source")
        version = r.get("version") or r.get("content_hash")
        if not trading_date or not bar_end_time or not source or not version:
            return SNAPSHOT_PROOF_MISSING
        end_dt = _parse_bar_end_time(bar_end_time)
        if end_dt is None:
            return SNAPSHOT_MALFORMED
        if str(bar_end_time)[:10] != str(trading_date):
            return SNAPSHOT_TRADING_DATE_MISMATCH
        if end_dt > self._now_fn():
            return SNAPSHOT_FUTURE_BAR
        return self._validate_ohlcv(r.get("bars"))

    @staticmethod
    def _validate_ohlcv(bars) -> Optional[str]:
        if not isinstance(bars, list) or len(bars) < MIN_BARS:
            return SNAPSHOT_BARS_INSUFFICIENT
        for b in bars:
            if not isinstance(b, dict):
                return SNAPSHOT_OHLCV_INVALID
            for f in _OHLCV_FIELDS:
                v = b.get(f)
                # bool is a subclass of int but is never a valid price/volume → reject.
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    return SNAPSHOT_OHLCV_INVALID
        return None

    def _validate_completed_row(self, r: dict) -> Optional[str]:
        if r.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            return SNAPSHOT_SCHEMA_UNSUPPORTED
        if str(r.get("timeframe")) != self._timeframe:
            return SNAPSHOT_TIMEFRAME_MISMATCH
        if not bool(r.get("available")):
            return SNAPSHOT_PROOF_MISSING  # availability proof absent → not a usable completed bar
        trading_date = r.get("trading_date")
        bar_end_time = r.get("bar_end_time")
        source = r.get("source")
        version = r.get("version") or r.get("content_hash")
        if not trading_date or not bar_end_time or not source or not version:
            return SNAPSHOT_PROOF_MISSING
        end_dt = _parse_bar_end_time(bar_end_time)
        if end_dt is None:
            return SNAPSHOT_MALFORMED
        if str(bar_end_time)[:10] != str(trading_date):
            return SNAPSHOT_TRADING_DATE_MISMATCH
        if end_dt > self._now_fn():
            return SNAPSHOT_FUTURE_BAR
        return None

    @staticmethod
    def _identities_agree(br: dict, cr: dict, cid: str, iuid: str, luid: str) -> bool:
        """True only when the completed-bar row agrees with the bars row on every id it carries.
        The completed-bar row need not carry ``canonical_instrument_id`` (the completed-bar provider
        ignores it), but if present it MUST equal the bars row's; its uid/listing MUST match."""
        if str(cr.get("instrument_uid")) != iuid:
            return False
        if str(cr.get("listing_uid")) != luid:
            return False
        cr_cid = cr.get("canonical_instrument_id")
        if cr_cid is not None and str(cr_cid) != cid:
            return False
        return True


def _load_snapshot_records(source_path: str):
    """Read snapshot records READ-ONLY from a file or a directory of ``.json``/``.jsonl`` files.

    Returns ``(records, None)`` or ``(None, source_reason)``. Mirrors the providers' loader: it
    re-validates EACH child path (basename + symlink-resolved basename) against the production-DB
    basename set before any open, so a planted child can never be read. Never raises; never writes."""
    path = str(source_path)
    if not os.path.exists(path):
        return None, SNAPSHOT_SOURCE_NOT_FOUND
    if os.path.isdir(path):
        try:
            names = sorted(n for n in os.listdir(path)
                           if n.endswith(".json") or n.endswith(".jsonl"))
        except OSError:
            return None, SNAPSHOT_SOURCE_UNREADABLE
        files = [os.path.join(path, n) for n in names]
    else:
        files = [path]

    records = []
    for fp in files:
        if os.path.basename(os.path.normpath(fp)) in PRODUCTION_DB_BASENAMES:
            return None, SNAPSHOT_SOURCE_UNSAFE
        if os.path.basename(os.path.realpath(fp)) in PRODUCTION_DB_BASENAMES:
            return None, SNAPSHOT_SOURCE_UNSAFE
        try:
            with open(fp, "r") as fh:  # strictly READ-ONLY
                text = fh.read()
        except OSError:
            return None, SNAPSHOT_SOURCE_UNREADABLE
        recs, reason = _parse_records_from_text(text)
        if reason is not None:
            return None, SNAPSHOT_MALFORMED
        records.extend(recs)
    return records, None
