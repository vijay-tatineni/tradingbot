"""Dynamic Universe v1 — idempotent daily shadow scheduler.

Reuses the regime scheduler's pattern: a per-cycle ``maybe_run`` that is a pure
no-op when the feature flag is off, evaluates only AFTER each instrument's completed
local daily session (conservative post-close UTC thresholds; multi-timezone aware),
and is restart-safe via the evaluator's (instrument, trading_date, evaluator_version)
idempotency. It never acts on an incomplete bar.

NOT enabled in production: this scheduler is constructed/run only in a test/shadow
environment with ``enable_dynamic_universe_shadow`` true. It is not wired into main.py.

Calendar safety (task §5)
-------------------------
The post-close UTC thresholds are deliberately conservative: each sits AFTER the
latest possible close for its market across BOTH daylight-saving regimes, so the gate
can only ever fire LATE, never early. That makes ordinary days, US/UK DST transitions
(including the weeks where US and UK DST dates differ), and early-close / half-day
sessions safe by construction — an earlier close only makes the completed bar
available sooner.

The fixed time gate canNOT, by itself, prove a completed bar actually EXISTS — on an
exchange holiday the gate passes but no session occurred, and a daily bar can arrive
late. So the time gate is a NECESSARY-not-SUFFICIENT precondition: when a
``bar_available_fn`` is injected the scheduler additionally requires it to confirm the
expected completed session bar exists before evaluating. If it is missing the
instrument is SKIPPED and logged (no history is written), so the next idempotent cycle
retries it. Invariant: *the evaluator may run late, but never before the completed
daily bar is safely available.*
"""
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger("universe.scheduler")

FLAG = "enable_dynamic_universe_shadow"

# Conservative post-close UTC thresholds (mirror bot/regime/scheduler._is_post_close):
# only evaluate an instrument once its market's daily session is safely complete.
# (LSE closes 16:30/15:30; US 21:00/20:00; Euronext 16:30/15:30 — thresholds sit after
# the latest possible close so a partial bar is never evaluated.)
_POST_CLOSE_UTC_HOUR = {"LSE": 17, "EU": 17, "US": 22}


def _market_of(rec: dict) -> str:
    ccy = (rec.get("currency") or "USD").upper()
    if ccy == "GBP":
        return "LSE"
    if ccy == "EUR":
        return "EU"
    return "US"


class DailyUniverseScheduler:
    def __init__(self, evaluator, flags,
                 now_fn: Optional[Callable[[], datetime]] = None,
                 bar_available_fn: Optional[Callable[[dict, str], bool]] = None):
        self.evaluator = evaluator
        self.flags = flags
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        # Injectable completed-bar availability check (task §5). When provided it must
        # confirm the EXPECTED completed session bar exists for (rec, trading_date)
        # before the instrument is evaluated. Broker-free by contract (a fixture / data
        # snapshot lookup — never a broker call). None ⇒ no extra gate (the time gate
        # alone), for unit tests that do not model holidays/late bars.
        self._bar_available_fn = bar_available_fn

    def _is_post_close(self, market: str, now: datetime) -> bool:
        return now.hour >= _POST_CLOSE_UTC_HOUR.get(market, 22)

    def maybe_run(self, canonical_records: list) -> dict:
        try:
            enabled = bool(self.flags.get(FLAG))
        except Exception:
            enabled = False
        if not enabled:
            return {"ran": False, "reason": "flag_off"}

        now = self._now_fn()
        trading_date = now.date().isoformat()
        post_close = [rec for rec in canonical_records
                      if self._is_post_close(_market_of(rec), now)]

        # Bar-availability gate: skip (and retry next idempotent cycle) any instrument
        # whose completed daily bar is not yet confirmed available — holiday/late bar.
        completed, skipped = [], []
        for rec in post_close:
            cid = rec["canonical_instrument_id"]
            if self._bar_available_fn is not None and not self._safe_bar_available(rec, trading_date):
                skipped.append(cid)
                logger.info("dynamic-universe scheduler %s: completed bar unavailable for "
                            "%s — skipping (will retry)", trading_date, cid)
                continue
            completed.append(cid)

        if not completed:
            return {"ran": False, "reason": "no_completed_sessions",
                    "trading_date": trading_date,
                    "missing_bar_skipped": skipped}

        # Idempotent in the evaluator: re-running the same (id, date, version) is a no-op.
        result = self.evaluator.maybe_run(trading_date, only_ids=completed)
        result["scheduled_ids"] = completed
        result["missing_bar_skipped"] = skipped
        return result

    def _safe_bar_available(self, rec: dict, trading_date: str) -> bool:
        """Confirm the completed bar exists; any provider error is treated as
        'not available' (fail safe → skip + retry), never as a silent green light."""
        try:
            return bool(self._bar_available_fn(rec, trading_date))
        except Exception:
            logger.warning("dynamic-universe scheduler %s: bar-availability check raised "
                           "for %s — treating as unavailable", trading_date,
                           rec.get("canonical_instrument_id"))
            return False
