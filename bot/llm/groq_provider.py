"""
Groq LLM provider using Llama 3.3 70B.

Free tier: 30 RPM, 14,400 RPD, 6,000 TPM.
Extremely fast: ~750 tokens/sec.

Requires: pip install groq
Env var: GROQ_API_KEY
"""
import os
import time
import logging
from typing import List, Dict

from groq import Groq

from .base import BaseLLM

logger = logging.getLogger("llm.groq")


class GroqLLM(BaseLLM):
    """Groq provider using Llama 3.3 70B."""

    def __init__(self, config=None):
        self.api_key = os.environ.get("GROQ_API_KEY", "")
        self.model = "llama-3.3-70b-versatile"
        self.client = None
        self._last_call_time = 0
        self._min_interval = 2.0  # Stay well under 30 RPM

        if self.api_key:
            try:
                self.client = Groq(api_key=self.api_key)
                logger.info(f"Groq LLM initialized (model: {self.model})")
            except Exception as e:
                logger.error(f"Failed to init Groq: {e}")

    def chat(self, messages: List[Dict[str, str]],
             temperature: float = 0.3,
             max_tokens: int = 500) -> str:
        if not self.client:
            logger.warning("Groq client not initialized")
            return ""

        # Rate limiting
        elapsed = time.time() - self._last_call_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        try:
            self._last_call_time = time.time()
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            text = response.choices[0].message.content.strip()
            logger.debug(f"Groq response ({len(text)} chars)")
            return text

        except Exception as e:
            logger.error(f"Groq chat failed: {e}")
            return ""

    def is_available(self) -> bool:
        return self.client is not None and bool(self.api_key)
