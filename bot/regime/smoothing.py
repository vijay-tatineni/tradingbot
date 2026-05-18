"""
Deterministic regime smoothing — §9.3 of CLAUDE_STRATEGY_SPEC_v3.

Pure deterministic. No API calls. Prevents regime label thrashing
via persistence and hysteresis rules.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from bot.regime.models import RegimeClassification, SmoothedRegimeState

logger = logging.getLogger("regime.smoothing")

SAME_REGIME_PERSISTENCE_THRESHOLD = 0.85
NEW_REGIME_PENDING_THRESHOLD = 0.70
TRENDING_TO_RANGING_HYSTERESIS = 0.75
PENDING_DAYS_TO_PROMOTE = 2
MAX_HISTORY_LENGTH = 10


def initial_state(instrument: str) -> SmoothedRegimeState:
    """§9.3: seed with UNCLEAR, days_in_regime=0."""
    return SmoothedRegimeState(
        instrument=instrument,
        effective_regime="UNCLEAR",
        source_regime="UNCLEAR",
        days_in_regime=0,
        last_changed_at=datetime.now(timezone.utc),
        confidence=0.0,
        pending_regime=None,
        pending_days=0,
        regime_history=[],
    )


def update(prior: SmoothedRegimeState,
           classification: RegimeClassification) -> SmoothedRegimeState:
    """Apply smoothing rules to produce new state.

    Rules (§9.3):
    1. confidence >= 0.85 and matches effective_regime → persist, increment days
    2. Differs from effective and confidence >= 0.70 → pending logic
    3. confidence < 0.70 → retain current state
    4. Fallback (confidence == 0.0) → retain entirely, warn
    5. TRENDING→RANGING requires confidence >= 0.75 (hysteresis)
    6. regime_history bounded to last 10
    """
    new_regime = classification.raw_regime
    confidence = classification.confidence

    # Rule 4: fallback classifier result
    if confidence == 0.0:
        logger.warning(f"Fallback classification for {prior.instrument}, retaining state")
        return prior

    # Rule 3: low confidence
    if confidence < NEW_REGIME_PENDING_THRESHOLD:
        return prior

    # Same regime, high confidence (Rule 1)
    if new_regime == prior.effective_regime and confidence >= SAME_REGIME_PERSISTENCE_THRESHOLD:
        return SmoothedRegimeState(
            instrument=prior.instrument,
            effective_regime=prior.effective_regime,
            source_regime=new_regime,
            days_in_regime=prior.days_in_regime + 1,
            last_changed_at=prior.last_changed_at,
            confidence=confidence,
            pending_regime=None,
            pending_days=0,
            regime_history=prior.regime_history,
        )

    # Different regime with sufficient confidence (Rule 2)
    if new_regime != prior.effective_regime and confidence >= NEW_REGIME_PENDING_THRESHOLD:
        # Rule 5: TRENDING→RANGING hysteresis
        if (prior.effective_regime == "TRENDING" and new_regime == "RANGING"
                and confidence < TRENDING_TO_RANGING_HYSTERESIS):
            return prior

        if prior.pending_regime == new_regime:
            new_pending_days = prior.pending_days + 1
            if new_pending_days >= PENDING_DAYS_TO_PROMOTE:
                return _promote(prior, new_regime, confidence)
            return SmoothedRegimeState(
                instrument=prior.instrument,
                effective_regime=prior.effective_regime,
                source_regime=new_regime,
                days_in_regime=prior.days_in_regime,
                last_changed_at=prior.last_changed_at,
                confidence=prior.confidence,
                pending_regime=new_regime,
                pending_days=new_pending_days,
                regime_history=prior.regime_history,
            )
        else:
            return SmoothedRegimeState(
                instrument=prior.instrument,
                effective_regime=prior.effective_regime,
                source_regime=new_regime,
                days_in_regime=prior.days_in_regime,
                last_changed_at=prior.last_changed_at,
                confidence=prior.confidence,
                pending_regime=new_regime,
                pending_days=1,
                regime_history=prior.regime_history,
            )

    # Same regime but confidence between 0.70 and 0.85 — retain
    return prior


def _promote(prior: SmoothedRegimeState, new_regime: str,
             confidence: float) -> SmoothedRegimeState:
    """Promote pending regime to effective."""
    history = list(prior.regime_history) + [prior.effective_regime]
    if len(history) > MAX_HISTORY_LENGTH:
        history = history[-MAX_HISTORY_LENGTH:]

    # §9.3: first run with days_in_regime=0 promotes on day 1
    return SmoothedRegimeState(
        instrument=prior.instrument,
        effective_regime=new_regime,
        source_regime=new_regime,
        days_in_regime=1,
        last_changed_at=datetime.now(timezone.utc),
        confidence=confidence,
        pending_regime=None,
        pending_days=0,
        regime_history=history,
    )


def update_first_run(state: SmoothedRegimeState,
                     classification: RegimeClassification) -> SmoothedRegimeState:
    """§9.3: First high-confidence classification promotes on day 1."""
    if (state.days_in_regime == 0
            and classification.confidence >= NEW_REGIME_PENDING_THRESHOLD):
        return _promote(state, classification.raw_regime, classification.confidence)
    return update(state, classification)
