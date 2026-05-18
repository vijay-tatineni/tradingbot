"""
Claude-based regime classifier — §9.2 of CLAUDE_STRATEGY_SPEC_v3.

Uses Anthropic API tool-use for structured JSON output, bypassing
BaseLLM.chat() which only returns plain text. This is intentional:
the classifier is Claude-specific by design.

Runs once per day per instrument, after market close.
"""
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import anthropic

from bot.regime.models import RegimeClassification
from bot.regime.classifier_prompt import CLASSIFIER_PROMPT_V1
from bot.regime.classifier_schema import ClassifierResponseSchema, CLASSIFY_REGIME_TOOL
from bot.regime.cache import RegimeCache
from bot.regime.cost_tracker import CostTracker, MODEL_ID

logger = logging.getLogger("regime.classifier")

PROMPT_VERSION = "V1"
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0


class RegimeClassifier:
    def __init__(self, cache: RegimeCache, cost_tracker: CostTracker,
                 model: str = MODEL_ID):
        self._cache = cache
        self._cost_tracker = cost_tracker
        self._model = model
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else None

    def is_available(self) -> bool:
        return self._client is not None

    def classify(self, instrument: str, trading_date: str,
                 features: dict) -> RegimeClassification:
        input_hash = self._compute_hash(features)

        cached = self._cache.get(instrument, trading_date, input_hash)
        if cached is not None:
            logger.info(f"Cache hit for {instrument} on {trading_date}")
            self._cost_tracker.log_classification(
                instrument=instrument, trading_date=trading_date,
                model=self._model, prompt_version=PROMPT_VERSION,
                raw_regime=cached.raw_regime, confidence=cached.confidence,
                cache_hit=True,
            )
            return cached

        if self._cost_tracker.is_budget_exceeded(trading_date):
            logger.warning(f"Daily budget exceeded for {trading_date}, using fallback")
            return self._fallback(instrument, trading_date, features, input_hash,
                                  error="budget_exceeded")

        if not self.is_available():
            logger.error("Anthropic client not available, using fallback")
            return self._fallback(instrument, trading_date, features, input_hash,
                                  error="client_unavailable")

        return self._call_api(instrument, trading_date, features, input_hash)

    def _call_api(self, instrument: str, trading_date: str,
                  features: dict, input_hash: str) -> RegimeClassification:
        user_content = (
            f"Instrument: {instrument}\n"
            f"Trading date: {trading_date}\n"
            f"Features:\n{json.dumps(features, indent=2)}"
        )

        for attempt in range(MAX_RETRIES):
            start_ms = _now_ms()
            try:
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=500,
                    temperature=0.3,
                    system=CLASSIFIER_PROMPT_V1,
                    messages=[{"role": "user", "content": user_content}],
                    tools=[CLASSIFY_REGIME_TOOL],
                    tool_choice={"type": "tool", "name": "classify_regime"},
                )
                latency_ms = _now_ms() - start_ms
                return self._parse_response(
                    response, instrument, trading_date, features,
                    input_hash, latency_ms,
                )
            except anthropic.RateLimitError:
                logger.warning(f"Rate limited (attempt {attempt + 1}/{MAX_RETRIES})")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF_BASE ** (attempt + 1))
            except anthropic.APIStatusError as e:
                if e.status_code >= 500:
                    logger.warning(f"API error {e.status_code} (attempt {attempt + 1})")
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(RETRY_BACKOFF_BASE ** (attempt + 1))
                else:
                    logger.error(f"API error {e.status_code}: {e}")
                    break
            except Exception as e:
                logger.error(f"Classifier call failed: {e}")
                break

        return self._fallback(instrument, trading_date, features, input_hash,
                              error="api_failure_after_retries")

    def _parse_response(self, response, instrument: str, trading_date: str,
                        features: dict, input_hash: str,
                        latency_ms: int) -> RegimeClassification:
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = self._cost_tracker.compute_cost(self._model, input_tokens, output_tokens)

        tool_use_block = None
        for block in response.content:
            if block.type == "tool_use" and block.name == "classify_regime":
                tool_use_block = block
                break

        if tool_use_block is None:
            logger.error("No tool_use block in classifier response")
            self._cost_tracker.log_classification(
                instrument=instrument, trading_date=trading_date,
                model=self._model, prompt_version=PROMPT_VERSION,
                input_tokens=input_tokens, output_tokens=output_tokens,
                cost_usd=cost, latency_ms=latency_ms,
                error="no_tool_use_block",
            )
            return self._fallback(instrument, trading_date, features, input_hash,
                                  error="no_tool_use_block")

        try:
            parsed = ClassifierResponseSchema(**tool_use_block.input)
        except Exception as e:
            logger.error(f"Schema validation failed: {e}")
            self._cost_tracker.log_classification(
                instrument=instrument, trading_date=trading_date,
                model=self._model, prompt_version=PROMPT_VERSION,
                input_tokens=input_tokens, output_tokens=output_tokens,
                cost_usd=cost, latency_ms=latency_ms,
                error=f"schema_validation: {e}",
            )
            return self._fallback(instrument, trading_date, features, input_hash,
                                  error=f"schema_validation: {e}")

        if parsed.regime not in ("TRENDING", "RANGING", "UNCLEAR"):
            logger.error(f"Invalid regime value: {parsed.regime}")
            return self._fallback(instrument, trading_date, features, input_hash,
                                  error=f"invalid_regime: {parsed.regime}")

        classification = RegimeClassification(
            instrument=instrument,
            classified_at=datetime.now(timezone.utc),
            trading_date=trading_date,
            raw_regime=parsed.regime,
            confidence=parsed.confidence,
            rationale=parsed.rationale,
            features=features,
            model_version=self._model,
            prompt_version=PROMPT_VERSION,
            input_hash=input_hash,
            cache_hit=False,
        )

        self._cache.put(classification)

        self._cost_tracker.log_classification(
            instrument=instrument, trading_date=trading_date,
            model=self._model, prompt_version=PROMPT_VERSION,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost, latency_ms=latency_ms,
            raw_regime=parsed.regime, confidence=parsed.confidence,
        )

        return classification

    def _fallback(self, instrument: str, trading_date: str,
                  features: dict, input_hash: str,
                  error: str) -> RegimeClassification:
        """§9.2: all failures return UNCLEAR with confidence=0.0."""
        logger.warning(f"Classifier fallback for {instrument}: {error}")
        self._cost_tracker.log_classification(
            instrument=instrument, trading_date=trading_date,
            model=self._model, prompt_version=PROMPT_VERSION,
            error=error,
        )
        return RegimeClassification(
            instrument=instrument,
            classified_at=datetime.now(timezone.utc),
            trading_date=trading_date,
            raw_regime="UNCLEAR",
            confidence=0.0,
            rationale=f"Fallback: {error}",
            features=features,
            model_version=self._model,
            prompt_version=PROMPT_VERSION,
            input_hash=input_hash,
            cache_hit=False,
        )

    def _compute_hash(self, features: dict) -> str:
        content = json.dumps(features, sort_keys=True) + PROMPT_VERSION + self._model
        return hashlib.sha256(content.encode()).hexdigest()


def _now_ms() -> int:
    return int(time.time() * 1000)
