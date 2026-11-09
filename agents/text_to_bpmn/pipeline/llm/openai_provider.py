"""OpenAI adapter.

A thin bridge from the provider-agnostic :class:`LLMProvider` interface to the real
SDK call in :mod:`api.openai_client`. See :mod:`.anthropic_provider` for the rationale
(``api/`` is the single source of truth for provider SDKs).
"""
from __future__ import annotations

from ..config import DEFAULT_OPENAI_MODEL
from .base import LLMError, LLMProvider, LLMResult, import_api_function


class OpenAIProvider(LLMProvider):
    """Force structured output via a required function tool call (delegated to api/).

    The API key is read from the environment (``OPENAI_API_KEY``) by the api client.
    """

    name = "openai"

    def __init__(self, model: str = DEFAULT_OPENAI_MODEL):
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
        ask = import_api_function("api.openai_client", "ask_openai_structured", sdk="openai")
        try:
            data, raw_text, model = ask(
                task=task, system=system, prompt=prompt, schema=schema,
                max_tokens=max_tokens, model=self.model,
            )
        except Exception as exc:  # normalise SDK/API errors to the pipeline's error type
            raise LLMError(f"OpenAI call failed: {exc}") from exc
        return LLMResult(
            data=data, raw_text=raw_text, provider=self.name, model=model, task=task,
        )
