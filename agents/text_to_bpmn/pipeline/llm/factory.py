"""Resolve an :class:`LLMProvider` from settings.

Keeping this behind a factory is what makes the model/provider replaceable
(guideline Step 1) and lets a local/private model be dropped in for healthcare
data (Step 18) without touching pipeline code. The concrete providers returned here
call the real SDKs via the ``api/`` package.
"""
from __future__ import annotations

import os

from ..config import Settings
from .base import LLMError, LLMProvider


def _auto_name() -> str:
    """Pick a real provider from whichever API key is configured."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    raise LLMError("No LLM configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env.")


def get_provider(settings: Settings | None = None, *, provider: str | None = None) -> LLMProvider:
    settings = settings or Settings.from_env()
    name = (provider or settings.llm_provider).lower()
    if name == "auto":
        name = _auto_name()

    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(model=settings.anthropic_model)
    if name == "openai":
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(model=settings.openai_model)
    if name == "mock":
        raise LLMError(
            "The 'mock' provider must be constructed explicitly with scripted "
            "responses (ScriptedLLMProvider); it cannot be auto-resolved."
        )
    raise LLMError(f"Unknown LLM provider: {name!r}")
