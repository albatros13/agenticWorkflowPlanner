"""Anthropic adapter.

A thin bridge from the provider-agnostic :class:`LLMProvider` interface to the real
SDK call, which lives in :mod:`api.anthropic_client`. Keeping the actual network call
in ``api/`` makes that folder the single source of truth for provider SDKs; this class
only adapts shapes (``LLMResult``) and error types (``LLMError``). Imported lazily so
the package still imports without the ``anthropic`` SDK installed.
"""
from __future__ import annotations

import os

from .base import LLMError, LLMProvider, LLMResult

# Latest, most capable default (see project model guidance).
DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")


class AnthropicProvider(LLMProvider):
    """Force structured output via a single tool the model must call (delegated to api/)."""

    name = "anthropic"

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None):
        # ``api_key`` is accepted for interface compatibility; the api client reads the
        # key from the environment (ANTHROPIC_API_KEY), keeping secrets in one place.
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
            from api.anthropic_client import ask_anthropic_structured  # noqa: PLC0415 - lazy
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise LLMError(
                "The 'anthropic' package is not installed. Install it with "
                "`pip install .[llm]` to use the Anthropic provider."
            ) from exc
        try:
            data, raw_text, model = ask_anthropic_structured(
                task=task, system=system, prompt=prompt, schema=schema,
                max_tokens=max_tokens, model=self.model,
            )
        except Exception as exc:  # normalise SDK/API errors to the pipeline's error type
            raise LLMError(f"Anthropic call failed: {exc}") from exc
        return LLMResult(
            data=data, raw_text=raw_text, provider=self.name, model=model, task=task,
        )
