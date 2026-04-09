"""
Anthropic Claude LLM provider.

Supports Haiku (fast/cheap) and Sonnet (smart/deeper analysis).
Uses the Anthropic Python SDK.

Requires: pip install anthropic
Env var: ANTHROPIC_API_KEY
"""
import os
import time
import logging
from typing import List, Dict

import anthropic

from .base import BaseLLM

logger = logging.getLogger("llm.claude")


class ClaudeLLM(BaseLLM):
    """Anthropic Claude provider (Haiku or Sonnet)."""

    # Model IDs
    MODELS = {
        "haiku": "claude-haiku-4-5-20251001",
        "sonnet": "claude-sonnet-4-20250514",
    }

    def __init__(self, config=None, model: str = "haiku"):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.model_name = model.lower()
        self.model_id = self.MODELS.get(self.model_name, self.MODELS["haiku"])
        self.client = None
        self._last_call_time = 0
        self._min_interval = 0.5  # Anthropic rate limits are generous

        if self.api_key:
            try:
                self.client = anthropic.Anthropic(api_key=self.api_key)
                logger.info(f"Claude LLM initialized (model: {self.model_id})")
            except Exception as e:
                logger.error(f"Failed to init Claude: {e}")

    def chat(self, messages: List[Dict[str, str]],
             temperature: float = 0.3,
             max_tokens: int = 500) -> str:
        if not self.client:
            logger.warning("Claude client not initialized")
            return ""

        # Rate limiting
        elapsed = time.time() - self._last_call_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        try:
            self._last_call_time = time.time()

            # Anthropic API separates system message from user messages
            system_msg = ""
            api_messages = []
            for msg in messages:
                if msg["role"] == "system":
                    system_msg = msg["content"]
                else:
                    api_messages.append(msg)

            # Ensure messages alternate user/assistant
            # If all are user messages, that's fine for single-turn
            kwargs = {
                "model": self.model_id,
                "max_tokens": max_tokens,
                "messages": api_messages,
                "temperature": temperature,
            }
            if system_msg:
                kwargs["system"] = system_msg

            response = self.client.messages.create(**kwargs)
            text = response.content[0].text.strip()
            logger.debug(f"Claude response ({len(text)} chars, model={self.model_name})")
            return text

        except Exception as e:
            logger.error(f"Claude chat failed: {e}")
            return ""

    def is_available(self) -> bool:
        return self.client is not None and bool(self.api_key)
