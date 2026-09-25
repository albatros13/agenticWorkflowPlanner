"""Guideline Step 6: deterministic IR -> BPMN compilation.

Every IR fixture compiles to schema-valid, connected BPMN with the expected
element mapping and stable ids.
"""
import pytest

from agents.text_to_bpmn.pipeline.bpmn.compiler import BpmnCompiler
from agents.text_to_bpmn.pipeline.validation.schema import check_connectivity, roundtrip, validate_bpmn

from .._ir_fixtures import and_parallel, sequential, xor_threshold

ALL = [sequential, xor_threshold, and_parallel]


@pytest.mark.parametrize("builder", ALL, ids=lambda b: b.__name__)
def test_every_fixture_compiles_to_valid_connected_bpmn(builder):
    model = BpmnCompiler().compile(builder())
    assert validate_bpmn(model.bpmn_xml).ok, validate_bpmn(model.bpmn_xml).errors
    assert check_connectivity(model.bpmn_xml).ok, check_connectivity(model.bpmn_xml).errors


@pytest.mark.parametrize("builder", ALL, ids=lambda b: b.__name__)
def test_compilation_is_deterministic(builder):
    a = BpmnCompiler().compile(builder()).bpmn_xml
    b = BpmnCompiler().compile(builder()).bpmn_xml
    assert a == b  # stable ids + layout


def test_xor_decision_maps_to_exclusive_gateway_and_business_rule_task():
    model = BpmnCompiler().compile(xor_threshold())
    kinds = {n.id: n.kind for n in model.graph.nodes}
    assert kinds["D1"] == "exclusiveGateway"
    assert kinds["D1_eval"] == "businessRuleTask"        # expanded for DMN
    assert model.dmn_xml is not None                     # DMN generated
    counts = roundtrip(model.bpmn_xml)
    assert counts.get("exclusiveGateway") == 1
    assert counts.get("businessRuleTask") == 1
    # The two branches carry condition expressions.
    assert counts.get("conditionExpression") == 2


def test_and_maps_to_parallel_gateway():
    model = BpmnCompiler().compile(and_parallel())
    kinds = {n.id: n.kind for n in model.graph.nodes}
    assert kinds["G1"] == kinds["G2"] == "parallelGateway"
    assert model.dmn_xml is None  # no thresholds -> no DMN


def test_actors_become_lanes():
    from lxml import etree

    from agents.text_to_bpmn.pipeline.bpmn.namespaces import BPMN

    model = BpmnCompiler().compile(sequential())
    counts = roundtrip(model.bpmn_xml)
    assert counts.get("participant") == 1     # a single pool
    doc = etree.fromstring(model.bpmn_xml.encode())
    lane_names = {el.get("name") for el in doc.iter(f"{{{BPMN}}}lane")}
    # Declared actors become lanes; unassigned structural nodes get their own.
    assert {"Nurse", "Physician"} <= lane_names
    assert "Unassigned" in lane_names


def test_no_orphan_nodes_and_di_present():
    model = BpmnCompiler().compile(xor_threshold())
    counts = roundtrip(model.bpmn_xml)
    # A DI shape exists for every flow node, and edges for flows.
    assert counts.get("BPMNShape", 0) >= len(model.graph.flow_nodes())
    assert counts.get("BPMNEdge", 0) == len(model.graph.flows)
    assert check_connectivity(model.bpmn_xml).ok
