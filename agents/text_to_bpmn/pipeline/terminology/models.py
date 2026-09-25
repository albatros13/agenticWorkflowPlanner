"""Terminology mapping records (guideline Step 5).

A mapping never replaces the source wording; it annotates it. The original
``surface_form`` is always kept alongside the normalized concept so clinical
meaning is auditable and low-confidence matches stay reviewable.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class MatchType(str, Enum):
    EXACT = "exact"
    SYNONYM = "synonym"
    PARTIAL = "partial"
    NONE = "none"          # no ontology match exists — this is allowed


class TermMapping(BaseModel):
    surface_form: str                  # exactly as written in the source
    normalized_label: str | None = None
    ontology: str | None = None        # e.g. SNOMED CT, LOINC, RxNorm, ATC, ICD
    concept_id: str | None = None
    match_type: MatchType = MatchType.NONE
    confidence: float = 0.0

    @property
    def is_reviewable(self) -> bool:
        """Low-confidence or partial matches must remain flagged for review."""
        return self.match_type in (MatchType.PARTIAL, MatchType.SYNONYM) or self.confidence < 0.75


class TerminologyAnnotation(BaseModel):
    """Mappings attached to one IR element, without changing the element."""

    element_id: str
    element_name: str
    mappings: list[TermMapping] = Field(default_factory=list)
