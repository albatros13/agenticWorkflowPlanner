"""Provider-agnostic LLM interface.

Every LLM interaction in the pipeline goes through :class:`LLMProvider` and is
*structured*: callers pass a JSON Schema and receive a validated ``dict`` back.
Free-form prose that later gets parsed is intentionally not part of this API
(guideline: "structure over free text").
"""
from __future__ import annotations

import abc
import json
from dataclasses import dataclass


class LLMError(RuntimeError):
    """Raised when a provider cannot return a usable structured response."""


@dataclass(frozen=True)
class LLMResult:
    """A structured model response plus provenance for the audit log."""

    data: dict
    raw_text: str
    provider: str
    model: str
    task: str


class LLMProvider(abc.ABC):
    """Base class for all model/provider adapters.

    ``task`` is a short stable label (e.g. ``"evidence_extraction"``) used for
    logging, audit keying, and — for test providers — response routing.
    """

    name: str = "base"

    @abc.abstractmethod
    def generate_json(
        self,
        *,
        task: str,
        system: str,
        prompt: str,
        schema: dict,
        max_tokens: int = 4096,
    ) -> LLMResult:
        """Return a JSON object conforming to ``schema``."""

    @staticmethod
    def _loads(text: str) -> dict:
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise LLMError(f"Model did not return valid JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise LLMError("Structured response must be a JSON object")
        return obj
