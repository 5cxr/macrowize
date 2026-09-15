"""Single place the chat model is constructed.

Switching providers is one env var, nothing downstream imports a provider package:
    MACROWIZE_LLM_PROVIDER=google   (default, needs GOOGLE_API_KEY)
    MACROWIZE_LLM_PROVIDER=groq     (needs GROQ_API_KEY)
    MACROWIZE_LLM_PROVIDER=ollama   (local, needs no key)
"""

from __future__ import annotations

import functools
import os

from langchain_core.language_models.chat_models import BaseChatModel

from config import LLM_MODEL, LLM_PROVIDER, LLM_TEMPERATURE, OLLAMA_BASE_URL


class LLMUnavailableError(RuntimeError):
    """Raised when no chat model can be constructed, usually a missing API key."""


def _require_key(*names: str) -> str:
    """Return the first of `names` that is set, or explain what to do about it."""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    raise LLMUnavailableError(
        f"{names[0]} is not set. Export it, or run with "
        "MACROWIZE_LLM_PROVIDER=ollama for a local model that needs no key."
    )


@functools.lru_cache(maxsize=1)
def get_llm() -> BaseChatModel:
    """Return the configured chat model, built once and reused."""
    provider = LLM_PROVIDER.lower()

    if provider == "google":
        # The SDK reads GOOGLE_API_KEY; accept AI Studio's GEMINI_API_KEY too.
        key = _require_key("GOOGLE_API_KEY", "GEMINI_API_KEY")
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=LLM_MODEL, temperature=LLM_TEMPERATURE, google_api_key=key
        )

    if provider == "groq":
        _require_key("GROQ_API_KEY")
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
