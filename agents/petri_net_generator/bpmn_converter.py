"""BPMN 2.0 XML -> accepting Petri net, via PM4Py's BPMN importer and converter.

PM4Py's converter only understands start/end events, tasks, sequence flows and exclusive /
parallel / inclusive gateways; it *silently ignores* every other flow node, which would leave a
net with missing paths. So before converting, this module:

1. parses the XML itself with a hardened lxml parser (no DTDs, no entity expansion, no network)
   and hands PM4Py the parsed tree — PM4Py's own file/string readers use a default parser;
2. scans every BPMN element against an allow-list and reports unsupported constructs as errors
   instead of approximating them.

PM4Py names some silent transitions with random UUIDs; :func:`_stabilize_names` renames them to
``tau<n>`` deterministically so the same BPMN always produces the same PNML IDs.
"""
from __future__ import annotations

import re

from lxml import etree
from pm4py.objects.bpmn.importer.variants.lxml import import_xml_tree_from_root
from pm4py.objects.conversion.bpmn.variants import to_petri_net
from pm4py.objects.petri_net.obj import Marking, PetriNet

from .schemas import Finding, Severity

BPMN_MODEL_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"

TASKS = {"task", "userTask", "serviceTask", "manualTask", "scriptTask", "businessRuleTask",
         "sendTask", "receiveTask"}
GATEWAYS = {"exclusiveGateway", "parallelGateway"}
# Converted by PM4Py, but its OR-join handling is a heuristic we have not verified.
APPROXIMATED = {"inclusiveGateway"}
EVENTS = {"startEvent", "endEvent"}
# Elements without control-flow semantics, or structural containers for the supported ones.
NEUTRAL = {"definitions", "process", "collaboration", "participant", "laneSet", "lane",
           "childLaneSet", "flowNodeRef", "sequenceFlow", "conditionExpression", "incoming",
           "outgoing", "documentation", "extensionElements", "textAnnotation", "text",
           "association", "dataObject", "dataObjectReference", "dataStoreReference",
           "dataStore", "dataInputAssociation", "dataOutputAssociation", "sourceRef",
           "targetRef", "property", "ioSpecification", "dataInput", "dataOutput", "inputSet",
           "outputSet", "dataInputRefs", "dataOutputRefs", "group", "category",
           "categoryValue", "itemDefinition", "signal", "message", "error", "escalation",
           "resource", "potentialOwner", "performer", "humanPerformer", "resourceAssignmentExpression",
           "expression", "formalExpression"}
# Event definitions only change how a start/end event is *triggered*; control flow is unchanged.
BENIGN_EVENT_DEFINITIONS = {"messageEventDefinition", "timerEventDefinition",
                            "signalEventDefinition", "conditionalEventDefinition",
                            "escalationEventDefinition", "errorEventDefinition"}
SUPPORTED = TASKS | GATEWAYS | APPROXIMATED | EVENTS | NEUTRAL | BENIGN_EVENT_DEFINITIONS

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class BpmnConversionError(ValueError):
    """The BPMN input cannot be converted faithfully; ``findings`` say why."""

    def __init__(self, findings: list[Finding]):
        super().__init__("; ".join(f.message for f in findings))
        self.findings = findings


def _local(tag) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def parse_bpmn_secure(bpmn_xml: str, *, max_bytes: int) -> etree._Element:
    data = bpmn_xml.encode("utf-8")
    if len(data) > max_bytes:
        raise BpmnConversionError([Finding(
            severity=Severity.error, code="input_too_large",
            message=f"BPMN XML is {len(data)} bytes (limit {max_bytes}).")])
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                             dtd_validation=False, huge_tree=False, remove_comments=True,
                             remove_pis=True)
    try:
        tree = etree.fromstring(data, parser=parser).getroottree()
    except etree.XMLSyntaxError as exc:
        raise BpmnConversionError([Finding(
            severity=Severity.error, code="malformed_xml",
            message=f"BPMN input is not well-formed XML (line {exc.lineno}).")]) from None
    if tree.docinfo.doctype or tree.docinfo.internalDTD is not None:
        raise BpmnConversionError([Finding(
            severity=Severity.error, code="dtd_not_allowed",
            message="DOCTYPE declarations are not allowed in BPMN input.")])
    root = tree.getroot()
    if root.tag != f"{{{BPMN_MODEL_NS}}}definitions":
        raise BpmnConversionError([Finding(
            severity=Severity.error, code="not_bpmn",
            message="Root element must be bpmn:definitions in the BPMN 2.0 MODEL namespace.")])
    return root


def scan_bpmn(root: etree._Element) -> list[Finding]:
    """Classify every BPMN element; errors mean the net would not be semantically faithful."""
    findings: list[Finding] = []
    unsupported: dict[str, list[str]] = {}
    approximated: list[str] = []
    triggers: list[str] = []
    counts = {"process": 0, "startEvent": 0, "endEvent": 0}

    for el in root.iter():
        if not isinstance(el.tag, str) or etree.QName(el).namespace != BPMN_MODEL_NS:
            continue  # DI, extension namespaces, comments
        name = _local(el.tag)
        if name in counts:
            counts[name] += 1
        parent = el.getparent()
        if name in BENIGN_EVENT_DEFINITIONS and parent is not None:
            triggers.append(parent.get("id", "?"))
        if name in APPROXIMATED:
            approximated.append(el.get("id", "?"))
        if name not in SUPPORTED:
            unsupported.setdefault(name, []).append(el.get("id", "?"))

    for name, ids in sorted(unsupported.items()):
        findings.append(Finding(
            severity=Severity.error, code="unsupported_bpmn_element",
            message=f"BPMN element <{name}> is not supported by the Petri net conversion "
                    f"and would be dropped.", element_ids=sorted(ids)))
    if approximated:
        findings.append(Finding(
            severity=Severity.warning, code="inclusive_gateway_approximated",
            message="Inclusive (OR) gateways are converted with PM4Py's heuristic; their "
                    "synchronization semantics have not been verified.", element_ids=sorted(approximated)))
    if triggers:
        findings.append(Finding(
            severity=Severity.info, code="event_trigger_ignored",
            message="Event triggers (message/timer/...) on start/end events are not modelled; "
                    "the events are treated as plain start/end.", element_ids=sorted(triggers)))
    if counts["process"] > 1:
        findings.append(Finding(
            severity=Severity.error, code="multiple_processes",
            message=f"Found {counts['process']} processes; only a single process is supported."))
    if counts["startEvent"] == 0:
        findings.append(Finding(severity=Severity.error, code="missing_start_event",
                                message="The process has no start event."))
    if counts["endEvent"] == 0:
        findings.append(Finding(severity=Severity.error, code="missing_end_event",
                                message="The process has no end event."))
    return findings


def _stabilize_names(net: PetriNet) -> None:
    randoms = [t for t in net.transitions if _UUID.match(t.name)]

    def key(t: PetriNet.Transition) -> tuple:
        return (sorted(a.source.name for a in t.in_arcs), sorted(a.target.name for a in t.out_arcs))

    for i, t in enumerate(sorted(randoms, key=key), start=1):
        t.name = f"tau{i}"


def bpmn_to_petri_net(
    bpmn_xml: str, *, max_bytes: int, name: str | None = None
) -> tuple[PetriNet, Marking, Marking, list[Finding], str]:
    """Return ``(net, im, fm, findings, process_name)``; raise :class:`BpmnConversionError` on errors."""
    root = parse_bpmn_secure(bpmn_xml, max_bytes=max_bytes)
    findings = scan_bpmn(root)
    errors = [f for f in findings if f.severity == Severity.error]
    if errors:
        raise BpmnConversionError(errors)

    process = root.find(f"{{{BPMN_MODEL_NS}}}process")
    process_name = name or (process.get("name") or process.get("id") or "process")

    graph = import_xml_tree_from_root(root)
    net, im, fm = to_petri_net.apply(graph)
    net.name = process_name
    _stabilize_names(net)
    return net, im, fm, findings, process_name
