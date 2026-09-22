"""OpenAI adapter. Imported lazily so the package works without the SDK."""
from __future__ import annotations

import json
import os

from .base import LLMError, LLMProvider, LLMResult

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")


class OpenAIProvider(LLMProvider):
    """Force structured output via a required function tool call."""

    name = "openai"

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None):
        try:
            from openai import OpenAI  # noqa: PLC0415 - lazy import
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise LLMError(
                "The 'openai' package is not installed. Install it with "
                "`pip install .[llm]` to use the OpenAI provider."
            ) from exc
        self.model = model
        self._client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

    def generate_json(
        self,
        *,
        task: str,
        system: str,
        prompt: str,
        schema: dict,
        max_tokens: int = 4096,
    ) -> LLMResult:
        fn_name = "emit_" + task
        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": fn_name,
                        "description": f"Return the structured result for task '{task}'.",
                        "parameters": schema,
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": fn_name}},
        )
        choice = resp.choices[0].message
        if not choice.tool_calls:
            raise LLMError("OpenAI response contained no tool call")
        raw = choice.tool_calls[0].function.arguments
        return LLMResult(
            data=self._loads(raw),
            raw_text=raw,
            provider=self.name,
            model=self.model,
            task=task,
        )
