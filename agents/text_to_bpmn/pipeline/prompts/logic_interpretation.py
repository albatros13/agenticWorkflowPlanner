"""Pass B — process/logic interpretation prompt (guideline Step 4).

Kept strictly separate from Pass A: one prompt does not both read the document
and invent process structure.
"""
from __future__ import annotations

LOGIC_SYSTEM = """\
You are a process-logic agent. You are given EVIDENCE ITEMS already extracted
from a source document. Interpret them into a structured process model
(elements + relations). Rules:

1. Build the process ONLY from the supplied evidence. Do not add clinical steps
   that no evidence supports.
2. Attach evidence_refs (evidence item ids) to every element you create.
3. If you must introduce a structural/derived element not stated verbatim, mark
   its status as DERIVED and list the evidence/element ids in derived_from.
4. Make gateway semantics explicit: use gateway_kind XOR for mutually exclusive
   choices, AND for concurrent branches, OR for inclusive branches.
5. Preserve thresholds and temporal constraints exactly as structured fields.
6. If the source is genuinely ambiguous about a decision criterion, create the
   element with status AMBIGUOUS instead of guessing a criterion.
7. Use element types: Start, End, Activity, Decision, Gateway, Event,
   Subprocess, DataObject, Message. Relation types: sequence, parallel,
   conditional, message, data, dependency. Conditional relations must carry a
   condition string.

Return your answer by calling the provided tool with the structured schema."""

LOGIC_USER_TEMPLATE = """\
Process name hint: {process_name}

Evidence items (id — text):
{evidence}

Produce the process elements and relations.
"""


def logic_schema() -> dict:
    element = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "type": {
                "type": "string",
                "enum": [
                    "Start", "End", "Activity", "Decision", "Gateway",
                    "Event", "Subprocess", "DataObject", "Message",
                ],
            },
            "name": {"type": "string"},
            "description": {"type": "string"},
            "actor": {"type": "string"},
            "conditions": {"type": "array", "items": {"type": "string"}},
            "gateway_kind": {"type": "string", "enum": ["AND", "OR", "XOR"]},
            "is_exception": {"type": "boolean"},
            "thresholds": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "measure": {"type": "string"},
                        "operator": {"type": "string"},
                        "value": {"type": "number"},
                        "value_high": {"type": "number"},
                        "unit": {"type": "string"},
                    },
                    "required": ["measure", "operator", "value"],
                },
            },
            "temporal_constraints": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string"},
                        "value": {"type": "number"},
                        "unit": {"type": "string"},
                        "reference": {"type": "string"},
                    },
                    "required": ["kind", "value", "unit"],
                },
            },
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
            "derived_from": {"type": "array", "items": {"type": "string"}},
            "status": {
                "type": "string",
                "enum": ["EXPLICIT", "DERIVED", "INFERRED", "AMBIGUOUS", "MISSING"],
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["id", "type", "name"],
    }
    relation = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "type": {
                "type": "string",
                "enum": ["sequence", "parallel", "conditional", "message", "data", "dependency"],
            },
            "source": {"type": "string"},
            "target": {"type": "string"},
            "condition": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
            "status": {
                "type": "string",
                "enum": ["EXPLICIT", "DERIVED", "INFERRED", "AMBIGUOUS", "MISSING"],
            },
        },
        "required": ["id", "type", "source", "target"],
    }
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "actors": {"type": "array", "items": {"type": "string"}},
            "elements": {"type": "array", "items": element},
            "relations": {"type": "array", "items": relation},
            "uncertainties": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["elements", "relations"],
    }
