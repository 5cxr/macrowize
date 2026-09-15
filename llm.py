"""Single place the chat model is constructed.

Swapping Groq for a local Ollama model is one env var:
    MACROWIZE_LLM_PROVIDER=ollama MACROWIZE_LLM_MODEL=llama3.1:8b
Nothing downstream imports a provider package directly.
"""

from __future__ import annotations

import functools

from langchain_core.language_models.chat_models import BaseChatModel

from config import LLM_MODEL, LLM_PROVIDER, LLM_TEMPERATURE, OLLAMA_BASE_URL


class LLMUnavailableError(RuntimeError):
    """Raised when no chat model can be constructed, usually a missing API key."""


@functools.lru_cache(maxsize=1)
def get_llm() -> BaseChatModel:
    """Return the configured chat model, built once and reused."""
    provider = LLM_PROVIDER.lower()

    if provider == "groq":
        import os

        if not os.getenv("GROQ_API_KEY"):
            raise LLMUnavailableError(
                "GROQ_API_KEY is not set. Export it, or run with "
                "MACROWIZE_LLM_PROVIDER=ollama for a local model."
            )
        from langchain_groq import ChatGroq

        return ChatGroq(model=LLM_MODEL, temperature=LLM_TEMPERATURE)

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=LLM_MODEL, temperature=LLM_TEMPERATURE, base_url=OLLAMA_BASE_URL
        )

    raise LLMUnavailableError(f"Unknown LLM provider: {LLM_PROVIDER!r}")


def llm_is_available() -> bool:
    """Check whether a model can be built, for the UI to degrade gracefully."""
    try:
        get_llm()
        return True
    except Exception:
        return False
