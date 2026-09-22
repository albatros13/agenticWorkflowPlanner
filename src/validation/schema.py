"""Deterministic BPMN/DMN validation against the bundled OMG XSDs, plus a
round-trip parse and a basic graph-connectivity check (guideline Step 6 tests;
the full structural suite lands in Step 7).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from lxml import etree

from ..bpmn.namespaces import BPMN
from ..config import REPO_ROOT

BPMN_XSD = REPO_ROOT / "schemas" / "bpmn" / "BPMN20.xsd"
DMN_XSD = REPO_ROOT / "schemas" / "dmn" / "DMN13.xsd"

# BPMN flow-node local names that must be connected in the control flow.
_FLOW_NODE_TAGS = {
    "startEvent", "endEvent", "intermediateThrowEvent", "intermediateCatchEvent",
    "task", "businessRuleTask", "userTask", "serviceTask", "scriptTask", "manualTask",
    "subProcess", "callActivity",
    "exclusiveGateway", "inclusiveGateway", "parallelGateway", "eventBasedGateway",
}


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:  # allow `if result:`
        return self.ok


@lru_cache(maxsize=None)
def _schema(path: str) -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(path))


def _parse(xml: str | bytes) -> etree._Element:
    data = xml.encode("utf-8") if isinstance(xml, str) else xml
    return etree.fromstring(data)


def _validate(xml: str | bytes, xsd: Path) -> ValidationResult:
    try:
        doc = _parse(xml)
    except etree.XMLSyntaxError as exc:
        return ValidationResult(ok=False, errors=[f"XML not well-formed: {exc}"])
    schema = _schema(str(xsd))
    if schema.validate(doc):
        return ValidationResult(ok=True)
    return ValidationResult(ok=False, errors=[str(e) for e in schema.error_log])


def validate_bpmn(xml: str | bytes) -> ValidationResult:
    """Validate against BPMN 2.0 XSD (well-formedness + schema conformance)."""
    return _validate(xml, BPMN_XSD)


def validate_dmn(xml: str | bytes) -> ValidationResult:
    """Validate against DMN 1.3 XSD."""
    return _validate(xml, DMN_XSD)


def roundtrip(xml: str | bytes) -> dict[str, int]:
    """Re-parse serialized XML and return element counts (proves parse stability)."""
    doc = _parse(xml)
    counts: dict[str, int] = {}
    for el in doc.iter():
        tag = etree.QName(el).localname
        counts[tag] = counts.get(tag, 0) + 1
    return counts


def check_connectivity(xml: str | bytes) -> ValidationResult:
    """Basic graph checks: start/end present, no orphan flow nodes, every flow
    node reachable from a start and able to reach an end."""
    doc = _parse(xml)
    q = lambda tag: f"{{{BPMN}}}{tag}"  # noqa: E731

    nodes: set[str] = set()
    kinds: dict[str, str] = {}
    for el in doc.iter():
        local = etree.QName(el).localname
        if local in _FLOW_NODE_TAGS and el.get("id"):
            nodes.add(el.get("id"))
            kinds[el.get("id")] = local

    succ: dict[str, set[str]] = {n: set() for n in nodes}
    pred: dict[str, set[str]] = {n: set() for n in nodes}
    for sf in doc.iter(q("sequenceFlow")):
        s, t = sf.get("sourceRef"), sf.get("targetRef")
        if s in nodes and t in nodes:
            succ[s].add(t)
            pred[t].add(s)

    errors: list[str] = []
    starts = [n for n, k in kinds.items() if k == "startEvent"]
    ends = [n for n, k in kinds.items() if k == "endEvent"]
    if not starts:
        errors.append("no start event")
    if not ends:
        errors.append("no end event")

    def reach(seeds: list[str], graph: dict[str, set[str]]) -> set[str]:
        seen, stack = set(seeds), list(seeds)
        while stack:
            cur = stack.pop()
            for nxt in graph.get(cur, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    from_start = reach(starts, succ)
    to_end = reach(ends, pred)
    for n in nodes:
        if kinds[n] not in ("startEvent",) and not pred[n] and not succ[n]:
            errors.append(f"orphan node {n!r}")
        if n not in from_start:
            errors.append(f"node {n!r} unreachable from a start event")
        if n not in to_end:
            errors.append(f"node {n!r} cannot reach an end event")

    return ValidationResult(ok=not errors, errors=errors)
