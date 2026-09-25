"""Pass B: interpret evidence into the structured process IR.

The LLM proposes elements/relations; this module constructs the Pydantic IR,
which enforces the structural rules (valid types, explicit gateway semantics,
valid relation endpoints). It additionally rejects any element that references
an evidence id that does not exist, and any meaningful element left with no
evidence unless it is properly marked DERIVED (guideline Step 4 validation).
"""
from __future__ import annotations

from ..config import Settings
from ..evidence.models import EvidenceSet
from ..ir.models import Element, Process, Relation
from ..llm.base import LLMProvider
from ..prompts import LOGIC_SYSTEM, LOGIC_USER_TEMPLATE, logic_schema


class LogicInterpretationError(RuntimeError):
    pass


_GATEWAY_TYPES = {"Decision", "Gateway"}


def _normalize_gateways(elements: list[dict], relations: list[dict]) -> list[dict]:
    """Infer a missing ``gateway_kind`` from branch relations before validation.

    Models sometimes omit the split semantics on a Decision/Gateway. Rather than
    fail the whole run, we deterministically infer it (parallel branches -> AND,
    otherwise exclusive XOR) — a structural default that adds no clinical content.
    """
    for el in elements:
        if el.get("type") in _GATEWAY_TYPES and not el.get("gateway_kind"):
            outgoing = [r for r in relations if r.get("source") == el.get("id")]
            if outgoing and all(r.get("type") == "parallel" for r in outgoing):
                el["gateway_kind"] = "AND"
            else:
                el["gateway_kind"] = "XOR"
    return elements


class ProcessLogicInterpreter:
    task = "logic_interpretation"

    def __init__(self, provider: LLMProvider, settings: Settings | None = None):
        self.provider = provider
        self.settings = settings or Settings.from_env()

    def interpret(self, evidence: EvidenceSet, *, process_name: str = "") -> Process:
        rendered = "\n".join(f"{i.id} — {i.text}" for i in evidence.items)
        prompt = LOGIC_USER_TEMPLATE.format(
            process_name=process_name or evidence.document_id, evidence=rendered
        )
        result = self.provider.generate_json(
            task=self.task,
            system=LOGIC_SYSTEM,
            prompt=prompt,
            schema=logic_schema(),
        )
        data = result.data
        raw_elements = _normalize_gateways(data.get("elements", []), data.get("relations", []))

        valid_evidence = evidence.ids()
        elements = [Element.model_validate(e) for e in raw_elements]
        element_ids = {e.id for e in elements}

        # Provenance check: evidence_refs must point at real evidence items.
        for el in elements:
            unknown = set(el.evidence_refs) - valid_evidence
            if unknown:
                raise LogicInterpretationError(
                    f"Element {el.id!r} references unknown evidence ids {sorted(unknown)}"
                )
            # derived_from may reference evidence OR other elements
            unknown_src = set(el.derived_from) - valid_evidence - element_ids
            if unknown_src:
                raise LogicInterpretationError(
                    f"Element {el.id!r} derived_from references unknown ids {sorted(unknown_src)}"
                )

        relations = [Relation.model_validate(r) for r in data.get("relations", [])]

        process = Process(
            id=evidence.document_id,
            name=data.get("name") or process_name or evidence.document_id,
            description=data.get("description"),
            actors=data.get("actors", []),
            elements=elements,
            relations=relations,
            evidence_ids=sorted(valid_evidence),
            uncertainties=data.get("uncertainties", []),
        )
        return process
