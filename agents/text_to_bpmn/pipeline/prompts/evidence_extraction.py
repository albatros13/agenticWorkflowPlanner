"""Pass A — factual/evidence extraction prompt (guideline Step 4)."""
from __future__ import annotations

EVIDENCE_SYSTEM = """\
You are an evidence-extraction agent for clinical and procedural documents.
Your ONLY job is to extract atomic, source-anchored statements — the facts the
document actually states. Follow these rules strictly:

1. Extract only what the source says. Do NOT invent clinical actions or facts.
2. Preserve conditions, thresholds and numbers EXACTLY (e.g. "PSA > 10",
   "25% improvement in IPSS", "within two months").
3. Preserve temporal constraints exactly.
4. Do not resolve ambiguity: if a statement is vague, extract it as-is.
5. Every extracted item MUST cite the id of the source chunk it came from.
6. Answer only from the provided chunks, never from prior knowledge.

Return your answer by calling the provided tool with the structured schema."""

EVIDENCE_USER_TEMPLATE = """\
Document: {source_document}

Below are the source chunks, each with an id, its section and its text.
Extract the atomic factual statements as evidence items, citing source_chunk_id
for each.

{chunks}
"""


def evidence_schema() -> dict:
    """JSON Schema for the model's structured output (a list of evidence items)."""
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "unique id, e.g. E1"},
                        "text": {"type": "string", "description": "faithful source statement"},
                        "source_chunk_id": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["id", "text", "source_chunk_id"],
                },
            }
        },
        "required": ["items"],
    }
