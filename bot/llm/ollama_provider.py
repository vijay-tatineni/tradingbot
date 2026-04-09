"""
Ollama LLM provider (local inference).
Placeholder — requires VPS upgrade to 8GB+ RAM.

When ready, install Ollama and pull a model:
    curl -fsSL https://ollama.com/install.sh | sh
    ollama pull llama3.1:8b
"""
import logging
from typing import List, Dict
from .base import BaseLLM

logger = logging.getLogger("llm.ollama")

class OllamaLLM(BaseLLM):
    def __init__(self, config=None):
        self.model = "llama3.1:8b"
        self.base_url = "http://localhost:11434"
        logger.info("Ollama provider initialized (placeholder)")

    def chat(self, messages: List[Dict[str, str]],
             temperature: float = 0.3,
             max_tokens: int = 500) -> str:
        # TODO: Implement when VPS has enough RAM
        # Use requests to call http://localhost:11434/api/chat
        logger.warning("Ollama not yet implemented — returning empty")
        return ""

    def is_available(self) -> bool:
        return False  # Not available until implemented
