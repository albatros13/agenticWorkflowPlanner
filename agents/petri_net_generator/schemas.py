"""Typed request/response models for the Petri net generator (agent payload + ``POST /generate``)."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class InputType(str, Enum):
    text = "text"                        # NL description -> LLM -> process-algebra IR -> net
    ir = "ir"                            # a process-algebra IR (e.g. edited / clarified) -> net
    bpmn_xml = "bpmn_xml"                # BPMN 2.0 XML -> PM4Py -> net
    text_to_bpmn_ir = "text_to_bpmn_ir"  # the text_to_bpmn agent's graph IR -> BPMN -> net


_REQUIRED_FIELD = {
    InputType.text: "text",
    InputType.ir: "ir",
    InputType.bpmn_xml: "bpmn_xml",
    InputType.text_to_bpmn_ir: "text_to_bpmn_ir",
}


class Severity(str, Enum):
    error = "error"
    warning = "warning"
    info = "info"


class Finding(BaseModel):
    severity: Severity
    code: str
    message: str
    element_ids: list[str] = Field(default_factory=list)


class GenerateRequest(BaseModel):
    input_type: InputType
    text: str | None = None
    ir: dict[str, Any] | None = None
    bpmn_xml: str | None = None
    text_to_bpmn_ir: dict[str, Any] | None = None
    model_name: str | None = None
    output_formats: list[Literal["pnml", "dot"]] = Field(default_factory=lambda: ["pnml"])
    check_soundness: bool = True

    @model_validator(mode="after")
    def _required_input_present(self) -> "GenerateRequest":
        field = _REQUIRED_FIELD[self.input_type]
        value = getattr(self, field)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"input_type={self.input_type.value!r} requires a non-empty {field!r} field")
        return self


class NetSummary(BaseModel):
    places: int
    transitions: int
    silent_transitions: int
    arcs: int
    source_place: str | None
    sink_place: str | None
    visible_labels: list[str]


class ValidationReport(BaseModel):
    structurally_valid: bool
    workflow_net: bool
    # "passed" only when the bounded state-space check actually completed; never inferred
    # from the net merely parsing or being connected.
    soundness: Literal["passed", "failed", "not_checked"] = "not_checked"
    soundness_detail: str = ""
    findings: list[Finding] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    status: Literal["succeeded", "needs_clarification", "failed"]
    input_type: InputType
    process_name: str = ""
    net_summary: NetSummary | None = None
    initial_marking: dict[str, int] = Field(default_factory=dict)
    final_marking: dict[str, int] = Field(default_factory=dict)
    validation: ValidationReport | None = None
    assumptions: list[str] = Field(default_factory=list)
    ambiguities: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[Finding] = Field(default_factory=list)
    errors: list[Finding] = Field(default_factory=list)
    ir: dict[str, Any] | None = None
    pnml: str | None = None
    pnml_ref: str | None = None  # run_id under the agent's private storage
    dot: str | None = None
