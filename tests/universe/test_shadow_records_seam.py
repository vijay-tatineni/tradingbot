"""Dynamic Universe — offline snapshot RECORDS / SEED seam tests (broker-free, local files only).

Covers ``bot.universe.shadow_records`` (the fail-closed source that replaces the ``[]`` placeholder
in ``TradingBot._shadow_canonical_records``) and its ``main.py`` config integration:

  * feature-off → no records source constructed, no snapshot file read;
  * missing / unsafe / symlink-to-production-DB source → fail closed (None / stable source_reason);
  * missing completed-bar or local-bars snapshot, unsupported schema, malformed JSON, identity
    mismatch, trading_date / timeframe mismatch, future bar_end_time, missing proof/hash,
    insufficient (<200) bars, invalid OHLCV → each fails closed with a stable reason;
  * valid local fixtures → a non-empty canonical-record set is built AND the real shadow scheduler
    (with the real broker-free completed-bar provider over the SAME snapshot) receives those records;
  * no broker import; no production DB touch; no universe.db / universe_shadow.db creation.

Everything uses temporary local files only — no downloads, no broker, no live/production DB.
"""
import json
import os
from datetime import datetime, timezone

import pytest

import main
from bot.universe.bar_provider import as_bar_available_fn
from bot.universe.local_bar_provider import build_local_completed_bar_provider
from bot.universe.scheduler import DailyUniverseScheduler
from bot.universe.shadow_records import (
    MIN_BARS,
    SNAPSHOT_BARS_INSUFFICIENT,
    SNAPSHOT_FUTURE_BAR,
    SNAPSHOT_IDENTITY_MISMATCH,
    SNAPSHOT_MALFORMED,
    SNAPSHOT_OHLCV_INVALID,
    SNAPSHOT_PROOF_MISSING,
    SNAPSHOT_SCHEMA_UNSUPPORTED,
    SNAPSHOT_SOURCE_MISSING,
    SNAPSHOT_SOURCE_NOT_FOUND,
    SNAPSHOT_SOURCE_UNSAFE,
    SNAPSHOT_TIMEFRAME_MISMATCH,
    SNAPSHOT_TRADING_DATE_MISMATCH,
    SnapshotShadowRecordsSource,
    build_shadow_records_source,
)
from tests.universe._fixtures import ON, OFF

# A fixed, deterministic UTC clock (all fixture bars END on 2026-06-10, safely in the past).
NOW = datetime(2026, 6, 10, 23, 0, tzinfo=timezone.utc)
TD = "2026-06-10"
BET = "2026-06-10T21:00:00Z"

CID, IUID, LUID = "US_AAPL", "iuid-aapl", "luid-aapl"


def _ohlcv(n=MIN_BARS + 20):
    return [{"open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i,
             "close": 100.5 + i, "volume": 2_000_000.0} for i in range(n)]


def _bars_row(**over):
    r = {"schema_version": 1, "canonical_instrument_id": CID, "instrument_uid": IUID,
         "listing_uid": LUID, "timeframe": "1d", "trading_date": TD, "bar_end_time": BET,
         "currency": "USD", "source": "local_snapshot:test_v1", "version": "bars-v1",
         "bars": _ohlcv()}
    r.update(over)
    return r


def _completed_row(**over):
    r = {"schema_version": 1, "canonical_instrument_id": CID, "instrument_uid": IUID,
         "listing_uid": LUID, "timeframe": "1d", "trading_date": TD, "available": True,
         "bar_end_time": BET, "source": "local_snapshot:test_v1", "version": "cbp-v1"}
    r.update(over)
    return r


def _write(path, rows):
    path.write_text(json.dumps(rows if isinstance(rows, list) else rows, indent=2))
    return str(path)


def _sources(tmp_path, bars_rows=None, completed_rows=None):
    """Write bars + completed snapshot files; return (bars_path, completed_path)."""
    bars = _write(tmp_path / "local_bars.json",
                  bars_rows if bars_rows is not None else [_bars_row()])
    completed = _write(tmp_path / "completed_bars.json",
                       completed_rows if completed_rows is not None else [_completed_row()])
    return bars, completed


def _source(tmp_path, **kw):
    bars, completed = _sources(tmp_path, **kw)
    return SnapshotShadowRecordsSource(bars, completed, now_fn=lambda: NOW)


# ── valid path ────────────────────────────────────────────────────────────────
def test_valid_fixtures_build_nonempty_records(tmp_path):
    res = _source(tmp_path).build()
    assert res.source_reason is None and res.excluded == {}
    assert [r["canonical_instrument_id"] for r in res.records] == [CID]
    rec = res.records[0]
    assert rec["instrument_uid"] == IUID and rec["listing_uid"] == LUID
    assert rec["currency"] == "USD"
    # The plain runtime contract returns the same list.
    assert _source(tmp_path)() == res.records


def test_valid_fixtures_feed_real_shadow_scheduler(tmp_path):
    """End-to-end (broker-free): seam records → real DailyUniverseScheduler whose completed-bar gate
    is the real ``LocalCompletedBarSnapshotProvider`` over the SAME snapshot → evaluator receives the
    proven cids. A recording evaluator stands in for the DB-backed one (no registry/DB needed)."""
    bars, completed = _sources(tmp_path)
    records = SnapshotShadowRecordsSource(bars, completed, now_fn=lambda: NOW)()
    assert records, "seam must produce a non-empty record set"

    class _RecordingEvaluator:
        def __init__(self):
            self.calls = []

        def maybe_run(self, trading_date, only_ids=None):
            self.calls.append((trading_date, sorted(only_ids or [])))
            return {"ran": True, "trading_date": trading_date, "evaluated": len(only_ids or [])}

    ev = _RecordingEvaluator()
    cbp = build_local_completed_bar_provider(completed, now_fn=lambda: NOW)
    sched = DailyUniverseScheduler(ev, ON, now_fn=lambda: NOW,
                                   bar_available_fn=as_bar_available_fn(cbp))
    result = sched.maybe_run(records)
    assert result["scheduled_ids"] == [CID]
    assert result["missing_bar_skipped"] == []
    assert ev.calls == [(TD, [CID])]


def test_valid_fixtures_drive_real_evaluator_over_seeded_registry(tmp_path):
    """Correctness (not merely fail-closed): the emitted ``canonical_instrument_id`` is genuinely
    consumable. Full broker-free path — seam records → real DailyUniverseScheduler → real
    completed-bar provider (gate) → real ShadowEvaluator over a SEEDED tmp registry → real
    LocalBarsSnapshotProvider (bars) — must actually evaluate the instrument (``evaluated >= 1``),
    proving the seam's cid matches a registry-produced canonical row. CID == canonical_id(AAPL,USD,
    NASDAQ) == 'US_AAPL'. Uses only temporary local DBs/files (no production DB)."""
    from bot.universe.evaluator import ShadowEvaluator
    from bot.universe.local_bars_provider import build_local_bars_provider
    from bot.universe.registry import Registry
    from bot.universe.seed import canonical_id, seed_registry
    from tests.universe._fixtures import inst, write_configs

    assert CID == canonical_id("AAPL", "USD", "NASDAQ")
    bars, completed = _sources(tmp_path)
    records = SnapshotShadowRecordsSource(bars, completed, now_fn=lambda: NOW)()
    assert [r["canonical_instrument_id"] for r in records] == [CID]

    p1, p2 = write_configs(tmp_path, [inst("AAPL", currency="USD", exchange="NASDAQ")], [])
    db = str(tmp_path / "universe.db")  # a TEMP db in tmp_path — not a production path
    seed_registry(db, p1, p2)

    bars_provider = build_local_bars_provider(bars, now_fn=lambda: NOW)
    evaluator = ShadowEvaluator(Registry(db), bars_provider, ON, equity=100_000)
    cbp = build_local_completed_bar_provider(completed, now_fn=lambda: NOW)
    sched = DailyUniverseScheduler(evaluator, ON, now_fn=lambda: NOW,
                                   bar_available_fn=as_bar_available_fn(cbp))
    result = sched.maybe_run(records)
    assert result["scheduled_ids"] == [CID]
    assert result["ran"] is True and result["evaluated"] >= 1


# ── source-level fail-closed (whole build → empty + source_reason) ──────────────
def test_factory_missing_source_returns_none(tmp_path):
    _, completed = _sources(tmp_path)
    assert build_shadow_records_source(bars_source=None, completed_source=completed) is None
    assert build_shadow_records_source(bars_source="", completed_source=completed) is None


def test_factory_unsafe_source_returns_none(tmp_path):
    bars, completed = _sources(tmp_path)
    # A configured basename that IS a production DB is rejected without construction.
    assert build_shadow_records_source(
        bars_source=str(tmp_path / "backtest.db"), completed_source=completed) is None
    assert build_shadow_records_source(
        bars_source=bars, completed_source=str(tmp_path / "universe.db")) is None


def test_factory_missing_source_reasons_map_to_stable_codes(tmp_path):
    # The factory returns None for missing/unsafe (source-level); the source object surfaces the
    # NOT_FOUND / MISSING / UNSAFE stable codes at build time for an explicitly-constructed source.
    src = SnapshotShadowRecordsSource(str(tmp_path / "nope.json"),
                                      str(tmp_path / "also_nope.json"), now_fn=lambda: NOW)
    assert src.build().source_reason == SNAPSHOT_SOURCE_NOT_FOUND
    assert src.build().records == []


def test_symlink_to_production_db_fails_closed(tmp_path):
    # A clean-basename snapshot file that is actually a symlink to a production-DB basename is
    # rejected by the realpath child-path check → source-level fail closed, nothing read.
    prod = tmp_path / "backtest.db"
    prod.write_text("not really a db")
    link = tmp_path / "local_bars.json"
    os.symlink(prod, link)  # clean basename, but realpath resolves to backtest.db
    completed = _write(tmp_path / "completed_bars.json", [_completed_row()])
    src = SnapshotShadowRecordsSource(str(link), completed, now_fn=lambda: NOW)
    assert src.build().source_reason == SNAPSHOT_SOURCE_UNSAFE


def test_missing_completed_snapshot_file_fails_closed(tmp_path):
    bars = _write(tmp_path / "local_bars.json", [_bars_row()])
    src = SnapshotShadowRecordsSource(bars, str(tmp_path / "absent_completed.json"),
                                      now_fn=lambda: NOW)
    res = src.build()
    assert res.source_reason == SNAPSHOT_SOURCE_NOT_FOUND and res.records == []


def test_missing_bars_snapshot_file_fails_closed(tmp_path):
    completed = _write(tmp_path / "completed_bars.json", [_completed_row()])
    src = SnapshotShadowRecordsSource(str(tmp_path / "absent_bars.json"), completed,
                                      now_fn=lambda: NOW)
    res = src.build()
    assert res.source_reason == SNAPSHOT_SOURCE_NOT_FOUND and res.records == []


def test_malformed_snapshot_fails_closed(tmp_path):
    bars = tmp_path / "local_bars.json"
    bars.write_text("{not valid json ]")
    completed = _write(tmp_path / "completed_bars.json", [_completed_row()])
    src = SnapshotShadowRecordsSource(str(bars), completed, now_fn=lambda: NOW)
    assert src.build().source_reason == SNAPSHOT_MALFORMED


# ── per-instrument fail-closed (instrument dropped with a stable reason) ────────
@pytest.mark.parametrize("over,reason", [
    ({"schema_version": 2}, SNAPSHOT_SCHEMA_UNSUPPORTED),
    ({"timeframe": "1h"}, SNAPSHOT_TIMEFRAME_MISMATCH),
    ({"bar_end_time": "2026-06-11T21:00:00Z"}, SNAPSHOT_TRADING_DATE_MISMATCH),
    ({"trading_date": "", "bar_end_time": ""}, SNAPSHOT_PROOF_MISSING),
    ({"version": None, "content_hash": None}, SNAPSHOT_PROOF_MISSING),
    ({"bars": _ohlcv(MIN_BARS - 1)}, SNAPSHOT_BARS_INSUFFICIENT),
    ({"bars": [{"open": 1, "high": 1, "low": 1, "close": 1, "volume": True}] * MIN_BARS},
     SNAPSHOT_OHLCV_INVALID),
    ({"bars": [{"open": "x", "high": 1, "low": 1, "close": 1, "volume": 1}] * MIN_BARS},
     SNAPSHOT_OHLCV_INVALID),
], ids=["schema", "timeframe", "trading_date", "no_proof_fields", "no_version",
        "insufficient_bars", "bool_volume", "nonnumeric_open"])
def test_bars_row_validation_drops_instrument(tmp_path, over, reason):
    res = _source(tmp_path, bars_rows=[_bars_row(**over)]).build()
    assert res.records == [] and res.excluded == {CID: reason}


def test_future_bar_end_time_fails_closed(tmp_path):
    # A bar that ends AFTER the injected clock is rejected (snapshot_future_bar).
    future = "2026-06-10T23:30:00Z"  # 30 min after NOW
    res = _source(tmp_path,
                  bars_rows=[_bars_row(bar_end_time=future)]).build()
    assert res.excluded == {CID: SNAPSHOT_FUTURE_BAR} and res.records == []


def test_identity_missing_on_bars_row_fails_closed(tmp_path):
    # No ticker/symbol fallback: a bars row lacking any of the R2A-1 triple is dropped.
    res = _source(tmp_path, bars_rows=[_bars_row(instrument_uid=None)]).build()
    assert res.records == []
    assert list(res.excluded.values()) == [SNAPSHOT_IDENTITY_MISMATCH]


def test_identity_mismatch_between_bars_and_completed_fails_closed(tmp_path):
    # completed shares the listing_uid but carries a DIFFERENT instrument_uid → disagreement.
    res = _source(tmp_path,
                  completed_rows=[_completed_row(instrument_uid="iuid-OTHER")]).build()
    assert res.records == [] and res.excluded == {CID: SNAPSHOT_IDENTITY_MISMATCH}


def test_no_matching_completed_row_fails_closed(tmp_path):
    # completed shares NEITHER uid nor listing → no availability proof for this instrument.
    res = _source(tmp_path,
                  completed_rows=[_completed_row(instrument_uid="iuid-X", listing_uid="luid-X",
                                                 canonical_instrument_id="US_X")]).build()
    assert res.records == [] and res.excluded == {CID: SNAPSHOT_PROOF_MISSING}


def test_completed_cid_disagrees_fails_closed(tmp_path):
    # completed row matches uid/listing but carries a conflicting canonical_instrument_id.
    res = _source(tmp_path,
                  completed_rows=[_completed_row(canonical_instrument_id="US_OTHER")]).build()
    assert res.records == [] and res.excluded == {CID: SNAPSHOT_IDENTITY_MISMATCH}


def test_completed_unavailable_fails_closed(tmp_path):
    res = _source(tmp_path, completed_rows=[_completed_row(available=False)]).build()
    assert res.records == [] and res.excluded == {CID: SNAPSHOT_PROOF_MISSING}


def test_completed_future_bar_fails_closed(tmp_path):
    res = _source(tmp_path,
                  completed_rows=[_completed_row(bar_end_time="2026-06-10T23:30:00Z")]).build()
    assert res.records == [] and res.excluded == {CID: SNAPSHOT_FUTURE_BAR}


def test_mixed_array_is_malformed_and_never_raises(tmp_path):
    # A JSON array with a non-dict entry is rejected by the strict parser as malformed (fail
    # closed, source-level) — it must not raise.
    bad = tmp_path / "local_bars.json"
    bad.write_text(json.dumps([_bars_row(), 42]))
    completed = _write(tmp_path / "completed_bars.json", [_completed_row()])
    src = SnapshotShadowRecordsSource(str(bad), completed, now_fn=lambda: NOW)
    res = src.build()  # must not raise
    assert res.source_reason == SNAPSHOT_MALFORMED and res.records == []


def test_foreign_dict_rows_in_bars_source_are_skipped(tmp_path):
    # A dict row without a "bars" key (e.g. an availability row that leaked into the bars file) is
    # silently skipped, not crashed; the real bars row is still built.
    res = _source(tmp_path, bars_rows=[_completed_row(), _bars_row()]).build()
    assert [r["canonical_instrument_id"] for r in res.records] == [CID]


# ── main.py config integration (flag-gated, fail-closed) ────────────────────────
def test_config_builder_flag_off_returns_none(tmp_path):
    bars, completed = _sources(tmp_path)
    cfg = {"bars_snapshot_source": bars, "completed_bar_snapshot_source": completed}
    assert main.build_shadow_records_fn_from_config(OFF, cfg) is None


def test_config_builder_flag_off_reads_no_file(tmp_path):
    # Even with a source that WOULD raise on read (a directory-as-file mismatch), flag-off returns
    # None before any construction/read.
    cfg = {"bars_snapshot_source": str(tmp_path / "x"),
           "completed_bar_snapshot_source": str(tmp_path / "y")}
    assert main.build_shadow_records_fn_from_config(OFF, cfg) is None
    # nothing was created
    assert not (tmp_path / "x").exists() and not (tmp_path / "y").exists()


def test_config_builder_flag_on_missing_config_fails_closed():
    assert main.build_shadow_records_fn_from_config(ON, {}) is None
    assert main.build_shadow_records_fn_from_config(ON, None) is None


def test_config_builder_flag_on_unsafe_source_fails_closed(tmp_path):
    bars, _ = _sources(tmp_path)
    cfg = {"bars_snapshot_source": bars,
           "completed_bar_snapshot_source": str(tmp_path / "positions.db")}
    assert main.build_shadow_records_fn_from_config(ON, cfg) is None


def test_config_builder_flag_on_valid_builds_records(tmp_path):
    bars, completed = _sources(tmp_path)
    cfg = {"bars_snapshot_source": bars, "completed_bar_snapshot_source": completed}
    fn = main.build_shadow_records_fn_from_config(ON, cfg)
    assert fn is not None
    records = fn()
    assert [r["canonical_instrument_id"] for r in records] == [CID]


# ── no production-DB / universe.db touch ────────────────────────────────────────
def test_build_creates_no_database(tmp_path):
    bars, completed = _sources(tmp_path)
    SnapshotShadowRecordsSource(bars, completed, now_fn=lambda: NOW).build()
    # The seam opens no DB: no universe.db / universe_shadow.db / *.db appears anywhere in tmp.
    dbs = [n for _, _, fs in os.walk(tmp_path) for n in fs if n.endswith(".db")]
    assert dbs == [], f"seam created database file(s): {dbs}"


def test_module_imports_no_broker():
    import importlib
    import sys

    for m in ("ib_insync", "trading_ig"):
        before = m in sys.modules
        importlib.import_module("bot.universe.shadow_records")
        assert (m in sys.modules) == before, f"importing shadow_records pulled in {m}"
