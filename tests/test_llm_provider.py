"""
Tests for the LLM provider abstraction layer.
"""
import os
import time
import pytest
from unittest.mock import patch, MagicMock


# --- Factory ---

def test_create_llm_groq():
    """create_llm('groq') returns GroqLLM instance."""
    from bot.llm import create_llm
    from bot.llm.groq_provider import GroqLLM
    with patch.dict(os.environ, {"GROQ_API_KEY": ""}):
        llm = create_llm("groq")
    assert isinstance(llm, GroqLLM)


def test_create_llm_claude_haiku():
    """create_llm('claude-haiku') returns ClaudeLLM with haiku model."""
    from bot.llm import create_llm
    from bot.llm.claude_provider import ClaudeLLM
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
        llm = create_llm("claude-haiku")
    assert isinstance(llm, ClaudeLLM)
    assert llm.model_name == "haiku"


def test_create_llm_claude_sonnet():
    """create_llm('claude-sonnet') returns ClaudeLLM with sonnet model."""
    from bot.llm import create_llm
    from bot.llm.claude_provider import ClaudeLLM
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
        llm = create_llm("claude-sonnet")
    assert isinstance(llm, ClaudeLLM)
    assert llm.model_name == "sonnet"


def test_create_llm_claude_default_is_haiku():
    """create_llm('claude') defaults to haiku model."""
    from bot.llm import create_llm
    from bot.llm.claude_provider import ClaudeLLM
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
        llm = create_llm("claude")
    assert isinstance(llm, ClaudeLLM)
    assert llm.model_name == "haiku"


def test_create_llm_ollama():
    """create_llm('ollama') returns OllamaLLM instance."""
    from bot.llm import create_llm
    from bot.llm.ollama_provider import OllamaLLM
    llm = create_llm("ollama")
    assert isinstance(llm, OllamaLLM)


def test_create_llm_unknown_raises():
    """create_llm('unknown') raises ValueError."""
    from bot.llm import create_llm
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        create_llm("unknown")


# --- Groq ---

def test_groq_not_available_without_key():
    """GroqLLM without GROQ_API_KEY: is_available() returns False."""
    from bot.llm.groq_provider import GroqLLM
    with patch.dict(os.environ, {"GROQ_API_KEY": ""}, clear=False):
        llm = GroqLLM()
    assert llm.is_available() is False


def test_groq_chat_returns_string():
    """chat() should return a string (mock the API call)."""
    from bot.llm.groq_provider import GroqLLM
    llm = GroqLLM.__new__(GroqLLM)
    llm.api_key = "test-key"
    llm._last_call_time = 0
    llm._min_interval = 0
    llm.model = "test"

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hello world"
    mock_client.chat.completions.create.return_value = mock_response
    llm.client = mock_client

    result = llm.chat([{"role": "user", "content": "test"}])
    assert isinstance(result, str)
    assert result == "Hello world"


def test_groq_chat_handles_error_gracefully():
    """If Groq API fails, chat() returns empty string, not exception."""
    from bot.llm.groq_provider import GroqLLM
    llm = GroqLLM.__new__(GroqLLM)
    llm.api_key = "test-key"
    llm._last_call_time = 0
    llm._min_interval = 0
    llm.model = "test"

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = Exception("API Error")
    llm.client = mock_client

    result = llm.chat([{"role": "user", "content": "test"}])
    assert result == ""


def test_groq_rate_limiting():
    """Two rapid calls should have at least min_interval between them."""
    from bot.llm.groq_provider import GroqLLM
    llm = GroqLLM.__new__(GroqLLM)
    llm.api_key = "test-key"
    llm._last_call_time = time.time()
    llm._min_interval = 0.2
    llm.model = "test"

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "ok"
    mock_client.chat.completions.create.return_value = mock_response
    llm.client = mock_client

    start = time.time()
    llm.chat([{"role": "user", "content": "test"}])
    elapsed = time.time() - start
    assert elapsed >= 0.15  # Should have waited ~0.2s


# --- Claude ---

def test_claude_not_available_without_key():
    """ClaudeLLM without ANTHROPIC_API_KEY: is_available() returns False."""
    from bot.llm.claude_provider import ClaudeLLM
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
        llm = ClaudeLLM()
    assert llm.is_available() is False


def test_claude_chat_returns_string():
    """chat() should return a string (mock the API call)."""
    from bot.llm.claude_provider import ClaudeLLM
    llm = ClaudeLLM.__new__(ClaudeLLM)
    llm.api_key = "test-key"
    llm._last_call_time = 0
    llm._min_interval = 0
    llm.model_id = "test"
    llm.model_name = "haiku"

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock()]
    mock_response.content[0].text = "Hello from Claude"
    mock_client.messages.create.return_value = mock_response
    llm.client = mock_client

    result = llm.chat([{"role": "user", "content": "test"}])
    assert isinstance(result, str)
    assert result == "Hello from Claude"


def test_claude_chat_handles_error_gracefully():
    """If Anthropic API fails, chat() returns empty string."""
    from bot.llm.claude_provider import ClaudeLLM
    llm = ClaudeLLM.__new__(ClaudeLLM)
    llm.api_key = "test-key"
    llm._last_call_time = 0
    llm._min_interval = 0
    llm.model_id = "test"
    llm.model_name = "haiku"

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("API Error")
    llm.client = mock_client

    result = llm.chat([{"role": "user", "content": "test"}])
    assert result == ""


def test_claude_separates_system_message():
    """Claude API requires system message separate from user messages.
    Verify the provider correctly extracts it."""
    from bot.llm.claude_provider import ClaudeLLM
    llm = ClaudeLLM.__new__(ClaudeLLM)
    llm.api_key = "test-key"
    llm._last_call_time = 0
    llm._min_interval = 0
    llm.model_id = "test"
    llm.model_name = "haiku"

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock()]
    mock_response.content[0].text = "response"
    mock_client.messages.create.return_value = mock_response
    llm.client = mock_client

    llm.chat([
        {"role": "system", "content": "You are a helper"},
        {"role": "user", "content": "Hello"},
    ])

    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["system"] == "You are a helper"
    # System message should NOT be in messages list
    assert all(m["role"] != "system" for m in call_kwargs["messages"])


def test_claude_haiku_uses_correct_model_id():
    """Haiku should use 'claude-haiku-4-5-20251001'."""
    from bot.llm.claude_provider import ClaudeLLM
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
        llm = ClaudeLLM(model="haiku")
    assert llm.model_id == "claude-haiku-4-5-20251001"


def test_claude_sonnet_uses_correct_model_id():
    """Sonnet should use 'claude-sonnet-4-20250514'."""
    from bot.llm.claude_provider import ClaudeLLM
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
        llm = ClaudeLLM(model="sonnet")
    assert llm.model_id == "claude-sonnet-4-20250514"


# --- Multi-model ---

def test_multi_model_config():
    """Different providers for different tasks."""
    from bot.llm import create_llm
    from bot.llm.groq_provider import GroqLLM
    from bot.llm.claude_provider import ClaudeLLM

    with patch.dict(os.environ, {"GROQ_API_KEY": "", "ANTHROPIC_API_KEY": ""}, clear=False):
        sentiment_llm = create_llm("claude-haiku")
        review_llm = create_llm("claude-sonnet")
        pattern_llm = create_llm("groq")

    assert isinstance(sentiment_llm, ClaudeLLM)
    assert sentiment_llm.model_name == "haiku"
    assert isinstance(review_llm, ClaudeLLM)
    assert review_llm.model_name == "sonnet"
    assert isinstance(pattern_llm, GroqLLM)


# --- Ollama ---

def test_ollama_not_available():
    """OllamaLLM.is_available() returns False (placeholder)."""
    from bot.llm.ollama_provider import OllamaLLM
    llm = OllamaLLM()
    assert llm.is_available() is False


def test_ollama_chat_returns_empty():
    """OllamaLLM.chat() returns empty string (placeholder)."""
    from bot.llm.ollama_provider import OllamaLLM
    llm = OllamaLLM()
    result = llm.chat([{"role": "user", "content": "test"}])
    assert result == ""


def test_base_llm_is_abstract():
    """Cannot instantiate BaseLLM directly."""
    from bot.llm.base import BaseLLM
    with pytest.raises(TypeError):
        BaseLLM()
