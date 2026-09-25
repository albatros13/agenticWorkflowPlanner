"""The text->BPMN agent handler.

Runs the existing pipeline exactly as the demo API did (ingest_text -> evidence -> interpret ->
terminology -> compile_bpmn), then packages the result into an :class:`AgentResult`. The
pipeline code in ``src/`` is not modified.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Callable

from agents.contract import AgentHandler, AgentManifest, AgentRequest, AgentResult, Capability
from agents.contract.models import JobStatus
from agents.storage import agent_storage

from .pipeline.config import Settings
from .pipeline.llm.base import LLMError, LLMProvider
from .pipeline.pipeline.artifacts import ArtifactStore
from .pipeline.pipeline.runner import Pipeline
from .pipeline.validation.schema import check_connectivity, validate_bpmn, validate_dmn
from .shaping import build_resources, select_provider

ENGINEER_PROCESS = "engineer_process"


class TextToBpmnHandler(AgentHandler):
    """Wraps :class:`.pipeline.pipeline.runner.Pipeline` behind the agent contract.

    The agent is configured with its **LLM calling interface**, in priority order:

    * ``provider`` — a concrete :class:`LLMProvider` instance (tests inject
      :class:`ScriptedLLMProvider`; a coordinator may pass an already-resolved one).
    * ``provider_factory`` — a callable ``(Settings) -> LLMProvider`` used to build a
      provider per request. Defaults to :func:`select_provider`, which resolves a real
      api-backed provider (Anthropic/OpenAI) from configuration.

    This keeps model/provider selection out of the pipeline and fully injectable.
    """

    def __init__(
        self,
        *,
        provider: LLMProvider | None = None,
        provider_factory: Callable[[Settings], LLMProvider] | None = None,
        settings: Settings | None = None,
    ):
        self._provider = provider
        self._provider_factory = provider_factory or select_provider
        self._settings = settings

    def manifest(self) -> AgentManifest:
        return AgentManifest(
            name="text_to_bpmn",
            version="0.1.0",
            description="Natural-language process text -> validated BPMN 2.0 (+DMN).",
            supports_async=False,
            capabilities=[
                Capability(
                    intent=ENGINEER_PROCESS,
                    description="Compile a process description into BPMN/DMN with provenance.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "minLength": 1},
                            "process_name": {"type": "string"},
                        },
                        "required": ["text"],
                    },
                )
            ],
        )

    def handle(self, request: AgentRequest) -> AgentResult:
        if request.intent != ENGINEER_PROCESS:
            return AgentResult(
                agent="text_to_bpmn",
                intent=request.intent,
                status=JobStatus.failed,
                error=f"Unsupported intent {request.intent!r}; this agent serves {ENGINEER_PROCESS!r}.",
            )

        text = (request.payload.get("text") or "").strip()
        if not text:
            return AgentResult(
                agent="text_to_bpmn",
                intent=request.intent,
                status=JobStatus.failed,
                error="payload.text is required and must be non-empty.",
            )
        process_name = request.payload.get("process_name", "")

        settings = self._settings or Settings.from_env()
        try:
            provider = self._provider or self._provider_factory(settings)
        except LLMError as exc:
            return AgentResult(
                agent="text_to_bpmn", intent=request.intent,
                status=JobStatus.failed, error=str(exc),
            )

        run_id = uuid.uuid4().hex[:12]
        # Private per-agent output storage (config/storage.json); RUNS_DIR overrides for tests/ops.
        runs_dir = Path(os.environ["RUNS_DIR"]) if os.getenv("RUNS_DIR") else agent_storage("text_to_bpmn").path("runs")
        store = ArtifactStore.new_run(runs_dir, run_id)
        pipe = Pipeline(provider, store=store, settings=settings)

        try:
            pipe.ingest_text(text)
            evidence = pipe.extract_evidence()
            process = pipe.interpret(process_name=process_name)
            annotations = pipe.ground_terminology()
            model = pipe.compile_bpmn()
        except Exception as exc:  # surface pipeline/LLM failures
            return AgentResult(
                agent="text_to_bpmn", intent=request.intent, status=JobStatus.failed,
                artifacts_ref=run_id, error=f"{type(exc).__name__}: {exc}",
            )

        vb = validate_bpmn(model.bpmn_xml)
        vd = validate_dmn(model.dmn_xml) if model.dmn_xml else None
        cc = check_connectivity(model.bpmn_xml)

        output = {
            "run_id": run_id,
            "provider": provider.name,
            "bpmn_xml": model.bpmn_xml,
            "dmn_xml": model.dmn_xml,
            "resources": build_resources(evidence, process, annotations, model),
            "validation": {
                "bpmn_valid": vb.ok,
                "bpmn_errors": vb.errors[:5],
                "dmn_valid": vd.ok if vd else None,
                "connectivity_ok": cc.ok,
                "connectivity_errors": cc.errors[:5],
            },
        }
        return AgentResult(
            agent="text_to_bpmn", intent=request.intent,
            status=JobStatus.succeeded, output=output, artifacts_ref=run_id,
        )
