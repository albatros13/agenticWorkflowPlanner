"""The text->eFLINT agent handler.

Intents:

* ``generate_eflint`` — regulatory text (inline, or a ``document_id`` from the regulatory_docs
  library, optionally narrowed to some sections) -> validated eFLINT specification + scenarios,
  via :class:`~agents.text_to_eflint.generation.EflintGenerator` (LLM draft + check/repair);
* ``validate_eflint`` — check an existing specification (spec JSON, or ``.eflint`` text plus
  optional scenario files) and run its scenarios; no LLM.

Each generation is saved under the agent's private storage ``runs/<run_id>/`` (the ``.eflint``
modules, ``spec.json`` and ``report.json``).
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from agents.contract import AgentHandler, AgentManifest, AgentRequest, AgentResult, Capability
from agents.contract.models import JobStatus
from agents.storage import agent_storage

from .generation import EflintGenerator, EflintSpec, check_spec, parse_base_spec, render_files
from .generation.convert import scenario_from_program
from .generation.syntax import Program, parse_program

AGENT_NAME = "text_to_eflint"
GENERATE_EFLINT = "generate_eflint"
VALIDATE_EFLINT = "validate_eflint"

GENERATE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "Regulatory text to formalise."},
        "document_id": {"type": "string", "description": "Alternative to text: a regulatory_docs library document."},
        "sections": {"type": "array", "items": {"type": "string"},
                     "description": "With document_id: keep only blocks whose section path contains one of these."},
        "title": {"type": "string"},
        "domain_context": {"type": "string", "description": "Application setting used for instances and scenarios."},
        "base_spec": {"type": "string", "description": "Existing .eflint specification to extend (no #require)."},
        "scenarios": {"type": "boolean", "default": True},
        "max_repair_rounds": {"type": "integer", "minimum": 0, "maximum": 5, "default": 2},
        "srl_hints": {"type": "boolean", "default": False},
        "language": {"type": "string", "enum": ["en", "nl"], "default": "en"},
    },
}
VALIDATE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "spec": {"type": "object", "description": "A spec as returned in output.spec of generate_eflint."},
        "eflint": {"type": "string", "description": "Alternatively, .eflint source (declarations + initial state)."},
        "scenarios": {"type": "object", "additionalProperties": {"type": "string"},
                      "description": "With eflint: {name: scenario .eflint source} run on top of it."},
        "source_text": {"type": "string", "description": "Optional regulatory text to check source quotes against."},
    },
}


def _default_provider():
    from agents.text_to_bpmn.pipeline.config import Settings  # noqa: PLC0415
    from agents.text_to_bpmn.shaping import select_provider  # noqa: PLC0415

    return select_provider(Settings.from_env())


class TextToEflintHandler(AgentHandler):
    def __init__(
        self,
        *,
        provider=None,
        provider_factory: Callable[[], object] | None = None,
        runs_dir: Path | None = None,
        documents_dir: Path | None = None,
    ):
        self._provider = provider
        self._provider_factory = provider_factory or _default_provider
        self._runs_dir = runs_dir
        self._documents_dir = documents_dir

    def manifest(self) -> AgentManifest:
        return AgentManifest(
            name=AGENT_NAME,
            version="0.2.0",
            description=(
                "Formalise regulatory text as an executable eFLINT specification (facts, duties, "
                "acts, initial state, scenarios). An LLM drafts the spec; it is parsed, type-checked, "
                "checked against the source text and its scenarios are executed, with findings fed "
                "back for repair."
            ),
            supports_async=False,
            capabilities=[
                Capability(intent=GENERATE_EFLINT, description="Regulatory text -> validated eFLINT (+ scenarios).",
                           input_schema=GENERATE_INPUT_SCHEMA),
                Capability(intent=VALIDATE_EFLINT, description="Check an eFLINT spec and run its scenarios (no LLM).",
                           input_schema=VALIDATE_INPUT_SCHEMA),
            ],
        )

    def handle(self, request: AgentRequest) -> AgentResult:
        try:
            if request.intent == GENERATE_EFLINT:
                return self._generate(request)
            if request.intent == VALIDATE_EFLINT:
                return self._validate(request)
        except (ValueError, KeyError) as exc:  # bad payload / unknown document / parse error
            return self._fail(request.intent, str(exc))
        except Exception as exc:  # LLM/provider failures surface as a failed result
            return self._fail(request.intent, f"{type(exc).__name__}: {exc}")
        return self._fail(request.intent,
                          f"Unsupported intent {request.intent!r}; this agent serves {GENERATE_EFLINT!r}, {VALIDATE_EFLINT!r}.")

    # --- intents -----------------------------------------------------------------------------
    def _generate(self, request: AgentRequest) -> AgentResult:
        p = request.payload
        text = self._input_text(p)
        base = parse_base_spec(p["base_spec"]) if (p.get("base_spec") or "").strip() else None
        provider = self._provider or self._provider_factory()
        generator = EflintGenerator(
            provider,
            max_repair_rounds=int(p.get("max_repair_rounds", 2)),
            srl_hints=bool(p.get("srl_hints", False)),
            language=p.get("language", "en"),
        )
        result = generator.generate(
            text, title=p.get("title", ""), domain_context=p.get("domain_context", ""),
            base_spec=base, scenarios=bool(p.get("scenarios", True)),
        )
        run_id = uuid.uuid4().hex[:12]
        output = {"run_id": run_id, "provider": getattr(provider, "name", ""), **result.to_dict()}
        self._save(run_id, output)
        return AgentResult(agent=AGENT_NAME, intent=request.intent, status=JobStatus.succeeded,
                           output=output, artifacts_ref=run_id)

    def _validate(self, request: AgentRequest) -> AgentResult:
        p = request.payload
        if p.get("spec") is not None:
            try:
                spec = EflintSpec.model_validate(p["spec"])
            except ValidationError as exc:
                raise ValueError(f"payload.spec is not a valid spec: {exc}") from exc
        elif (p.get("eflint") or "").strip():
            base = parse_program(p["eflint"], source="<base>")
            spec = parse_base_spec(p["eflint"])
            for name, src in (p.get("scenarios") or {}).items():
                # Any #require in the scenario resolves to the base spec, which is already loaded.
                own = parse_program(src, source=name, resolver=lambda _path, _from: ("", "<base>"),
                                    _seen={"<base>"})
                program = Program(base.statements + own.statements)
                spec.scenarios.append(scenario_from_program(program, name, name=name))
        else:
            raise ValueError("payload.spec or payload.eflint is required")
        report = check_spec(spec, source_text=p.get("source_text"))
        output = {
            "status": "succeeded" if report.ok else "needs_review",
            "summary": report.summary(),
            "findings": [f.model_dump(mode="json") for f in report.findings],
            "scenarios": [s.model_dump(mode="json") for s in report.scenarios],
            "files": render_files(spec),
        }
        return AgentResult(agent=AGENT_NAME, intent=request.intent, status=JobStatus.succeeded, output=output)

    # --- helpers -----------------------------------------------------------------------------
    def _input_text(self, p: dict) -> str:
        if (p.get("text") or "").strip():
            return p["text"]
        doc_id = p.get("document_id")
        if not doc_id:
            raise ValueError("payload.text or payload.document_id is required")
        from agents.regulatory_docs.library import DocumentLibrary  # noqa: PLC0415

        root = self._documents_dir or (
            Path(os.environ["REGDOCS_DIR"]) if os.getenv("REGDOCS_DIR")
            else agent_storage("regulatory_docs").path("documents")
        )
        doc = DocumentLibrary(root).load_document(doc_id)
        sections = [s.lower() for s in p.get("sections") or []]
        if not sections:
            return doc.text
        blocks = [b.text for b in doc.blocks if any(s in b.location.section.lower() for s in sections)]
        if not blocks:
            raise ValueError(f"no blocks of document {doc_id!r} match sections {p['sections']!r}")
        return "\n\n".join(blocks)

    def _runs(self) -> Path:
        if self._runs_dir is not None:
            return self._runs_dir
        if os.getenv("EFLINT_RUNS_DIR"):
            return Path(os.environ["EFLINT_RUNS_DIR"])
        return agent_storage(AGENT_NAME).path("runs")

    def _save(self, run_id: str, output: dict) -> None:
        run = self._runs() / run_id
        run.mkdir(parents=True, exist_ok=True)
        for name, content in output["files"].items():
            (run / name).write_text(content, encoding="utf-8")
        (run / "spec.json").write_text(json.dumps(output["spec"], indent=2, ensure_ascii=False), encoding="utf-8")
        report = {k: v for k, v in output.items() if k not in ("files", "spec")}
        (run / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _fail(intent: str, error: str) -> AgentResult:
        return AgentResult(agent=AGENT_NAME, intent=intent, status=JobStatus.failed, error=error)


__all__ = ["TextToEflintHandler", "GENERATE_EFLINT", "VALIDATE_EFLINT"]
