"""Terminology resolver (guideline Step 5).

Rules enforced here:
* never silently replace the original wording — mappings are annotations;
* preserve the source term alongside the normalized terminology;
* low-confidence mappings remain reviewable;
* an ontology match is not required when none exists (returns ``match_type=none``).

Mappings are loaded from a configurable JSON file so the controlled vocabulary
set is deployment-specific and lives outside code.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import PIPELINE_ROOT
from ..ir.models import Process
from .models import MatchType, TermMapping, TerminologyAnnotation

_DEFAULT_CONFIG = PIPELINE_ROOT / "config" / "terminology.json"


class TerminologyResolver:
    def __init__(self, mappings: list[TermMapping]):
        # Index by lower-cased surface form for lookup; keep originals intact.
        self._by_surface: dict[str, TermMapping] = {
            m.surface_form.lower(): m for m in mappings
        }

    @classmethod
    def from_config(cls, path: Path | None = None) -> "TerminologyResolver":
        path = path or _DEFAULT_CONFIG
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([TermMapping.model_validate(m) for m in data.get("mappings", [])])

    def resolve(self, surface_form: str) -> TermMapping:
        """Return a mapping for ``surface_form``; a ``NONE`` mapping if unmatched.

        The returned mapping always carries the *exact* input surface form.
        """
        hit = self._by_surface.get(surface_form.lower())
        if hit is not None:
            return hit.model_copy(update={"surface_form": surface_form})
        return TermMapping(surface_form=surface_form, match_type=MatchType.NONE, confidence=0.0)

    def find_terms(self, text: str) -> list[str]:
        """Return known surface forms occurring in ``text`` (case-insensitive)."""
        found: list[str] = []
        low = text.lower()
        for surface in self._by_surface:
            if re.search(rf"\b{re.escape(surface)}\b", low):
                found.append(self._by_surface[surface].surface_form)
        return found

    def annotate_process(self, process: Process) -> list[TerminologyAnnotation]:
        """Attach mappings to each element WITHOUT modifying the element names."""
        annotations: list[TerminologyAnnotation] = []
        for element in process.elements:
            haystack = " ".join(
                filter(None, [element.name, element.description, *element.conditions])
            )
            surfaces = self.find_terms(haystack)
            if not surfaces:
                continue
            annotations.append(
                TerminologyAnnotation(
                    element_id=element.id,
                    element_name=element.name,  # unchanged, for audit
                    mappings=[self.resolve(s) for s in surfaces],
                )
            )
        return annotations
