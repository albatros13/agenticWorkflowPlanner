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


def select_provider(settings: Settings) -> LLMProvider:
    """Pick a real (api-backed) provider from configuration.

    Resolution order: an explicit ``LLM_PROVIDER`` env override, then the configured
    ``settings.llm_provider``, then auto-detection from whichever API key is present.
    ``mock`` is never auto-resolved (tests inject :class:`ScriptedLLMProvider` directly).
    """
    name = (os.getenv("LLM_PROVIDER") or settings.llm_provider or "").lower()
    if name in ("anthropic", "openai", "auto"):
        return get_provider(settings, provider=name)
    # Unset or "mock": fall back to key-based auto-detection for real calls.
    return get_provider(settings, provider="auto")


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
                    f"{t.measure} {t.operator} {t.value}{t.value_high and ('..' + str(t.value_high)) or ''}"
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
