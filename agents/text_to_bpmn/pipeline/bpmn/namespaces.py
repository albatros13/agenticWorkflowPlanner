"""XML namespaces and QName helpers for BPMN 2.0 / DMN 1.3 serialization."""
from __future__ import annotations

from lxml import etree

# --- BPMN 2.0 -----------------------------------------------------------------
BPMN = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI = "http://www.omg.org/spec/BPMN/20100524/DI"
DI = "http://www.omg.org/spec/DD/20100524/DI"
DC = "http://www.omg.org/spec/DD/20100524/DC"
XSI = "http://www.w3.org/2001/XMLSchema-instance"

# Our own extension namespace, used only inside <extensionElements> (schema-valid
# via the ##other wildcard) to link a businessRuleTask to its DMN decision.
AWP = "https://agentic-workflow-planner.org/ext"

BPMN_NSMAP = {
    "bpmn": BPMN,
    "bpmndi": BPMNDI,
    "di": DI,
    "dc": DC,
    "xsi": XSI,
    "awp": AWP,
}

# --- DMN 1.3 ------------------------------------------------------------------
DMN = "https://www.omg.org/spec/DMN/20191111/MODEL/"
DMN_NSMAP = {"dmn": DMN}

# Target namespace we stamp onto generated DMN definitions (required attribute).
DMN_TARGET_NS = "https://agentic-workflow-planner.org/dmn"


def qn(ns: str, tag: str) -> str:
    """Return a Clark-notation qualified name, e.g. ``{ns}tag``."""
    return f"{{{ns}}}{tag}"


def b(tag: str) -> str:
    return qn(BPMN, tag)


def bdi(tag: str) -> str:
    return qn(BPMNDI, tag)


def di(tag: str) -> str:
    return qn(DI, tag)


def dc(tag: str) -> str:
    return qn(DC, tag)


def d(tag: str) -> str:
    return qn(DMN, tag)


def tostring(root: etree._Element) -> str:
    return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8").decode(
        "utf-8"
    )
