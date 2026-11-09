"""Provider selection + response shaping for the text->BPMN agent.

Lifted verbatim (behaviour-preserving) from the original demo API so both the demo
(``agents/text_to_bpmn/demo/main.py``) and the agent produce byte-identical resource payloads.
Keep this the single source of truth; the demo now delegates here.
"""
from __future__ import annotations

import os

from .pipeline.config import Settings
from .pipeline.llm.base import LLMProvider
from .pipeline.llm.factory import get_provider
from .pipeline.validation.schema import check_connectivity, validate_bpmn, validate_dmn


def select_provider(settings: Settings) -> LLMProvider:
    """Pick a real (api-backed) provider from configuration.

    Resolution order: an explicit ``LLM_PROVIDER`` env override, then the configured
    ``settings.llm_provider``; anything other than ``anthropic`` / ``openai`` (unset, ``auto``,
    ``mock``) auto-detects from whichever API key is present. ``mock`` is never auto-resolved
    (tests inject :class:`ScriptedLLMProvider` directly).
    """
    name = (os.getenv("LLM_PROVIDER") or settings.llm_provider or "").lower()
    return get_provider(settings, provider=name if name in ("anthropic", "openai") else "auto")


def default_provider() -> LLMProvider:
    """The provider every agent uses when none is injected: :func:`select_provider` on the
    environment (``.env`` included)."""
    return select_provider(Settings.from_env())


def build_validation(bpmn_xml: str, dmn_xml: str | None) -> dict:
    """Schema + connectivity checks of a generated model, in the agent's output shape."""
    vb, cc = validate_bpmn(bpmn_xml), check_connectivity(bpmn_xml)
    vd = validate_dmn(dmn_xml) if dmn_xml else None
    return {
        "bpmn_valid": vb.ok,
        "bpmn_errors": vb.errors[:5],
        "dmn_valid": vd.ok if vd else None,
        "connectivity_ok": cc.ok,
        "connectivity_errors": cc.errors[:5],
    }


def build_resources(evidence, process, annotations, model) -> dict:
    """Shape extracted artifacts for display (identical to the original demo payload)."""
    return {
        "actors": process.actors,
        "evidence": [
            {"id": e.id, "text": e.text, "section": e.section, "page": e.page}
            for e in evidence.items
        ],
        "elements": [
            {
                "id": el.id,
                "type": el.type.value,
                "name": el.name,
                "actor": el.actor,
                "status": el.status.value,
                "gateway_kind": el.gateway_kind.value if el.gateway_kind else None,
                "thresholds": [
                    f"{t.measure} {t.operator} {t.value}{'' if t.value_high is None else '..' + str(t.value_high)}"
                    f"{(' ' + t.unit) if t.unit else ''}"
                    for t in el.thresholds
                ],
                "temporal": [
                    f"{t.kind} {t.value} {t.unit}{(' ' + t.reference) if t.reference else ''}"
                    for t in el.temporal_constraints
                ],
                "evidence_refs": el.evidence_refs,
            }
            for el in process.elements
        ],
        "terminology": [
            {
                "element_name": a.element_name,
                "mappings": [
                    {
                        "surface_form": m.surface_form,
                        "normalized_label": m.normalized_label,
                        "ontology": m.ontology,
                        "concept_id": m.concept_id,
                        "match_type": m.match_type.value,
                        "confidence": m.confidence,
                    }
                    for m in a.mappings
                ],
            }
            for a in annotations
        ],
        "decisions": [
            {
                "id": d.id,
                "name": d.name,
                "inputs": d.inputs,
                "rules": [{"inputs": r.inputs, "output": r.output} for r in d.rules],
            }
            for d in model.graph.decisions
        ],
        "counts": {
            "evidence": len(evidence.items),
            "elements": len(process.elements),
            "relations": len(process.relations),
            "nodes": len(model.graph.nodes),
            "flows": len(model.graph.flows),
        },
    }
