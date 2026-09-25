"""Deterministic in-memory providers used for tests and offline smoke runs.

``ScriptedLLMProvider`` maps a ``task`` label to a canned response (or a queue of
responses). It records every call so tests can assert on prompts/tasks. No
network, no API keys — this is what lets the whole pipeline run in CI.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from .base import LLMError, LLMProvider, LLMResult


@dataclass
class RecordedCall:
    task: str
    system: str
    prompt: str
    schema: dict


class ScriptedLLMProvider(LLMProvider):
    """Return pre-registered structured responses, routed by ``task``.

    ``responses`` maps a task label to either a single ``dict`` (returned for
    every call with that task) or a list of ``dict`` (consumed one per call).
    """

    name = "mock"

    def __init__(self, responses: dict[str, dict | list[dict]] | None = None, *, model: str = "mock-1"):
        self._single: dict[str, dict] = {}
        self._queues: dict[str, deque] = defaultdict(deque)
        self.model = model
        self.calls: list[RecordedCall] = []
        for task, value in (responses or {}).items():
            self.register(task, value)

    def register(self, task: str, value: dict | list[dict]) -> "ScriptedLLMProvider":
        if isinstance(value, list):
            self._queues[task].extend(value)
        else:
            self._single[task] = value
        return self

    def generate_json(
        self,
        *,
        task: str,
        system: str,
        prompt: str,
        schema: dict,
        max_tokens: int = 4096,
    ) -> LLMResult:
        self.calls.append(RecordedCall(task=task, system=system, prompt=prompt, schema=schema))
        if self._queues.get(task):
            data = self._queues[task].popleft()
        elif task in self._single:
            data = self._single[task]
        else:
            raise LLMError(
                f"ScriptedLLMProvider has no response registered for task {task!r}. "
                f"Registered: {sorted(set(self._single) | set(self._queues))}"
            )
        import json

        return LLMResult(
            data=data,
            raw_text=json.dumps(data),
            provider=self.name,
            model=self.model,
            task=task,
        )
