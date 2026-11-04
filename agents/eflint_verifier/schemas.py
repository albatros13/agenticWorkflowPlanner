"""Payload of the ``verify_eflint`` intent (the wrapper's own contract, mapped onto the service's).

The fields mirror the external service's job request (``inputs.physical`` / ``normative`` /
``qualification`` / ``analysis`` / ``scenario`` and ``options``) but flattened, with conveniences:

* ``operation="auto"`` picks ``model-check`` / ``compliance-check`` / ``plan`` from the declarations
  in ``analysis`` (and ``compliance-check`` when there is no analysis);
* without ``analysis``, a compliance check over *all* physical transitions is generated
  ("no normative violation in any execution within the bound");
* Petri net PNML is normalised to the service's profile and naming rules (see :mod:`.pnml`).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Operation = Literal["auto", "model-check", "compliance-check", "plan", "run", "explore", "validate", "compile"]
ANALYSIS_OPERATIONS = ("model-check", "compliance-check", "plan")


class ModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Literal["eflint", "petri-net"]
    format: Literal["pn", "pnml"] | None = None   # Petri nets only; pn when omitted
    source: str = Field(min_length=1)
    dependencies: dict[str, str] = Field(default_factory=dict)  # eFLINT #require'd files

    @model_validator(mode="after")
    def _format_matches_language(self):
        if self.language == "eflint" and self.format is not None:
            raise ValueError("format applies to petri-net models only")
        if self.language == "petri-net" and self.format is None:
            self.format = "pnml" if self.source.lstrip().startswith("<") else "pn"
        return self


class VerifyOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_states: int | None = Field(default=None, ge=0, le=2_147_483_647)
    all: bool | None = None
    verbose: bool | None = None
    timeout_seconds: int | None = Field(default=None, gt=0)


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Operation = "auto"
    target: Literal["run", "explore", "model-check", "compliance-check", "plan"] | None = None
    physical: ModelInput
    normative: ModelInput | None = None
    qualification: str | None = None
    analysis: str | None = None
    scenario: str | None = None
    options: VerifyOptions = Field(default_factory=VerifyOptions)
    normalize_pnml: bool = True

    @model_validator(mode="after")
    def _composition_is_complete(self):
        if (self.normative is None) != (not (self.qualification or "").strip()):
            raise ValueError("normative and qualification must be supplied together")
        return self


class PropertyResult(BaseModel):
    id: str
    outcome: str
    bound: int | None = None
    witness: list[list[str]] = Field(default_factory=list)  # actions per step of the first trace
    completion: dict | str | None = None


Verdict = Literal["passed", "failed", "inconclusive"]


class VerifyResponse(BaseModel):
    run_id: str
    job_id: str | None = None
    operation: str
    verdict: Verdict
    summary: str
    properties: list[PropertyResult] = Field(default_factory=list)
    analysis: str | None = None          # the analysis actually submitted (incl. generated default)
    analysis_generated: bool = False
    physical_transitions: list[str] = Field(default_factory=list)
    renamed: dict[str, str] = Field(default_factory=dict)
    pnml_changes: list[str] = Field(default_factory=list)
    service_url: str
    elapsed_s: float | None = None
    raw_result: dict | None = None
