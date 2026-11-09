"""Provider-agnostic LLM interface.

Every LLM interaction in the pipeline goes through :class:`LLMProvider` and is
*structured*: callers pass a JSON Schema and receive a validated ``dict`` back.
Free-form prose that later gets parsed is intentionally not part of this API
(guideline: "structure over free text").
"""
from __future__ import annotations

import abc
import importlib
from dataclasses import dataclass
from typing import Callable


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


def import_api_function(module: str, function: str, *, sdk: str) -> Callable:
    """Lazily import the SDK call ``module.function`` from the ``api/`` package.

    Only a missing *SDK* (``sdk``) is reported as "not installed"; any other import failure
    (a broken ``api`` module, a missing ``api`` package in a deployment) is reported as such
    instead of being disguised as a missing dependency.
    """
    try:
        return getattr(importlib.import_module(module), function)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing == sdk or missing.startswith(sdk + ".") or (not missing and f"'{sdk}'" in str(exc)):
            raise LLMError(
                f"The '{sdk}' package is not installed. Install it with "
                f"`pip install .[llm]` to use the {sdk} provider."
            ) from exc
        raise LLMError(f"Cannot load the {sdk} client {module}: {exc}") from exc
    except (ImportError, AttributeError) as exc:
        raise LLMError(f"Cannot load the {sdk} client {module}.{function}: {exc}") from exc
