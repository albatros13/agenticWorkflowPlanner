"""Anthropic adapter. Imported lazily so the package works without the SDK."""
from __future__ import annotations

import json
import os

from .base import LLMError, LLMProvider, LLMResult

# Latest, most capable default (see project model guidance).
DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")


class AnthropicProvider(LLMProvider):
    """Force structured output via a single tool the model must call."""

    name = "anthropic"

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None):
        try:
            from anthropic import Anthropic  # noqa: PLC0415 - lazy import
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise LLMError(
                "The 'anthropic' package is not installed. Install it with "
                "`pip install .[llm]` to use the Anthropic provider."
            ) from exc
        self.model = model
        self._client = Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))

    def generate_json(
        self,
        *,
        task: str,
        system: str,
        prompt: str,
        schema: dict,
        max_tokens: int = 4096,
    ) -> LLMResult:
        tool = {
            "name": "emit_" + task,
            "description": f"Return the structured result for task '{task}'.",
            "input_schema": schema,
        }
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": prompt}],
            timeout=120,
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                data = block.input
                return LLMResult(
                    data=data,
                    raw_text=json.dumps(data),
                    provider=self.name,
                    model=self.model,
                    task=task,
                )
        raise LLMError("Anthropic response contained no tool_use block")
