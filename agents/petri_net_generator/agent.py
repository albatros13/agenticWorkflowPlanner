"""The Petri net generator agent handler (intent ``generate_petri_net``).

Validates the payload into a :class:`GenerateRequest`, runs :func:`.generator.generate`, and wraps
the typed response in an :class:`AgentResult`. ``needs_clarification`` is a *successful* agent
result (the agent did its job: it found blocking ambiguities); callers read ``output.status``.

PM4Py is imported lazily (inside :meth:`handle`) so the coordinator can register this handler
without the ``petri_net`` extra installed.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from agents.contract import AgentHandler, AgentManifest, AgentRequest, AgentResult, Capability
from agents.contract.models import JobStatus
from agents.runs import runs_root
from agents.text_to_bpmn.shaping import default_provider

from .config import Limits
from .schemas import GenerateRequest, GenerateResponse

log = logging.getLogger(__name__)

AGENT_NAME = "petri_net_generator"
GENERATE_PETRI_NET = "generate_petri_net"


class PetriNetGeneratorHandler(AgentHandler):
    """``provider`` / ``provider_factory`` configure the LLM used for ``input_type=text`` only,
    mirroring :class:`~agents.text_to_bpmn.agent.TextToBpmnHandler`."""

    def __init__(
        self,
        *,
        provider=None,
        provider_factory: Callable[[], object] | None = None,
        limits: Limits | None = None,
        runs_dir: Path | None = None,
    ):
        self._provider = provider
        self._provider_factory = provider_factory or default_provider
        self._limits = limits
        self._runs_dir = runs_dir

    def manifest(self) -> AgentManifest:
        return AgentManifest(
            name=AGENT_NAME,
            version="0.1.0",
            description=(
                "Generate an ordinary workflow Petri net from BPMN 2.0 XML, the text_to_bpmn graph "
                "IR, or a natural-language process description (via a validated process-algebra "
                "IR). Returns PNML, a model summary, assumptions and validation findings; asks for "
                "clarification when essential process semantics are ambiguous."
            ),
            supports_async=False,
            capabilities=[Capability(
                intent=GENERATE_PETRI_NET,
                description="BPMN XML / IR / text -> workflow Petri net (PNML).",
                input_schema=GenerateRequest.model_json_schema(),
                output_schema=GenerateResponse.model_json_schema(),
            )],
        )

    def _runs(self) -> Path:
        return runs_root(AGENT_NAME, "PETRI_NET_RUNS_DIR", self._runs_dir)

    def _fail(self, request: AgentRequest, error: str) -> AgentResult:
        return AgentResult(agent=AGENT_NAME, intent=request.intent, status=JobStatus.failed, error=error)

    def handle(self, request: AgentRequest) -> AgentResult:
        if request.intent != GENERATE_PETRI_NET:
            return self._fail(request, f"Unsupported intent {request.intent!r}; this agent serves "
                                       f"{GENERATE_PETRI_NET!r}.")
        try:
            gen_request = GenerateRequest.model_validate(request.payload)
        except ValidationError as exc:
            return self._fail(request, "Invalid payload: " + "; ".join(e["msg"] for e in exc.errors()[:5]))

        from .generator import generate  # noqa: PLC0415 - pulls in PM4Py

        try:
            response = generate(
                gen_request,
                provider=lambda: self._provider or self._provider_factory(),
                limits=self._limits or Limits.from_env(),
                runs_dir=self._runs(),
            )
        except Exception:  # keep stack traces out of client responses
            log.exception("petri net generation failed (correlation_id=%s)", request.correlation_id)
            return self._fail(request, "Internal error during Petri net generation "
                                       f"(correlation_id={request.correlation_id}).")

        output = response.model_dump(mode="json")
        if response.status == "failed":
            return AgentResult(agent=AGENT_NAME, intent=request.intent, status=JobStatus.failed,
                               output=output, artifacts_ref=response.pnml_ref,
                               error="; ".join(f"{e.code}: {e.message}" for e in response.errors))
        return AgentResult(agent=AGENT_NAME, intent=request.intent, status=JobStatus.succeeded,
                           output=output, artifacts_ref=response.pnml_ref)
