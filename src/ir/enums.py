"""Controlled vocabularies for the intermediate representation."""
from __future__ import annotations

from enum import Enum


class ElementType(str, Enum):
    START = "Start"
    END = "End"
    ACTIVITY = "Activity"
    DECISION = "Decision"
    GATEWAY = "Gateway"
    EVENT = "Event"
    SUBPROCESS = "Subprocess"
    DATA_OBJECT = "DataObject"
    MESSAGE = "Message"


class GatewayKind(str, Enum):
    """Explicit split/merge semantics (guideline Step 3 "important semantic fields")."""

    AND = "AND"   # parallel
    OR = "OR"     # inclusive
    XOR = "XOR"   # exclusive


class RelationType(str, Enum):
    SEQUENCE = "sequence"
    PARALLEL = "parallel"
    CONDITIONAL = "conditional"
    MESSAGE = "message"
    DATA = "data"
    DEPENDENCY = "dependency"


class Uncertainty(str, Enum):
    """How well-supported an inferred item is. Must be represented explicitly;
    unsupported assumptions must never be recorded as EXPLICIT facts (Step 3)."""

    EXPLICIT = "EXPLICIT"      # stated verbatim in the source
    DERIVED = "DERIVED"        # logically derived from other supported items
    INFERRED = "INFERRED"      # plausibly inferred, weaker than DERIVED
    AMBIGUOUS = "AMBIGUOUS"    # source is unclear; needs clarification
    MISSING = "MISSING"        # required but absent from the source


# Element types that carry clinical/process meaning and therefore require
# traceable evidence (structural nodes like Start/End/Gateway do not).
MEANINGFUL_TYPES = frozenset(
    {
        ElementType.ACTIVITY,
        ElementType.DECISION,
        ElementType.EVENT,
        ElementType.SUBPROCESS,
        ElementType.MESSAGE,
        ElementType.DATA_OBJECT,
    }
)

GATEWAY_TYPES = frozenset({ElementType.DECISION, ElementType.GATEWAY})
