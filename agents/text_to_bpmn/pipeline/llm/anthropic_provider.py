"""Anthropic adapter.

A thin bridge from the provider-agnostic :class:`LLMProvider` interface to the real
SDK call, which lives in :mod:`api.anthropic_client`. Keeping the actual network call
in ``api/`` makes that folder the single source of truth for provider SDKs; this class
only adapts shapes (``LLMResult``) and error types (``LLMError``). Imported lazily so
the package still imports without the ``anthropic`` SDK installed.
"""
from __future__ import annotations

from ..config import DEFAULT_ANTHROPIC_MODEL
from .base import LLMError, LLMProvider, LLMResult, import_api_function


class AnthropicProvider(LLMProvider):
    """Force structured output via a single tool the model must call (delegated to api/).

    The API key is read from the environment (``ANTHROPIC_API_KEY``) by the api client,
    keeping secrets in one place.
    """

    name = "anthropic"

    def __init__(self, model: str = DEFAULT_ANTHROPIC_MODEL):
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
        ask = import_api_function("api.anthropic_client", "ask_anthropic_structured", sdk="anthropic")
        try:
            data, raw_text, model = ask(
                task=task, system=system, prompt=prompt, schema=schema,
                max_tokens=max_tokens, model=self.model,
            )
        except Exception as exc:  # normalise SDK/API errors to the pipeline's error type
            raise LLMError(f"Anthropic call failed: {exc}") from exc
        return LLMResult(
            data=data, raw_text=raw_text, provider=self.name, model=model, task=task,
        )
