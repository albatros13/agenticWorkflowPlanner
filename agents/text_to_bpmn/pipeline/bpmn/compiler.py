"""Deterministic IR -> BPMN 2.0 compiler (guideline Step 6).

The LLM never writes XML: this module maps the validated IR onto BPMN elements
with stable ids, explicit gateway semantics, actor lanes, a full diagram-
interchange layout, and — for thresholded decisions — a linked DMN decision.
"""
from __future__ import annotations

from dataclasses import dataclass

from lxml import etree

from ..dmn.compiler import compile_dmn
from ..ir.models import Process
from .layout import Geometry, Rect, layout
from .model import (
    BUSINESS_RULE_TASK,
    BpmnGraph,
    build_graph,
)
from .namespaces import (
    AWP,
    BPMN_NSMAP,
    XSI,
    b,
    bdi,
    dc,
    di,
    qn,
    tostring,
)

_TARGET_NS = "https://agentic-workflow-planner.org/process"


@dataclass
class CompiledModel:
    bpmn_xml: str
    dmn_xml: str | None
    graph: BpmnGraph
    geometry: Geometry


class BpmnCompiler:
    def compile(self, process: Process) -> CompiledModel:
        graph = build_graph(process)
        geo = layout(graph)
        bpmn_xml = self._render_bpmn(graph, geo)
        dmn_xml = compile_dmn(graph.decisions, model_name=graph.process_id) if graph.decisions else None
        return CompiledModel(bpmn_xml=bpmn_xml, dmn_xml=dmn_xml, graph=graph, geometry=geo)

    # -- BPMN XML ---------------------------------------------------------------
    def _render_bpmn(self, graph: BpmnGraph, geo: Geometry) -> str:
        root = etree.Element(
            b("definitions"),
            nsmap=BPMN_NSMAP,
            attrib={
                "id": f"definitions_{graph.process_id}",
                "targetNamespace": _TARGET_NS,
                "exporter": "agentic-workflow-planner",
            },
        )
        use_pool = bool(geo.pool)
        collab_id = f"Collaboration_{graph.process_id}"
        participant_id = f"Participant_{graph.process_id}"

        if use_pool:
            collab = etree.SubElement(root, b("collaboration"), attrib={"id": collab_id})
            etree.SubElement(
                collab, b("participant"),
                attrib={"id": participant_id, "name": graph.name, "processRef": graph.process_id},
            )

        proc = etree.SubElement(
            root, b("process"),
            attrib={"id": graph.process_id, "name": graph.name, "isExecutable": "false"},
        )
        if use_pool:
            self._lane_set(proc, graph, geo)
        self._flow_nodes(proc, graph)
        self._sequence_flows(proc, graph)

        self._diagram(root, graph, geo, use_pool, collab_id if use_pool else graph.process_id,
                      participant_id)
        return tostring(root)

    def _lane_set(self, proc: etree._Element, graph: BpmnGraph, geo: Geometry) -> None:
        lane_set = etree.SubElement(proc, b("laneSet"), attrib={"id": f"{graph.process_id}_lanes"})
        for name, _ in geo.lanes:
            lane = etree.SubElement(
                lane_set, b("lane"), attrib={"id": _lane_id(graph, name), "name": name}
            )
            for node_id in geo.lane_members.get(name, []):
                etree.SubElement(lane, b("flowNodeRef")).text = node_id

    def _flow_nodes(self, proc: etree._Element, graph: BpmnGraph) -> None:
        for node in graph.nodes:
            el = etree.SubElement(proc, b(node.kind), attrib={"id": node.id, "name": node.name})
            if node.kind == BUSINESS_RULE_TASK and node.dmn_ref:
                ext = etree.SubElement(el, b("extensionElements"))
                etree.SubElement(ext, qn(AWP, "decisionRef"), attrib={"decisionId": node.dmn_ref})

    def _sequence_flows(self, proc: etree._Element, graph: BpmnGraph) -> None:
        for flow in graph.flows:
            attrib = {"id": flow.id, "sourceRef": flow.source, "targetRef": flow.target}
            if flow.name:
                attrib["name"] = flow.name
            sf = etree.SubElement(proc, b("sequenceFlow"), attrib=attrib)
            if flow.condition:
                cond = etree.SubElement(sf, b("conditionExpression"))
                cond.set(qn(XSI, "type"), "bpmn:tFormalExpression")
                cond.text = flow.condition

    # -- BPMN DI ----------------------------------------------------------------
    def _diagram(self, root, graph, geo, use_pool, plane_element, participant_id) -> None:
        diagram = etree.SubElement(root, bdi("BPMNDiagram"), attrib={"id": f"{graph.process_id}_di"})
        plane = etree.SubElement(
            diagram, bdi("BPMNPlane"),
            attrib={"id": f"{graph.process_id}_plane", "bpmnElement": plane_element},
        )
        if use_pool and geo.pool:
            self._shape(plane, participant_id, geo.pool, horizontal=True)
            for name, rect in geo.lanes:
                self._shape(plane, _lane_id(graph, name), rect, horizontal=True)
        for node in graph.nodes:
            rect = geo.nodes.get(node.id)
            if rect:
                self._shape(plane, node.id, rect)
        for flow in graph.flows:
            pts = geo.edges.get(flow.id)
            if pts:
                self._edge(plane, flow.id, pts)

    def _shape(self, plane, element_id: str, rect: Rect, *, horizontal: bool = False) -> None:
        attrib = {"id": f"di_{element_id}", "bpmnElement": element_id}
        if horizontal:
            attrib["isHorizontal"] = "true"
        shape = etree.SubElement(plane, bdi("BPMNShape"), attrib=attrib)
        etree.SubElement(
            shape, dc("Bounds"),
            attrib={"x": str(rect.x), "y": str(rect.y),
                    "width": str(rect.width), "height": str(rect.height)},
        )

    def _edge(self, plane, flow_id: str, points: list[tuple[int, int]]) -> None:
        edge = etree.SubElement(
            plane, bdi("BPMNEdge"), attrib={"id": f"di_{flow_id}", "bpmnElement": flow_id}
        )
        for x, y in points:
            etree.SubElement(edge, di("waypoint"), attrib={"x": str(x), "y": str(y)})


def _lane_id(graph: BpmnGraph, name: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in name)
    return f"Lane_{graph.process_id}_{safe}"
