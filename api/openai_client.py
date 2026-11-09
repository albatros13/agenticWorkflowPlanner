"""The OpenAI SDK call behind the pipeline's ``OpenAIProvider``.

See :mod:`api.anthropic_client`: the client is created lazily and importing has no side effects.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from openai import OpenAI

REQUEST_TIMEOUT_S = 120


@lru_cache(maxsize=4)
def _client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key)


def ask_openai_structured(*, task, system, prompt, schema, max_tokens=4096, model):
    """Force a function tool call so the model returns JSON conforming to ``schema``.

    Returns ``(data: dict, raw_text: str, model: str)``. Raises ``RuntimeError`` when the
    key is missing, the output was truncated, or the model returns no tool call; SDK/API
    errors propagate.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set; cannot call OpenAI.")
    fn_name = "emit_" + task
    response = _client(api_key).chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        timeout=REQUEST_TIMEOUT_S,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        tools=[{
            "type": "function",
            "function": {
                "name": fn_name,
                "description": f"Return the structured result for task '{task}'.",
                "parameters": schema,
            },
        }],
        tool_choice={"type": "function", "function": {"name": fn_name}},
    )
    choice = response.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise RuntimeError(
            f"OpenAI output was truncated at max_tokens={max_tokens} before the structured "
            f"result for task '{task}' was complete; shorten the input or raise max_tokens"
        )
    message = choice.message
    if not message.tool_calls:
        raise RuntimeError("OpenAI response contained no tool call")
    raw = message.tool_calls[0].function.arguments
    return json.loads(raw), raw, model
