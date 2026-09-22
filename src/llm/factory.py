"""Resolve an :class:`LLMProvider` from settings.

Keeping this behind a factory is what makes the model/provider replaceable
(guideline Step 1) and lets a local/private model be dropped in for healthcare
data (Step 18) without touching pipeline code.
"""
from __future__ import annotations

from ..config import Settings
from .base import LLMError, LLMProvider


def get_provider(settings: Settings | None = None, *, provider: str | None = None) -> LLMProvider:
    settings = settings or Settings.from_env()
    name = (provider or settings.llm_provider).lower()

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
