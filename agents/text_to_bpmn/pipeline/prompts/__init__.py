"""Versioned prompts, kept out of business logic (guideline Step 1).

Prompt text lives here and is keyed by ``config.PROMPT_VERSION`` in audit
artifacts. Each prompt is paired with the JSON Schema the model must satisfy;
the schemas are derived from the Pydantic IR/evidence models so prompt and code
cannot drift apart.
"""
from .evidence_extraction import EVIDENCE_SYSTEM, EVIDENCE_USER_TEMPLATE, evidence_schema
from .logic_interpretation import LOGIC_SYSTEM, LOGIC_USER_TEMPLATE, logic_schema

__all__ = [
    "EVIDENCE_SYSTEM",
    "EVIDENCE_USER_TEMPLATE",
    "evidence_schema",
    "LOGIC_SYSTEM",
    "LOGIC_USER_TEMPLATE",
    "logic_schema",
]
