"""
Abstract LLM provider interface.
All providers must implement chat() — a simple message-in, text-out interface.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Optional


class BaseLLM(ABC):
    """Abstract LLM provider."""

    @abstractmethod
    def chat(self, messages: List[Dict[str, str]],
             temperature: float = 0.3,
             max_tokens: int = 500) -> str:
        """
        Send messages and get a text response.

        messages: [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
        Returns: response text string

        Must handle errors gracefully — return "" on failure, never crash.
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider is configured and reachable."""
        pass
