"""
Daily classifier scheduler — §14 of CLAUDE_STRATEGY_SPEC_v3.

The classifier runs once per day per instrument, after market close. main.py's
per-cycle loop calls maybe_run(); the scheduler decides per instrument whether
the post-close window is open AND we haven't already classified today.

When both are true: fetch bars → compute features → classify → smooth → persist.

Idempotency: a successful classification (real or fallback) writes to
regime_classification_cache, so subsequent maybe_run() calls within the same
trading_date are no-ops.

Gated on FeatureFlags.enable_classifier_shadow. Flag off ⇒ pure no-op.
"""
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from bot.regime.cache import RegimeCache
from bot.regime.classifier import RegimeClassifier
from bot.regime.features import compute_regime_features
from bot.regime.flags import FeatureFlags
from bot.regime.smoothing import initial_state, update_first_run, update
from bot.regime.smoothing_store import SmoothedStateStore

logger = logging.getLogger("regime.scheduler")

# Post-close windows (UTC). Set conservatively past the actual close so we
# capture closing-bar features regardless of BST/EST or DST shifts:
#   LSE closes 16:30 UTC (winter) / 15:30 UTC (BST).      Trigger at 17:00 UTC.
#   NYSE closes 21:00 UTC (winter) / 20:00 UTC (DST).     Trigger at 21:30 UTC.
LSE_POST_CLOSE_HOUR = 17
LSE_POST_CLOSE_MINUTE = 0
US_POST_CLOSE_HOUR = 21
US_POST_CLOSE_MINUTE = 30

MIN_BARS_REQUIRED = 200  # MA200 slope requires this many


class RegimeClassificationScheduler:
    def __init__(self,
                 flags: FeatureFlags,
                 classifier: RegimeClassifier,
                 cache: RegimeCache,
                 smoothing_store: SmoothedStateStore,
                 bars_fetcher: Callable[[dict], object],
                 now_fn: Optional[Callable[[], datetime]] = None):
        self._flags = flags
        self._classifier = classifier
        self._cache = cache
        self._smoothing_store = smoothing_store
        self._bars_fetcher = bars_fetcher
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def maybe_run(self, instruments: list) -> dict:
        """Iterate instruments; classify each that is post-close AND not yet
        classified today. Returns a per-status summary for the caller / logs."""
        summary = {
            "classified": [],
            "skipped_flag_off": [],
            "skipped_not_post_close": [],
            "skipped_cache_hit": [],
            "errors": [],
        }

        if not self._flags.get("enable_classifier_shadow"):
            symbols = [i.get("symbol") for i in instruments if i.get("symbol")]
            summary["skipped_flag_off"] = symbols
            return summary

        now = self._now_fn()
        trading_date = now.strftime("%Y-%m-%d")

        for inst in instruments:
            symbol = inst.get("symbol")
            if not symbol:
                continue

            if not self._is_post_close(inst, now):
                summary["skipped_not_post_close"].append(symbol)
                continue

            if self._cache.has_for_day(symbol, trading_date):
                summary["skipped_cache_hit"].append(symbol)
                continue

            try:
                regime, confidence, smoothed_regime = self._classify_and_persist(
                    inst, symbol, trading_date,
                )
                summary["classified"].append({
                    "symbol": symbol,
                    "regime": regime,
                    "confidence": confidence,
                    "smoothed": smoothed_regime,
                })
                logger.info(
                    "[Scheduler] %s classified: regime=%s confidence=%.2f "
                    "smoothed=%s (trading_date=%s)",
                    symbol, regime, confidence, smoothed_regime, trading_date,
                )
            except Exception as e:
                logger.warning(
                    "[Scheduler] %s classification failed: %s", symbol, e,
                )
                summary["errors"].append({"symbol": symbol, "error": str(e)})

        return summary

    def _is_post_close(self, inst: dict, now: datetime) -> bool:
        market = (inst.get("market") or "").upper()
        if market == "LSE":
            hour, minute = LSE_POST_CLOSE_HOUR, LSE_POST_CLOSE_MINUTE
        else:
            hour, minute = US_POST_CLOSE_HOUR, US_POST_CLOSE_MINUTE
        threshold = now.replace(hour=hour, minute=minute,
                                second=0, microsecond=0)
        return now >= threshold

    def _classify_and_persist(self, inst: dict, symbol: str,
                              trading_date: str) -> tuple:
        df = self._bars_fetcher(inst)
        if df is None or len(df) < MIN_BARS_REQUIRED:
            n = 0 if df is None else len(df)
            raise ValueError(
                f"insufficient bars: got {n}, need {MIN_BARS_REQUIRED}"
            )

        features = compute_regime_features(df)
        classification = self._classifier.classify(symbol, trading_date, features)

        # Always persist — even fallbacks — so we don't retry within the day.
        # INSERT OR REPLACE makes the classifier's own put (happy path) safe.
        self._cache.put(classification)

        prior = self._smoothing_store.get_latest(symbol)
        if prior is None:
            prior = initial_state(symbol)
            smoothed = update_first_run(prior, classification)
        else:
            smoothed = update(prior, classification)
        self._smoothing_store.put(smoothed)

        return classification.raw_regime, classification.confidence, smoothed.effective_regime
