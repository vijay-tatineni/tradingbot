"""Dynamic Universe — BARS_PROVIDER resolution: a concrete, broker-free ``bars_provider``.

``LocalBarsSnapshotProvider`` answers the shadow evaluator's "what is this instrument's recent
completed OHLCV history (for indicator/eligibility computation)?" question from an
EXPLICITLY-configured, local, read-only, immutable daily-bar *snapshot* (a JSON / JSONL file, or a
directory of them) that is materialized out-of-band. It is the sibling of
``LocalCompletedBarSnapshotProvider`` (the W1 completed-bar *availability* boundary) and reuses its
path-safety and parsing helpers, but answers a DIFFERENT question and carries a DIFFERENT record
type (an OHLCV array, not a per-date availability row).

It:
  * imports NO broker / IBKR / IG / EODHD / live market-data API and constructs no live client;
  * does NOT import ``backtest.breakout_strategy`` — it SUPPLIES bars; it never computes indicators
    (the evaluator calls ``compute_indicators`` on the returned frame);
  * fetches NO live data and opens the snapshot strictly READ-ONLY (mode ``"r"``, never write);
  * touches NO production database (``positions.db`` / ``regime.db`` / ``backtest.db`` /
    ``universe.db`` / ``universe_shadow.db`` / order/execution stores) and never a production default;
  * is INJECTED only and is constructed solely through the flag-gated, fail-closed factory
    ``build_local_bars_provider`` — there is NO live default;
  * declares ``is_live = False`` so ``validate_shadow_config`` accepts it (a live source would set
    ``is_live = True`` and be rejected pending separate approval);
  * fails CLOSED: every error resolves to ``None`` (the evaluator's ``self.bars_provider(rec) or {}``
    then yields no bars → the instrument is blocked) and NO exception escapes into the evaluator.

CONTRACT NOTE (spec-vs-code, per CLAUDE.md). The runtime ``bars_provider`` is
``Callable[[dict], Optional[dict]]`` — the evaluator calls ``self.bars_provider(rec)`` with ONLY the
canonical record (``bot/universe/evaluator.py:295``); it passes NO ``trading_date``/``timeframe``
argument. So, unlike the completed-bar provider (which validates against a *requested*
date/timeframe), this provider validates the snapshot record's OWN declared metadata for internal
consistency: ``timeframe`` must equal the provider's configured timeframe, and the most recent bar's
``bar_end_time`` must be a completed (``[:10] == trading_date``), non-future session. The return
payload shape (``{"bars": <DataFrame>, "corp_action_status", "sector", ...}``) matches the de-facto
contract the evaluator already consumes (``tests/universe/test_evaluator.py`` ``_uptrend_source`` /
``SpyProvider``). See ``docs/dynamic_universe_local_bars_provider_completion.md``.

This module is NOT wired into any runtime startup that runs in production today; the ``main.py``
seam constructs it only when the master flag is on AND an explicit snapshot source is configured
(neither is true in production).
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# Reuse the already-reviewed, single-source-of-truth path-safety + parsing helpers from the sibling
# completed-bar provider (no duplication; identical semantics). PRODUCTION_DB_BASENAMES itself lives
# in shadow_runtime.py and is re-used transitively.
from bot.universe.local_bar_provider import (
    PRODUCTION_DB_BASENAMES,
    SOURCE_PATH_MISSING,
    SOURCE_PATH_UNSAFE,
    SOURCE_PATH_NOT_FOUND,
    SOURCE_PATH_UNREADABLE,
    SNAPSHOT_MALFORMED,
    _parse_bar_end_time,
    _parse_records_from_text,
    validate_source_path,
)

logger = logging.getLogger("universe.local_bars_provider")

# Default eligibility timeframe (daily). The shadow evaluator's indicators are daily.
DEFAULT_TIMEFRAME = "1d"

# Snapshot format version this provider understands. Bump (and extend SUPPORTED) on format change.
SNAPSHOT_SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})

# Required OHLCV columns on each bar (volume is required — the evaluator computes ADV20 = close*vol).
_OHLCV_FIELDS = ("open", "high", "low", "close", "volume")

# ── Stable fail-closed reason codes (non-sensitive; safe to log/persist as shadow evidence) ──
# Source/path reasons are reused from the sibling provider (imported above).
SNAPSHOT_SCHEMA_UNSUPPORTED = "snapshot_schema_unsupported"
INSTRUMENT_MISMATCH = "instrument_mismatch"
TIMEFRAME_MISMATCH = "timeframe_mismatch"
TRADING_DATE_MISMATCH = "trading_date_mismatch"     # declared trading_date vs last bar_end_time
BAR_FUTURE_TIMESTAMP = "bar_future_timestamp"
BAR_PROOF_MISSING = "bar_proof_missing"
BARS_MISSING = "bars_missing"                        # no/empty bars array
BARS_MALFORMED = "bars_malformed"                    # bars not a list of OHLCV dicts / non-numeric
PROVIDER_ERROR = "provider_error"


@dataclass(frozen=True)
class LocalBarsResult:
    """Structured outcome of resolving one canonical record against the snapshot.

    ``payload`` is the bars dict the evaluator consumes (``{"bars": <DataFrame>, ...}``) and is
    populated ONLY when ``available`` is True; otherwise it is ``None`` and ``reason`` carries the
    stable fail-closed code. This is the structured sibling of ``__call__`` — it takes the SAME
    single canonical ``record`` the runtime passes (no request date/timeframe), so it introduces no
    request-based surface that the runtime never exercises; it exists so a stable reason is testable
    and loggable while ``__call__`` keeps the plain ``Optional[dict]`` runtime contract."""
    available: bool
    payload: Optional[dict] = None
    reason: Optional[str] = None


def _is_strict_bool(value, default: bool) -> bool:
    """Strict boolean read (P3-2 hardening): only a real ``bool`` is honored; anything else (e.g.
    the string ``"false"``, which is truthy) falls back to ``default`` rather than being coerced."""
    return value if isinstance(value, bool) else default


def _looks_like_bars_record(rec: dict) -> bool:
    """A bars record is recognized by carrying a ``bars`` key. Used to filter foreign records
    (e.g. completed-bar availability rows) out fail-closed if a source path is shared."""
    return isinstance(rec, dict) and ("bars" in rec)


def _identity_matches(snap_rec: dict, req: dict) -> bool:
    """True when the snapshot record matches the requested canonical identity.

    The PRIMARY key is ``canonical_instrument_id`` — that is the only identity the evaluator's
    record carries (``registry.all_canonical()`` over the ``canonical_instruments`` table, whose
    columns do NOT include ``instrument_uid``/``listing_uid``; those live in the separate R2A-1
    identity tables). ``canonical_instrument_id`` is a broker-neutral master id, never a ticker.
    ``instrument_uid``/``listing_uid`` are ALSO accepted when both sides carry them (forward-compat
    for a richer record). An absent requested id never matches."""
    req_cid = req.get("canonical_instrument_id")
    if req_cid and snap_rec.get("canonical_instrument_id") == req_cid:
        return True
    req_iuid = req.get("instrument_uid")
    if req_iuid and snap_rec.get("instrument_uid") == req_iuid:
        return True
    req_luid = req.get("listing_uid")
    if req_luid and snap_rec.get("listing_uid") == req_luid:
        return True
    return False


def build_local_bars_provider(source_path, *, timeframe: str = DEFAULT_TIMEFRAME, now_fn=None
                              ) -> Optional["LocalBarsSnapshotProvider"]:
    """Fail-closed factory: construct the provider ONLY for a present, safe snapshot source.

    Returns ``None`` (logging a stable reason; constructing NOTHING) when the source is missing or
    unsafe — so the caller's ``bars_provider`` stays ``None`` and ``validate_shadow_config`` fails
    closed at ``bars_provider_missing``. Existence/readability/format are NOT checked here (no
    filesystem read); they are validated fail-closed at call time. ``now_fn`` is an injectable UTC
    clock (tests pass a fixed clock; default is real ``datetime.now(UTC)``)."""
    reason = validate_source_path(source_path)
    if reason is not None:
        logger.info("[BarsProvider] not constructed: %s", reason)
        return None
    return LocalBarsSnapshotProvider(str(source_path), timeframe=timeframe, now_fn=now_fn)


class LocalBarsSnapshotProvider:
    """Concrete broker-free ``bars_provider`` over a local, read-only snapshot source.

    Constructed only via ``build_local_bars_provider`` (path already validated safe). ``__call__``
    reads the snapshot READ-ONLY, matches the canonical record by identity, validates the selected
    record (timeframe == configured · schema · proof · completed/non-future bar_end_time · a
    well-formed OHLCV array), and returns the evaluator's bars dict ONLY for a fully-proven record.
    Every other outcome is ``None`` (with a stable reason logged). NEVER raises, NEVER writes,
    NEVER calls a broker, NEVER computes indicators."""

    # Declared non-live so validate_shadow_config accepts it (a live source would set this True).
    is_live = False

    def __init__(self, source_path: str, *, timeframe: str = DEFAULT_TIMEFRAME, now_fn=None):
        self._source_path = str(source_path)
        self._timeframe = str(timeframe)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def __call__(self, record: dict) -> Optional[dict]:
        """Runtime ``bars_provider`` contract: ``Callable[[dict], Optional[dict]]``. Never raises."""
        return self.resolve(record).payload

    def resolve(self, record: dict) -> LocalBarsResult:
        """Fail-closed resolution carrying a stable reason (see ``LocalBarsResult``). Never raises."""
        try:
            return self._resolve(record)
        except Exception:  # pragma: no cover - defensive: no exception may escape to the evaluator
            logger.warning("[BarsProvider] unexpected error — failing closed")
            return LocalBarsResult(available=False, reason=PROVIDER_ERROR)

    def _resolve(self, record: dict) -> LocalBarsResult:
        records, reason = self._load_records()
        if reason is not None:
            return LocalBarsResult(available=False, reason=reason)

        req = record or {}
        # Match by canonical identity (R2A-1 uid/listing, never a ticker), then by the configured
        # timeframe; only bars-shaped records are eligible (foreign rows fail closed).
        id_matches = [r for r in records
                      if _looks_like_bars_record(r) and _identity_matches(r, req)]
        if not id_matches:
            return self._na(INSTRUMENT_MISMATCH)
        tf_matches = [r for r in id_matches if str(r.get("timeframe")) == self._timeframe]
        if not tf_matches:
            return self._na(TIMEFRAME_MISMATCH)
        # If several sessions are present, evaluate the most recent declared trading_date.
        r = max(tf_matches, key=lambda x: str(x.get("trading_date") or ""))

        if r.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            return self._na(SNAPSHOT_SCHEMA_UNSUPPORTED)

        trading_date = r.get("trading_date")
        bar_end_time = r.get("bar_end_time")
        source = r.get("source")
        version = r.get("version") or r.get("content_hash")
        if not trading_date or not bar_end_time or not source or not version:
            return self._na(BAR_PROOF_MISSING)

        end_dt = _parse_bar_end_time(bar_end_time)
        if end_dt is None:
            return self._na(SNAPSHOT_MALFORMED)
        # Internal consistency: the most recent bar must END on the declared session date …
        if str(bar_end_time)[:10] != str(trading_date):
            return self._na(TRADING_DATE_MISMATCH)
        # … and that session must not be in the future relative to the injected/real clock.
        if end_dt > self._now_fn():
            return self._na(BAR_FUTURE_TIMESTAMP)

        bars_df, breason = self._build_bars_frame(r.get("bars"))
        if breason is not None:
            return self._na(breason)

        payload = {
            "bars": bars_df,
            "corp_action_status": r.get("corp_action_status", "unavailable"),
            "sector": r.get("sector"),
            "price_unit": r.get("price_unit"),
            "currency": r.get("currency"),
            "spread": r.get("spread"),
            "fresh_bar": _is_strict_bool(r.get("fresh_bar"), True),
            "admin_paused": _is_strict_bool(r.get("admin_paused"), False),
            "source": str(source),
            "version": str(version),
            "trading_date": str(trading_date),
            "timeframe": self._timeframe,
            "bar_end_time": str(bar_end_time),
        }
        return LocalBarsResult(available=True, payload=payload, reason=None)

    def _na(self, reason: str) -> LocalBarsResult:
        return LocalBarsResult(available=False, reason=reason)

    @staticmethod
    def _build_bars_frame(bars):
        """Validate the OHLCV array and build the DataFrame the evaluator's ``compute_indicators``
        consumes. Returns ``(DataFrame, None)`` or ``(None, reason)``. Pandas is imported lazily so
        the module's import graph stays minimal; it is NOT a broker dependency."""
        if not isinstance(bars, list) or len(bars) == 0:
            return None, BARS_MISSING
        cols = {f: [] for f in _OHLCV_FIELDS}
        dates = []
        has_date = False
        for b in bars:
            if not isinstance(b, dict):
                return None, BARS_MALFORMED
            for f in _OHLCV_FIELDS:
                v = b.get(f)
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    # bool is a subclass of int but is never a valid price/volume → reject.
                    return None, BARS_MALFORMED
                cols[f].append(float(v))
            if "date" in b:
                has_date = True
            dates.append(b.get("date"))
        import pandas as pd  # lazy; broker-free
        data = {f: cols[f] for f in _OHLCV_FIELDS}
        if has_date:
            data = {"date": dates, **data}
        return pd.DataFrame(data), None

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
            # P3-1 hardening: revalidate EACH child path against production DB basenames before open
            # (not just the configured parent), so a planted child can never be read.
            if os.path.basename(os.path.normpath(fp)) in PRODUCTION_DB_BASENAMES:
                return None, SOURCE_PATH_UNSAFE
            if os.path.basename(os.path.realpath(fp)) in PRODUCTION_DB_BASENAMES:
                return None, SOURCE_PATH_UNSAFE
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
