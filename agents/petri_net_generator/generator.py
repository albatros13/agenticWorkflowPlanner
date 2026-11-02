"""Orchestration: request -> (IR | BPMN) -> Petri net -> validation -> exports.

Kept free of transport concerns so it is testable without the HTTP service or the agent contract.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from agents.text_to_bpmn.pipeline.llm.base import LLMError, LLMProvider

from .bpmn_converter import BpmnConversionError, bpmn_to_petri_net
from .compiler import compile_ir
from .config import Limits
from .exporters import NetExporter, to_dot, to_pnml
from .ir import ProcessIR, check_ir
from .net_validation import check_soundness, marking_dict, source_and_sink, structural_findings
from .schemas import (
    Finding,
    GenerateRequest,
    GenerateResponse,
    InputType,
    NetSummary,
    Severity,
    ValidationReport,
)

EXPORTERS: dict[str, NetExporter] = {"pnml": to_pnml, "dot": to_dot}


def _failed(request: GenerateRequest, *errors: Finding, **extra) -> GenerateResponse:
    return GenerateResponse(status="failed", input_type=request.input_type, errors=list(errors), **extra)


def _error(code: str, message: str) -> Finding:
    return Finding(severity=Severity.error, code=code, message=message)


def _pydantic_errors(exc: ValidationError, code: str) -> Finding:
    parts = [f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in exc.errors()[:5]]
    return _error(code, "; ".join(parts))


def generate(
    request: GenerateRequest,
    *,
    provider: Callable[[], LLMProvider],
    limits: Limits,
    runs_dir: Path | None = None,
) -> GenerateResponse:
    """Run one generation. ``provider`` is only called for ``input_type == text``."""
    raw = request.text or request.bpmn_xml or json.dumps(request.ir or request.text_to_bpmn_ir)
    if len(raw.encode("utf-8")) > limits.max_input_bytes:
        return _failed(request, _error("input_too_large",
                                       f"Input exceeds the {limits.max_input_bytes}-byte limit."))

    ir: ProcessIR | None = None
    warnings: list[Finding] = []
    try:
        if request.input_type in (InputType.text, InputType.ir):
            if request.input_type == InputType.text:
                from .text_to_ir import extract_ir  # noqa: PLC0415 - keep LLM wiring off the BPMN path

                ir = extract_ir(provider(), request.text, process_name=request.model_name or "")
            else:
                ir = ProcessIR.model_validate(request.ir)
            if request.model_name:
                ir = ir.model_copy(update={"process_name": request.model_name})
            ir_dump = ir.model_dump(mode="json")
            if ir.blocking_ambiguities:
                return GenerateResponse(
                    status="needs_clarification", input_type=request.input_type,
                    process_name=ir.process_name, assumptions=ir.assumptions,
                    ambiguities=[a.model_dump() for a in ir.ambiguities], ir=ir_dump,
                )
            ir_findings = check_ir(ir, max_nodes=limits.max_ir_nodes, max_depth=limits.max_ir_depth)
            errors = [f for f in ir_findings if f.severity == Severity.error]
            if errors:
                return _failed(request, *errors, ir=ir_dump, process_name=ir.process_name)
            warnings += [f for f in ir_findings if f.severity != Severity.error]
            net, im, fm = compile_ir(ir)
            process_name = ir.process_name or net.name
        else:
            bpmn_xml = request.bpmn_xml
            if request.input_type == InputType.text_to_bpmn_ir:
                from agents.text_to_bpmn.pipeline.bpmn.compiler import BpmnCompiler  # noqa: PLC0415
                from agents.text_to_bpmn.pipeline.ir.models import Process  # noqa: PLC0415

                bpmn_xml = BpmnCompiler().compile(Process.model_validate(request.text_to_bpmn_ir)).bpmn_xml
            net, im, fm, bpmn_findings, process_name = bpmn_to_petri_net(
                bpmn_xml, max_bytes=limits.max_input_bytes, name=request.model_name)
            warnings += bpmn_findings
    except BpmnConversionError as exc:
        return _failed(request, *exc.findings)
    except ValidationError as exc:
        return _failed(request, _pydantic_errors(exc, "invalid_ir"))
    except LLMError as exc:
        return _failed(request, _error("llm_error", str(exc)))

    findings = structural_findings(net, im, fm)
    structurally_valid = not any(f.code in _STRUCTURE_CODES for f in findings)
    workflow_net = structurally_valid and not any(f.severity == Severity.error for f in findings)
    soundness, detail = "not_checked", "Soundness check not requested."
    if not workflow_net:
        detail = "Not a workflow net; soundness is undefined."
    elif request.check_soundness:
        soundness, detail = check_soundness(net, im, fm, max_states=limits.max_states,
                                            timeout_s=limits.soundness_timeout_s)

    source, sink = source_and_sink(net)
    labels = sorted({t.label for t in net.transitions if t.label is not None})
    response = GenerateResponse(
        status="succeeded" if workflow_net else "failed",
        input_type=request.input_type,
        process_name=process_name,
        net_summary=NetSummary(
            places=len(net.places), transitions=len(net.transitions),
            silent_transitions=sum(1 for t in net.transitions if t.label is None),
            arcs=len(net.arcs), source_place=source, sink_place=sink, visible_labels=labels,
        ),
        initial_marking=marking_dict(im),
        final_marking=marking_dict(fm),
        validation=ValidationReport(structurally_valid=structurally_valid, workflow_net=workflow_net,
                                    soundness=soundness, soundness_detail=detail, findings=findings),
        assumptions=ir.assumptions if ir else [],
        ambiguities=[a.model_dump() for a in ir.ambiguities] if ir else [],
        warnings=warnings,
        errors=[f for f in findings if f.severity == Severity.error],
        ir=ir.model_dump(mode="json") if ir else None,
    )

    pnml = to_pnml(net, im, fm)
    if runs_dir is not None:
        run_id = uuid.uuid4().hex[:12]
        run = runs_dir / run_id
        run.mkdir(parents=True, exist_ok=True)
        (run / "net.pnml").write_text(pnml, encoding="utf-8")
        if ir is not None:
            (run / "ir.json").write_text(json.dumps(ir.model_dump(mode="json"), indent=2), encoding="utf-8")
        response.pnml_ref = run_id
    if "pnml" in request.output_formats and len(pnml.encode("utf-8")) <= limits.max_inline_pnml_bytes:
        response.pnml = pnml
    elif "pnml" in request.output_formats:
        response.warnings.append(Finding(
            severity=Severity.info, code="pnml_not_inlined",
            message=f"PNML exceeds {limits.max_inline_pnml_bytes} bytes; fetch it via pnml_ref."))
    if "dot" in request.output_formats:
        response.dot = EXPORTERS["dot"](net, im, fm)
    if runs_dir is not None:
        (runs_dir / response.pnml_ref / "response.json").write_text(
            response.model_dump_json(indent=2, exclude={"pnml", "dot"}), encoding="utf-8")
    return response


# Findings that mean the net itself is malformed (as opposed to "valid net, but not a workflow net").
_STRUCTURE_CODES = {"duplicate_id", "dangling_arc", "invalid_arc", "weighted_arc",
                    "invalid_initial_marking", "invalid_final_marking"}
