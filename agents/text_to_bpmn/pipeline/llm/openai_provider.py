"""OpenAI adapter.

A thin bridge from the provider-agnostic :class:`LLMProvider` interface to the real
SDK call in :mod:`api.openai_client`. See :mod:`.anthropic_provider` for the rationale
(``api/`` is the single source of truth for provider SDKs).
"""
from __future__ import annotations

import os

from .base import LLMError, LLMProvider, LLMResult

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")


class OpenAIProvider(LLMProvider):
    """Force structured output via a required function tool call (delegated to api/)."""

    name = "openai"

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None):
        # ``api_key`` accepted for interface compatibility; the api client reads it from env.
        self.model = model

    def generate_json(
        self,
        *,
        task: str,
        system: str,
        prompt: str,
        schema: dict,
        max_tokens: int = 4096,
    ) -> LLMResult:
        try:
            from api.openai_client import ask_openai_structured  # noqa: PLC0415 - lazy
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise LLMError(
                "The 'openai' package is not installed. Install it with "
                "`pip install .[llm]` to use the OpenAI provider."
            ) from exc
        try:
            data, raw_text, model = ask_openai_structured(
                task=task, system=system, prompt=prompt, schema=schema,
                max_tokens=max_tokens, model=self.model,
            )
        except Exception as exc:  # normalise SDK/API errors to the pipeline's error type
            raise LLMError(f"OpenAI call failed: {exc}") from exc
        return LLMResult(
            data=data, raw_text=raw_text, provider=self.name, model=model, task=task,
        )
