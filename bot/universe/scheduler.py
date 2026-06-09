"""Dynamic Universe v1 — idempotent daily shadow scheduler.

Reuses the regime scheduler's pattern: a per-cycle ``maybe_run`` that is a pure
no-op when the feature flag is off, evaluates only AFTER each instrument's completed
local daily session (conservative post-close UTC thresholds; multi-timezone aware),
and is restart-safe via the evaluator's (instrument, trading_date, evaluator_version)
idempotency. It never acts on an incomplete bar.

NOT enabled in production: this scheduler is constructed/run only in a test/shadow
environment with ``enable_dynamic_universe_shadow`` true. It is not wired into main.py.
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
    def __init__(self, evaluator, flags, now_fn: Optional[Callable[[], datetime]] = None):
        self.evaluator = evaluator
        self.flags = flags
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

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
        completed = [
            rec["canonical_instrument_id"]
            for rec in canonical_records
            if self._is_post_close(_market_of(rec), now)
        ]
        if not completed:
            return {"ran": False, "reason": "no_completed_sessions",
                    "trading_date": trading_date}

        # Idempotent in the evaluator: re-running the same (id, date, version) is a no-op.
        result = self.evaluator.maybe_run(trading_date, only_ids=completed)
        result["scheduled_ids"] = completed
        return result
