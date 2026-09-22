"""The structured process Intermediate Representation (IR).

BPMN is compiled from *this*, never directly from text (guideline core rule).
The models encode the semantic fields the guideline requires — AND/OR/XOR,
thresholds, temporal constraints, exceptions, uncertainty — and enforce the
validation rules from Steps 3 and 4:

* invalid element types are rejected (enum-typed);
* a meaningful element with no evidence is rejected unless it is ``DERIVED`` and
  records what it was derived from;
* malformed gateways (no explicit AND/OR/XOR) are rejected;
* relation endpoints must reference existing elements.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from .enums import (
    GATEWAY_TYPES,
    MEANINGFUL_TYPES,
    ElementType,
    GatewayKind,
    RelationType,
    Uncertainty,
)


class Threshold(BaseModel):
    """A numeric decision criterion, preserved exactly from the source (Step 4)."""

    measure: str                       # e.g. "PSA", "IPSS improvement"
    operator: str                      # ">", ">=", "<", "<=", "==", "between"
    value: float
    value_high: float | None = None    # upper bound when operator == "between"
    unit: str | None = None


class TemporalConstraint(BaseModel):
    """A timing rule, e.g. "within 2 months", "every 6 months", "after 30 days"."""

    kind: str                          # "within" | "after" | "before" | "every" | "at"
    value: float
    unit: str                          # "day" | "week" | "month" | "year" | ...
    reference: str | None = None       # anchor event, e.g. "after surgery"


class Element(BaseModel):
    id: str
    type: ElementType
    name: str
    description: str | None = None
    actor: str | None = None
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    thresholds: list[Threshold] = Field(default_factory=list)
    temporal_constraints: list[TemporalConstraint] = Field(default_factory=list)
    gateway_kind: GatewayKind | None = None
    is_exception: bool = False
    evidence_refs: list[str] = Field(default_factory=list)
    derived_from: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    status: Uncertainty = Uncertainty.EXPLICIT

    @model_validator(mode="after")
    def _check(self) -> "Element":
        # Malformed gateway: decisions/gateways must state their split semantics.
        if self.type in GATEWAY_TYPES and self.gateway_kind is None:
            raise ValueError(
                f"Gateway element {self.id!r} ({self.type.value}) must set gateway_kind (AND/OR/XOR)"
            )
        if self.type not in GATEWAY_TYPES and self.gateway_kind is not None:
            raise ValueError(
                f"Element {self.id!r} of type {self.type.value} must not set gateway_kind"
            )
        # Evidence traceability: meaningful elements need evidence unless DERIVED
        # (which must record its provenance) or explicitly MISSING/AMBIGUOUS.
        if self.type in MEANINGFUL_TYPES and not self.evidence_refs:
            if self.status == Uncertainty.DERIVED:
                if not self.derived_from:
                    raise ValueError(
                        f"DERIVED element {self.id!r} must record derived_from source ids"
                    )
            elif self.status not in (Uncertainty.MISSING, Uncertainty.AMBIGUOUS):
                raise ValueError(
                    f"Meaningful element {self.id!r} ({self.type.value}) has no evidence_refs "
                    f"and is not DERIVED/MISSING/AMBIGUOUS (status={self.status.value})"
                )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence for {self.id!r} must be in [0, 1]")
        return self


class Relation(BaseModel):
    id: str
    type: RelationType
    source: str
    target: str
    condition: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    status: Uncertainty = Uncertainty.EXPLICIT

    @model_validator(mode="after")
    def _check(self) -> "Relation":
        if self.type == RelationType.CONDITIONAL and not self.condition:
            raise ValueError(f"Conditional relation {self.id!r} must carry a condition")
        return self


class Process(BaseModel):
    id: str
    name: str
    description: str | None = None
    actors: list[str] = Field(default_factory=list)
    elements: list[Element] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "Process":
        ids = [e.id for e in self.elements]
        dupes = {x for x in ids if ids.count(x) > 1}
        if dupes:
            raise ValueError(f"Duplicate element ids: {sorted(dupes)}")
        idset = set(ids)
        for rel in self.relations:
            missing = {rel.source, rel.target} - idset
            if missing:
                raise ValueError(
                    f"Relation {rel.id!r} references unknown element ids {sorted(missing)}"
                )
        return self

    # --- convenience accessors -------------------------------------------------
    def element(self, element_id: str) -> Element | None:
        return next((e for e in self.elements if e.id == element_id), None)

    def outgoing(self, element_id: str) -> list[Relation]:
        return [r for r in self.relations if r.source == element_id]

    def decisions(self) -> list[Element]:
        return [e for e in self.elements if e.type in GATEWAY_TYPES]
