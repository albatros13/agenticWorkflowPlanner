"""The Anthropic SDK call behind the pipeline's ``AnthropicProvider``.

``api/`` is the single call site for provider SDKs. The client is created lazily, on first use,
so the API key is read *after* ``.env`` has been loaded (``Settings.from_env``) and importing this
module has no side effects (no network client, no logging configuration, no prints).
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from anthropic import Anthropic

REQUEST_TIMEOUT_S = 120


@lru_cache(maxsize=4)
def _client(api_key: str) -> Anthropic:
    return Anthropic(api_key=api_key)


def ask_anthropic_structured(*, task, system, prompt, schema, max_tokens=4096, model):
    """Force a single tool call so Claude returns JSON conforming to ``schema``.

    Returns ``(data: dict, raw_text: str, model: str)``. Raises ``RuntimeError`` when the
    key is missing, the output was truncated, or the model returns no tool_use block;
    SDK/API errors propagate.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set; cannot call Anthropic.")
    tool = {
        "name": "emit_" + task,
        "description": f"Return the structured result for task '{task}'.",
        "input_schema": schema,
    }
    response = _client(api_key).messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": prompt}],
        timeout=REQUEST_TIMEOUT_S,
    )
    if getattr(response, "stop_reason", None) == "max_tokens":
        # The tool input is cut off mid-JSON; returning it would surface later as a confusing
        # schema-validation error (missing required fields), so fail with the real cause.
        raise RuntimeError(
            f"Anthropic output was truncated at max_tokens={max_tokens} before the structured "
            f"result for task '{task}' was complete; shorten the input or raise max_tokens"
        )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input, json.dumps(block.input), model
    raise RuntimeError("Anthropic response contained no tool_use block")
