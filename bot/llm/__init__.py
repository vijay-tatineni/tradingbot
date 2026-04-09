from .base import BaseLLM

def create_llm(provider: str = "groq", config=None, model: str = None) -> BaseLLM:
    """
    Create an LLM provider instance.

    provider: "groq" | "claude" | "claude-haiku" | "claude-sonnet" | "ollama"
    model: Optional model override (e.g., "haiku", "sonnet" for Claude)
    """
    if provider == "groq":
        from .groq_provider import GroqLLM
        return GroqLLM(config)
    elif provider in ("claude", "claude-haiku"):
        from .claude_provider import ClaudeLLM
        return ClaudeLLM(config, model=model or "haiku")
    elif provider == "claude-sonnet":
        from .claude_provider import ClaudeLLM
        return ClaudeLLM(config, model="sonnet")
    elif provider == "ollama":
        from .ollama_provider import OllamaLLM
        return OllamaLLM(config)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
