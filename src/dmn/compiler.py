"""Deterministic DMN 1.3 compiler.

Turns the :class:`~src.bpmn.model.DecisionSpec` objects produced during BPMN
lowering into a DMN ``<definitions>`` document containing one decision table per
thresholded decision. Threshold logic is expressed as FEEL unary tests so the
table is executable by a DMN engine (guideline Step 6; enables Phase 12
simulation).
"""
from __future__ import annotations

from lxml import etree

from ..bpmn.model import DecisionSpec
from ..bpmn.namespaces import DMN, DMN_NSMAP, DMN_TARGET_NS, d, tostring


def _feel_string(label: str) -> str:
    """Quote a branch label as a FEEL string literal for an output entry."""
    escaped = label.replace('"', '\\"')
    return f'"{escaped}"'


def compile_dmn(decisions: list[DecisionSpec], *, model_name: str = "decisions") -> str:
    root = etree.Element(
        d("definitions"),
        nsmap=DMN_NSMAP,
        attrib={
            "id": "definitions_" + model_name,
            "name": model_name,
            "namespace": DMN_TARGET_NS,
            "exporter": "agentic-workflow-planner",
        },
    )
    for spec in decisions:
        _decision(root, spec)
    return tostring(root)


def _decision(root: etree._Element, spec: DecisionSpec) -> None:
    decision = etree.SubElement(root, d("decision"), attrib={"id": spec.id, "name": spec.name})
    table = etree.SubElement(
        decision, d("decisionTable"), attrib={"id": spec.id + "_table", "hitPolicy": "FIRST"}
    )
    for i, measure in enumerate(spec.inputs):
        inp = etree.SubElement(
            table, d("input"), attrib={"id": f"{spec.id}_in{i}", "label": measure}
        )
        expr = etree.SubElement(
            inp, d("inputExpression"), attrib={"id": f"{spec.id}_ie{i}", "typeRef": "number"}
        )
        etree.SubElement(expr, d("text")).text = measure
    etree.SubElement(
        table, d("output"),
        attrib={"id": f"{spec.id}_out", "name": "result", "typeRef": "string"},
    )

    for r, rule in enumerate(spec.rules):
        rule_el = etree.SubElement(table, d("rule"), attrib={"id": f"{spec.id}_r{r}"})
        for i, measure in enumerate(spec.inputs):
            entry = etree.SubElement(rule_el, d("inputEntry"), attrib={"id": f"{spec.id}_r{r}_i{i}"})
            etree.SubElement(entry, d("text")).text = rule.inputs.get(measure, "-")
        out_entry = etree.SubElement(rule_el, d("outputEntry"), attrib={"id": f"{spec.id}_r{r}_o"})
        etree.SubElement(out_entry, d("text")).text = _feel_string(rule.output)
